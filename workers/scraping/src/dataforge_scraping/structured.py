"""Selector-free extraction: schema.org structured data (extruct + mf2py) and main article text
(trafilatura + htmldate).

Structured data survives redesigns far better than CSS selectors. When two syntaxes on one page describe
the same entity differently, both records are kept and flagged; they are never merged silently.
"""

from __future__ import annotations

import html as html_lib
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path

SYNTAXES = ("json-ld", "microdata", "rdfa", "opengraph", "microformat")
_CONTAINERS = ("mainEntity", "itemListElement", "item", "hasPart", "about", "subjectOf", "containsPlace", "subOrganization", "department", "makesOffer", "itemOffered")
_MAPPINGS_PATH = Path(__file__).with_name("data") / "schemaorg_mappings.json"
_CLOSING_TAGS = re.compile(r"</(?:body|html)\s*>", re.I)


@lru_cache(maxsize=1)
def mappings() -> dict:
    return json.loads(_MAPPINGS_PATH.read_text(encoding="utf-8"))


def _type_names(value: object) -> list[str]:
    names = value if isinstance(value, list) else [value]
    return [str(name).rsplit("/", 1)[-1].rsplit("#", 1)[-1] for name in names if name]


def _walk(node: object, syntax: str, out: list[dict]) -> None:
    """Flatten @graph containers and collect every typed object (nested ones included)."""
    if isinstance(node, list):
        for item in node:
            _walk(item, syntax, out)
        return
    if not isinstance(node, dict):
        return
    if "@graph" in node:
        _walk(node["@graph"], syntax, out)
    if node.get("@type"):
        out.append({"syntax": syntax, "types": _type_names(node["@type"]), "data": node})
    # Descend only through container properties. Values such as `publisher` or `address` describe the
    # parent entity and are read through the mappings, not emitted as records of their own.
    for key in _CONTAINERS:
        if isinstance(node.get(key), (dict, list)):
            _walk(node[key], syntax, out)


def extract_entities(html: str, url: str, syntaxes: tuple[str, ...] = SYNTAXES) -> list[dict]:
    import extruct

    # Markup injected after </html> (common with tag managers and concatenated templates) is dropped by lxml, so the
    # closing tags go and the parser closes the document at the real end instead.
    html = _CLOSING_TAGS.sub("", html)
    try:
        data = extruct.extract(html, base_url=url, syntaxes=[s for s in syntaxes if s != "rdfa"] + (["rdfa"] if "rdfa" in syntaxes else []), uniform=True, errors="ignore")
    except Exception:  # noqa: BLE001 - malformed markup must not fail the page
        return []
    entities: list[dict] = []
    for syntax in syntaxes:
        if syntax == "microformat":
            for item in data.get("microformat", []):
                types = [t.removeprefix("h-") for t in item.get("type", [])]
                properties = {k: (v[0] if isinstance(v, list) and len(v) == 1 else v) for k, v in item.get("properties", {}).items()}
                entities.append({"syntax": syntax, "types": types, "data": properties})
        elif syntax != "opengraph":
            _walk(data.get(syntax, []), syntax, entities)
    return entities


def detect(html: str, url: str) -> dict:
    """What structured data a page carries (Studio's "Use structured data" offer)."""
    entities = extract_entities(html, url)
    types = Counter(name for entity in entities for name in entity["types"] if _resolve_type(name))
    return {
        "types": dict(types.most_common()),
        "syntaxes": sorted({entity["syntax"] for entity in entities}),
        "suggested_type": types.most_common(1)[0][0] if types else None,
        "mapped_types": sorted({_resolve_type(name) for name in types}),
    }


def _resolve_type(name: str) -> str | None:
    table = mappings()["types"]
    if name in table:
        return name
    for mapped, spec in table.items():
        if name in spec.get("includes_subtypes", []):
            return mapped
    return None


def _path(data: object, path: str) -> object:
    value = data
    for part in path.split("."):
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    if isinstance(value, list):
        value = value[0] if value else None
    return _scalar(value)


def _scalar(value: object) -> object:
    if isinstance(value, dict):  # e.g. image objects, brand without name, QuantitativeValue
        value = value.get("url") or value.get("name") or value.get("@value") or value.get("value") or value.get("@id")
    return value


def _path_all(data: object, path: str, limit: int = 30) -> list:
    """Every value at `path`, following each list element (all authors, all opening hours, all ingredients)."""
    values = data if isinstance(data, list) else [data]
    for part in path.split("."):
        found = []
        for value in values:
            items = value if isinstance(value, list) else [value]
            found.extend(item.get(part) for item in items if isinstance(item, dict) and item.get(part) not in (None, "", []))
        values = found
    flat: list = []
    for value in values:
        flat.extend(value if isinstance(value, list) else [value])
    return [v for v in (_scalar(v) for v in flat) if isinstance(v, (str, int, float)) and str(v).strip()][:limit]


# Plain text allowed where schema.org expects an object ("address": "1 Main St", "author": "Jo").
_TEXT_FOR_OBJECT = ("address", "author", "brand", "publisher", "name", "venue", "manufacturer", "seller", "organizer", "hiring_organization",
                    "works_for", "provider", "creator", "director", "performer", "item_reviewed", "affiliation", "founder", "actors")
_LONG_TEXT = ("description", "article_body", "review_body", "answer", "responsibilities", "qualifications", "job_benefits", "transcript", "release_notes")
_ENUM_URL = re.compile(r"^https?://schema\.org/([A-Za-z]+)$")
MAX_TEXT = 100_000


