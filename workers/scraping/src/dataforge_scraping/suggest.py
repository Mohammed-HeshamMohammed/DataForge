"""Opt-in draft presets (Track K). Proposals only: deterministic code always extracts, and a proposal is shown
only after it passes an evaluation against the page it came from.

Providers
- `local_heuristic` (default, offline): structured data first, then repeated-structure detection.
- `model`: an OpenAI-compatible chat endpoint. Loopback endpoints (for example a local Ollama) need no
  extra consent. Any other endpoint needs the project's remote-model consent, and page text is redacted
  (emails, phone numbers, long digit runs) before it leaves the machine. The exact payload is returned so
  the UI can show what was or would be sent.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .extraction import extract_document
from .resilience import _classes, _part, css_for

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s().-]{7,}\d)(?!\d)")
_DIGITS = re.compile(r"\d{9,}")
MAX_MODEL_CHARS = 12_000


def redact(text: str) -> str:
    return _DIGITS.sub("[number]", _PHONE.sub("[phone]", _EMAIL.sub("[email]", text)))


def page_markdown(html: str, url: str) -> str:
    from markdownify import markdownify

    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        node.decompose()
    return re.sub(r"\n{3,}", "\n\n", markdownify(str(soup.body or soup), heading_style="ATX"))[:MAX_MODEL_CHARS]


def _base_preset(url: str, name: str) -> dict:
    host = urlparse(url).hostname or "site"
    slug = re.sub(r"[^a-z0-9]+", "_", host.lower()).strip("_")[:30] or "site"
    return {
        "id": f"custom.draft.{slug}", "version": "0.1.0", "display_name": name, "category": "custom", "page_type": "draft",
        "status": "active", "owner": "local-user", "description": "Draft proposed by DataForge. Review, test 10 records, then save.",
        "policy": {"collection_basis": "public_html", "requires_user_authorization_acknowledgement": True, "robots_policy": "respect",
                   "authentication": "forbidden", "captcha_or_access_challenge": "stop", "paywall_or_rate_limit": "stop", "personal_data_classification": "unknown"},
        "request_limits": {"max_concurrency": 1, "min_delay_ms": 2000, "max_pages_default": 10, "max_records_default": 500, "max_duration_seconds": 900},
        "url_scope": {"allowed_hosts": [host], "allowed_path_patterns": []},
        "strategy": {"preferred": "http", "allowed": ["http", "webview"]},
        "pagination": {"type": "none"},
        "validation": {"minimum_record_coverage": 0.7, "unique_by": []},
        "labels": {"ai_assisted": True},
    }


def _heuristic_selectors(html: str) -> dict | None:
    """Find the most repeated sibling group with text and links; propose title/link/price/image fields."""
    soup = BeautifulSoup(html, "html.parser")
    best: tuple[int, Tag, str] | None = None
    for parent in soup.find_all(True):
        children = [c for c in parent.find_all(True, recursive=False) if c.name not in ("script", "style", "br", "option")]
        if len(children) < 3:
            continue
        signatures = Counter(_part(c, False) for c in children if _classes(c) or c.name in ("li", "article", "tr"))
        if not signatures:
            continue
        signature, count = signatures.most_common(1)[0]
        members = [c for c in children if _part(c, False) == signature]
        rich = sum(1 for m in members if m.find("a", href=True) and len(m.get_text(" ", strip=True)) > 10)
        score = rich * (2 if _classes(members[0]) else 1)
        if count >= 3 and rich >= 3 and (best is None or score > best[0]):
            best = (score, members[0], signature)
    if best is None:
        return None
    _, sample, signature = best
    fields = []
    heading = sample.find(["h1", "h2", "h3", "h4"]) or sample.find("a", href=True)
    if heading is not None:
        fields.append({"key": "title", "type": "string", "required": True, "selectors": [{"css": css_for(heading, sample)}], "transforms": ["trim", "collapse_whitespace"]})
    link = sample.find("a", href=True)
    if link is not None:
        fields.append({"key": "link", "type": "url", "selectors": [{"css": css_for(link, sample) + "::attr(href)"}], "transforms": ["to_absolute_url"]})
    price = sample.find(string=re.compile(r"[$£€¥]\s?\d"))
    if price is not None and isinstance(price.parent, Tag):
        fields.append({"key": "price", "type": "decimal", "selectors": [{"css": css_for(price.parent, sample)}], "transforms": ["parse_price"]})
    image = sample.find("img", src=True)
    if image is not None:
        fields.append({"key": "image", "type": "url", "selectors": [{"css": css_for(image, sample) + "::attr(src)"}], "transforms": ["to_absolute_url"]})
    return {"record_root": {"css": signature}, "fields": fields}


def propose_local(html: str, url: str) -> list[dict]:
    from .structured import detect

    proposals = []
    found = detect(html, url)
    if found["mapped_types"]:
        preset = _base_preset(url, f"{found['suggested_type']} structured data")
        preset["extraction"] = {"mode": "structured_data", "schema_types": found["mapped_types"], "fields": []}
        proposals.append({"source": "structured_data", "preset": preset, "detected": found})
    selectors = _heuristic_selectors(html)
    if selectors:
        preset = _base_preset(url, "Repeated items")
        preset["extraction"] = selectors
        link = next((f for f in selectors["fields"] if f["key"] == "link"), None)
        preset["validation"]["unique_by"] = ["link"] if link else []
        proposals.append({"source": "repeated_structure", "preset": preset})
    return proposals


def model_payload(html: str, url: str, remote: bool) -> dict:
    markdown = page_markdown(html, url)
    text = redact(markdown) if remote else markdown
    outline = []
    soup = BeautifulSoup(html, "html.parser")
    for node in soup.find_all(True, class_=True)[:400]:
        outline.append(_part(node, False))
    prompt = (
        "You propose CSS extraction rules for a web data tool. Reply with JSON only: "
        '{"record_root": {"css": "..."}, "fields": [{"key": "...", "type": "string|url|decimal|integer", "selectors": [{"css": "...::text or ::attr(name)"}]}]}. '
        "Use only selectors that exist in the outline."
    )
    return {"messages": [{"role": "system", "content": prompt},
                         {"role": "user", "content": f"URL path: {urlparse(url).path}\n\nClass outline:\n{' '.join(dict.fromkeys(outline))[:3000]}\n\nPage text:\n{text}"}],
            "temperature": 0}


def propose_with_model(html: str, url: str, endpoint: str, model: str, consent_remote: bool, client) -> dict:
    parsed = urlparse(endpoint)
    loopback = parsed.hostname in ("127.0.0.1", "localhost", "::1")
    if not loopback and not consent_remote:
        raise PermissionError("Sending page content to a remote model needs this project's consent (Settings → AI suggestions)")
    if not loopback and parsed.scheme != "https":
        raise PermissionError("Remote model endpoints must use HTTPS")
    payload = {**model_payload(html, url, remote=not loopback), "model": model}
    response = client.post(endpoint.rstrip("/") + "/chat/completions", json=payload, timeout=120)
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", content, re.S)
    if not match:
        raise ValueError("The model did not return JSON")
    rules = json.loads(match.group(0))
    preset = _base_preset(url, "Model-suggested items")
    preset["extraction"] = {"record_root": rules.get("record_root") or {}, "fields": [
        {"key": re.sub(r"[^a-z0-9_]", "_", str(f.get("key", "field")).lower())[:40] or "field", "type": f.get("type", "string") if f.get("type") in ("string", "url", "decimal", "integer") else "string",
         "selectors": [{"css": str(s.get("css", ""))} for s in f.get("selectors", []) if isinstance(s, dict)][:3], "transforms": ["trim"]}
        for f in rules.get("fields", [])[:20] if isinstance(f, dict)]}
    return {"source": "model", "preset": preset, "sent": payload, "endpoint_is_local": loopback}


def evaluate(proposal: dict, html: str, url: str) -> dict:
    """Run the proposal's deterministic extraction on the page. Only proposals meeting the coverage bar are surfaced."""
    preset = proposal["preset"]
    try:
        records, rejected, warnings = extract_document(html, url, preset)
    except Exception as error:  # noqa: BLE001 - an invalid proposal is simply not surfaced
        return {"passed": False, "records": 0, "reason": f"{type(error).__name__}: {error}"}
    fields = [f["key"] for f in (preset.get("extraction") or {}).get("fields", [])]
    coverage = {key: round(sum(1 for r in records if r.get(key) not in (None, "")) / len(records), 3) for key in fields} if records else {}
    minimum = float(preset["validation"].get("minimum_record_coverage", 0.7))
    required = [f["key"] for f in (preset.get("extraction") or {}).get("fields", []) if f.get("required")]
    passed = len(records) >= 2 and all(coverage.get(key, 0) >= minimum for key in required) if fields else len(records) >= 1
    return {"passed": passed, "records": len(records), "rejected": len(rejected), "field_coverage": coverage, "sample": records[:3], "warnings": warnings[:5]}


def propose(html: str, url: str, provider: str = "local_heuristic", endpoint: str | None = None, model: str | None = None, consent_remote: bool = False, client=None) -> list[dict]:
    proposals = propose_local(html, url)
    if provider == "model":
        if not endpoint or not model or client is None:
            raise ValueError("A model endpoint and model name are required")
        proposals.append(propose_with_model(html, url, endpoint, model, consent_remote, client))
    surfaced = []
    for proposal in proposals:
        proposal["evaluation"] = evaluate(proposal, html, url)
        if proposal["evaluation"]["passed"]:
            surfaced.append(proposal)
    return surfaced
