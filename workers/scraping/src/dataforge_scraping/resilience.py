"""Preset maintenance: field fingerprints, adaptive relocation suggestions (the Scrapling technique),
example-based selector suggestions (autoscraper), and coverage drift.

Everything here only *suggests*. A person reviews a suggestion and saves a new preset version; fixes are
never applied silently.
"""

from __future__ import annotations

import re
from collections import Counter

from bs4 import BeautifulSoup, Tag

_VALUE_SHAPE = (("currency", re.compile(r"^[£$€¥₹]\s?\d|\d\s?(usd|eur|gbp)$", re.I)), ("number", re.compile(r"^[\d.,\s%]+$")),
                ("date", re.compile(r"\d{4}-\d{2}-\d{2}|\b\d{1,2}/\d{1,2}/\d{2,4}\b")), ("url", re.compile(r"^https?://")))


def _shape(text: str) -> str:
    text = text.strip()
    return next((name for name, pattern in _VALUE_SHAPE if pattern.search(text)), "text" if text else "empty")


def _classes(node: Tag) -> list[str]:
    value = node.get("class") or []
    return [c for c in (value if isinstance(value, list) else str(value).split()) if c]


def _path(node: Tag, depth: int = 3) -> list[str]:
    parts, current = [], node.parent
    while isinstance(current, Tag) and current.name not in ("[document]", "html", "body") and len(parts) < depth:
        parts.append(current.name + "".join(f".{c}" for c in _classes(current)[:2]))
        current = current.parent
    return parts


def fingerprint(node: Tag, attribute: str | None = None) -> dict:
    text = node.get(attribute) if attribute else node.get_text(" ", strip=True)
    return {
        "tag": node.name, "classes": _classes(node), "id": node.get("id"), "attributes": sorted(k for k in node.attrs if k not in ("class", "id", "style")),
        "path": _path(node), "shape": _shape(str(text or "")), "text_length": min(len(str(text or "")), 500),
    }


def _similarity(a: dict, b: dict) -> float:
    def jaccard(x: list, y: list) -> float:
        x, y = set(x), set(y)
        return len(x & y) / len(x | y) if x | y else 1.0

    score = 2.0 * (a["tag"] == b["tag"]) + 3.0 * jaccard(a["classes"], b["classes"]) + 1.5 * (bool(a["id"]) and a["id"] == b["id"])
    score += 1.0 * jaccard(a["attributes"], b["attributes"]) + 2.0 * jaccard(a["path"], b["path"]) + 1.0 * (a["shape"] == b["shape"])
    ratio = min(a["text_length"], b["text_length"]) / max(a["text_length"], b["text_length"], 1)
    return round((score + 0.5 * ratio) / 11.0, 4)


def _split_css(css: str) -> tuple[str, str | None]:
    match = re.search(r"::(text|attr\(([\w-]+)\))\s*$", css)
    return (css[: match.start()], match.group(2)) if match else (css, None)


def _part(node: Tag, positional: bool) -> str:
    classes = [c for c in _classes(node) if re.match(r"^[A-Za-z_][\w-]*$", c)][:2]
    part = node.name + "".join(f".{c}" for c in classes)
    parent = node.parent
    if positional and isinstance(parent, Tag):
        same = parent.find_all(node.name, recursive=False)
        if len(same) > 1:
            part += f":nth-of-type({same.index(node) + 1})"
    return part


def css_for(node: Tag, root: Tag | None = None) -> str:
    """A short CSS selector whose first match (inside root when given) is node. Prefers ids and classes;
    adds :nth-of-type only when needed."""
    scope = root if root is not None else next((p for p in node.parents if p.name == "[document]"), node)
    if root is None and node.get("id") and re.match(r"^[A-Za-z][\w-]*$", str(node["id"])):
        return f"#{node['id']}"
    chain: list[Tag] = []
    current = node
    while isinstance(current, Tag) and current is not root and current.name not in ("[document]", "html", "body") and len(chain) < 5:
        chain.append(current)
        current = current.parent
    for positional in (False, True):
        for length in range(1, len(chain) + 1):
            selector = " ".join(_part(n, positional) for n in reversed(chain[:length]))
            try:
                if scope.select_one(selector) is node:
                    return selector
            except Exception:  # noqa: BLE001 - odd class names
                continue
    return " > ".join(_part(n, True) for n in reversed(chain))


def field_fingerprints(html: str, preset: dict) -> dict[str, dict]:
    """Fingerprint of each field's first match inside the first record root (stored with health checks)."""
    soup = BeautifulSoup(html, "html.parser")
    extraction = preset.get("extraction") or {}
    root_css = (extraction.get("record_root") or {}).get("css")
    root = soup.select_one(root_css) if root_css else soup
    prints: dict[str, dict] = {"__root__": fingerprint(root)} if root is not None and root_css else {}
    if root is None:
        return prints
    for field in extraction.get("fields", []):
        for selector in field.get("selectors", []):
            css, attribute = _split_css(str(selector.get("css", "")))
            node = root.select_one(css) if css.strip() else root
            if node is not None:
                prints[field["key"]] = fingerprint(node, attribute or selector.get("attribute"))
                break
    return prints