def _paths(spec: object) -> tuple[list[str], bool, str]:
    if isinstance(spec, dict):
        return list(spec.get("paths") or []), bool(spec.get("join")), str(spec.get("separator", ", "))
    return ([spec] if isinstance(spec, str) else list(spec)), False, ", "


def _clean(field: str, value: object) -> object:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip()
    if match := _ENUM_URL.match(text):
        return match.group(1)  # https://schema.org/InStock -> InStock
    if field in _LONG_TEXT and "<" in text and ">" in text:
        text = re.sub(r"\s+([.,;:!?])", r"\1", " ".join(html_lib.unescape(re.sub(r"<[^>]+>", " ", text)).split()))
    return text[:MAX_TEXT] or None


def _field_value(data: dict, field: str, spec: object) -> object:
    paths, joined, separator = _paths(spec)
    if joined:
        values: list = []
        for path in paths:
            values.extend(v for v in (_clean(field, v) for v in _path_all(data, path)) if v not in (None, "") and v not in values)
            if values:
                break
        if not values and field in _TEXT_FOR_OBJECT and paths and "." in paths[0]:
            head = data.get(paths[0].split(".", 1)[0])
            values = [_clean(field, v) for v in (head if isinstance(head, list) else [head]) if isinstance(v, str) and v.strip()]
        return separator.join(str(v) for v in values) if values else None
    for path in paths:
        value = _path(data, path)
        if value is None and "." in path:
            head = _path(data, path.split(".", 1)[0])
            if isinstance(head, str) and field in _TEXT_FOR_OBJECT:
                value = head  # schema.org allows plain text where an object is expected
        value = _clean(field, value)
        if value not in (None, ""):
            return value
    return None


def entity_records(entities: list[dict], wanted_types: list[str] | None = None) -> list[dict]:
    """Map entities to flat records. Records describing the same thing in different syntaxes are
    compared field by field; disagreements are listed in `structured_conflicts` on both records."""
    table = mappings()["types"]
    wanted = set(wanted_types or table)
    records: list[dict] = []
    for entity in entities:
        mapped = next((m for m in map(_resolve_type, entity["types"]) if m and m in wanted), None)
        if not mapped:
            continue
        record: dict[str, object] = {"schema_type": entity["types"][0], "structured_syntax": entity["syntax"]}
        for field, spec in table[mapped]["fields"].items():
            value = _field_value(entity["data"], field, spec)
            if value is not None:
                record[field] = value
        if len(record) > 2:
            records.append(record)
    by_key: dict[tuple, list[dict]] = {}
    for record in records:
        key = (record["schema_type"], str(record.get("identifier") or record.get("sku") or record.get("url") or record.get("name") or record.get("title") or "").lower())
        by_key.setdefault(key, []).append(record)
    kept: list[dict] = []
    for (schema_type, key), group in by_key.items():
        if not key or len(group) == 1:
            kept.extend(group)  # nothing identifies these as the same thing
            continue
        fields = {k for r in group for k in r if k not in ("structured_syntax", "schema_type")}
        conflicts = sorted(k for k in fields if len({str(r[k]) for r in group if k in r}) > 1)
        if conflicts:
            for record in group:
                record["structured_conflicts"] = ",".join(conflicts)
            kept.extend(group)
            continue
        merged = dict(group[0])  # consistent descriptions (repeated blocks or several syntaxes): one record
        for record in group[1:]:
            merged.update({k: v for k, v in record.items() if k not in merged})
        merged["structured_syntax"] = ",".join(sorted({r["structured_syntax"] for r in group}))
        kept.append(merged)
    return kept


def extract_structured_records(html: str, url: str, extraction: dict) -> list[dict]:
    syntaxes = tuple(extraction.get("syntaxes") or SYNTAXES)
    return entity_records(extract_entities(html, url, syntaxes), extraction.get("schema_types"))


def extract_article(html: str, url: str, extraction: dict | None = None) -> dict | None:
    import trafilatura
    from htmldate import find_date

    extraction = extraction or {}
    document = trafilatura.bare_extraction(
        html, url=url, with_metadata=True, include_comments=False, include_tables=bool(extraction.get("include_tables", True)),
        favor_precision=extraction.get("favor") == "precision", favor_recall=extraction.get("favor") == "recall",
    )
    if document is None:
        return None
    data = document.as_dict() if hasattr(document, "as_dict") else dict(document)
    text = data.get("text") or ""
    if len(text) < int(extraction.get("min_text_length", 200)):
        return None
    published = data.get("date") or find_date(html, url=url, original_date=True, outputformat="%Y-%m-%d")
    record = {
        "title": data.get("title"), "author": data.get("author"), "date_published": published, "site_name": data.get("sitename"),
        "description": data.get("description"), "language": data.get("language"), "categories": data.get("categories"),
        "tags": data.get("tags"), "url": data.get("url") or url, "image": data.get("image"), "text": text[: int(extraction.get("max_text_length", 100_000))],
        "word_count": len(text.split()),
    }
    if extraction.get("output") == "markdown":
        record["markdown"] = trafilatura.extract(html, url=url, output_format="markdown", include_comments=False) or ""
    return {k: (", ".join(v) if isinstance(v, list) else v) for k, v in record.items() if v not in (None, "", [])}