def relocation_suggestions(html: str, preset: dict, prints: dict[str, dict], threshold: float = 0.55) -> list[dict]:
    """For fields (or the record root) whose selectors no longer match, propose the most similar element."""
    soup = BeautifulSoup(html, "html.parser")
    extraction = preset.get("extraction") or {}
    root_css = (extraction.get("record_root") or {}).get("css")
    suggestions: list[dict] = []
    roots = soup.select(root_css) if root_css else [soup]
    candidates = [node for node in soup.find_all(True) if node.name not in ("script", "style", "head", "meta", "link")]
    if root_css and not roots and "__root__" in prints:
        # A record root repeats: group elements by tag+class signature and score each repeating group.
        groups: dict[str, list[float]] = {}
        for node in candidates:
            groups.setdefault(_part(node, False), []).append(_similarity(prints["__root__"], fingerprint(node)))
        best = max(((css, sum(scores) / len(scores), len(scores)) for css, scores in groups.items() if len(scores) >= 2), key=lambda item: item[1], default=None)
        if best and best[1] >= threshold:
            suggestions.append({"field": "__root__", "current": root_css, "suggested": best[0], "score": round(best[1], 3), "matches": best[2]})
            roots = soup.select(best[0])
    if not roots:
        return suggestions
    root = roots[0]
    for field in extraction.get("fields", []):
        if field["key"] not in prints:
            continue
        matched = False
        for selector in field.get("selectors", []):
            css, _ = _split_css(str(selector.get("css", "")))
            if (root.select_one(css) if css.strip() else root) is not None:
                matched = True
                break
        if matched:
            continue
        attribute = _split_css(str((field.get("selectors") or [{}])[0].get("css", "")))[1]
        best_node, best_score = None, 0.0
        for node in root.find_all(True):
            score = _similarity(prints[field["key"]], fingerprint(node, attribute))
            if score > best_score:
                best_node, best_score = node, score
        if best_node is not None and best_score >= threshold - 0.1:
            css = css_for(best_node, root)
            suggestions.append({"field": field["key"], "current": [s.get("css") for s in field.get("selectors", [])],
                                "suggested": css + (f"::attr({attribute})" if attribute else ""), "score": best_score,
                                "sample": (best_node.get(attribute) if attribute else best_node.get_text(" ", strip=True))[:120]})
    return suggestions


def _repeated_ancestor(node: Tag) -> Tag | None:
    """Nearest ancestor whose tag+class signature repeats among its siblings: the record container."""
    current = node.parent
    while isinstance(current, Tag) and current.name not in ("body", "html", "[document]"):
        parent = current.parent
        if isinstance(parent, Tag):
            signature = (current.name, tuple(_classes(current)))
            if sum(1 for sibling in parent.find_all(current.name, recursive=False) if (sibling.name, tuple(_classes(sibling))) == signature) >= 2:
                return current
        current = current.parent
    return None


def suggest_from_examples(html: str, url: str, examples: dict[str, str]) -> dict:
    """Example-based picking: {"title": "A Light in the Attic", "price": "£51.77"} -> record root + field selectors.
    autoscraper learns a rule per example; the rule is converted to CSS and verified against the page."""
    from autoscraper import AutoScraper

    soup = BeautifulSoup(html, "html.parser")
    fields, roots, notes = [], [], []
    for key, example in examples.items():
        example = str(example).strip()
        if not example:
            continue
        scraper = AutoScraper()
        scraper.build(html=html, url=url or "https://example.invalid/", wanted_list=[example])
        node, attribute = None, None
        for stack in scraper.stack_list:
            css = " ".join(tag + "".join(f".{c}" for c in (attrs.get("class") or [] if isinstance(attrs.get("class"), list) else str(attrs.get("class") or "").split())) for tag, attrs in stack["content"])
            attribute = stack.get("wanted_attr")
            for candidate in soup.select(css) if css else []:
                value = candidate.get(attribute) if attribute else candidate.get_text(" ", strip=True)
                if value and example in str(value):
                    node = candidate
                    break
            if node is not None:
                break
        if node is None:  # fall back to a direct text or attribute search
            node = soup.find(lambda tag: tag.get_text(" ", strip=True) == example) or soup.find(lambda tag: any(str(v) == example for v in tag.attrs.values()))
            if node is not None:
                attribute = next((k for k, v in node.attrs.items() if str(v) == example), None) if node.get_text(" ", strip=True) != example else None
        if node is None:
            notes.append(f"'{key}': example value not found on the page")
            continue
        root = _repeated_ancestor(node)
        roots.append(root)
        fields.append((key, node, attribute))
    root = Counter(r for r in roots if r is not None).most_common(1)[0][0] if any(roots) else None
    root_css = None
    if root is not None:
        root_css = root.name + "".join(f".{c}" for c in _classes(root)[:2])
    result_fields = []
    for key, node, attribute in fields:
        inside = root is not None and root in node.parents
        css = css_for(node, root if inside else None)
        css = css + (f"::attr({attribute})" if attribute else "")
        count = len(soup.select(root_css)) if root_css else 1
        result_fields.append({"key": key, "selectors": [{"css": css}], "relative_to_root": inside, "type": "url" if attribute in ("href", "src") else "string",
                              "transforms": ["to_absolute_url"] if attribute in ("href", "src") else ["trim", "collapse_whitespace"]})
    return {"record_root": {"css": root_css} if root_css else None, "fields": result_fields, "record_count": len(soup.select(root_css)) if root_css else 0, "notes": notes}


def coverage_drift(baseline: dict[str, float], current: dict[str, float], tolerance: float = 0.2) -> list[dict]:
    """Fields whose coverage dropped more than `tolerance` (absolute) below the baseline."""
    return [{"field": key, "baseline": round(base, 3), "current": round(current.get(key, 0.0), 3)}
            for key, base in baseline.items() if base - current.get(key, 0.0) > tolerance]
