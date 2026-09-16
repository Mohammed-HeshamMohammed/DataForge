from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup, Tag

from .errors import PolicyViolation
from .fetch import APP_USER_AGENT as USER_AGENT  # noqa: F401 - public alias
from .fetch import LOCAL_HOSTS as _LOCAL_HOSTS

TEST_MODE_MAX_RECORDS = 10
EXTRACTION_MODES = ("selectors", "structured_data", "article", "document_tables", "api")


@dataclass(frozen=True)
class ScrapeResult:
    records: tuple[dict[str, object], ...]
    source_url: str
    retrieved_at: str
    strategy_used: str
    pages_fetched: int = 1
    rejected_records: int = 0
    duplicate_records: int = 0
    warnings: tuple[str, ...] = ()
    stop_reason: str = "completed"
    strategy_rationale: str = ""
    engine: str = "httpx"
    signals: tuple[dict, ...] = ()
    cached_responses: int = 0
    discovered_urls: int = 0
    files: tuple[dict, ...] = ()


def extract_html(url: str, preset: dict[str, object], max_records: int | None = None, **kwargs) -> ScrapeResult:
    return extract_html_pages(url, preset, max_records=max_records, **kwargs)


def extract_html_pages(url: str, preset: dict[str, object], max_records: int | None = None, max_pages: int | None = None, **kwargs) -> ScrapeResult:
    """Collect records with the in-process httpx engine (all modes). See runtime.collect."""
    from .runtime import collect

    return collect(url, preset, max_records=max_records, max_pages=max_pages, **kwargs)


def api_auth_headers(preset: dict[str, object], credential: str | None) -> dict[str, str]:
    """Authorized API credentials go only in headers to the preset's own hosts (redirects out of scope are refused)."""
    integration = (preset.get("strategy") or {}).get("api_integration") if isinstance(preset.get("strategy"), dict) else None
    if not isinstance(integration, dict) or not integration.get("auth"):
        return {}
    if not credential:
        if integration.get("optional"):
            return {}
        raise PolicyViolation("This API preset requires a saved credential; add one in Settings")
    if integration["auth"] == "bearer":
        return {"Authorization": f"Bearer {credential}"}
    if integration["auth"] == "query_param":
        return {}  # added to each in-scope request URL by the runtime, never to stored URLs
    return {str(integration["header_name"]): credential}


def extract_document(text: str | bytes, source_url: str, preset: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Extract and validate records from an already-loaded document (fixtures, rendered WebView pages,
    archive replays). Binary fixtures may be passed as bytes or as a "base64:" string. No network access.

    Returns (valid records, rejected candidates, warnings).
    """
    if isinstance(text, str) and text.startswith("base64:"):
        import base64

        text = base64.b64decode(text[7:])
    content = text if isinstance(text, bytes) else text.encode("utf-8")
    body = text.decode("utf-8", "replace") if isinstance(text, bytes) else text
    content_type = "application/pdf" if content[:5] == b"%PDF-" else "application/json" if extraction_mode(preset) == "api" else "text/html"
    warnings: list[str] = []
    if (preset.get("discovery") or {}).get("mode") == "feed" and ((preset.get("discovery") or {}).get("feed") or {}).get("records", True):
        from .discovery import parse_feed
        from .runtime import _feed_record

        candidates = [_feed_record(entry, source_url, preset) for entry in parse_feed(content, source_url)]
        valid, rejected, more = validate_candidates(candidates, preset)
        return valid, rejected, more
    candidates = extract_page(body, content, content_type, source_url, preset, warnings)
    valid, rejected, more = validate_candidates(candidates, preset)
    return valid, rejected, list(dict.fromkeys(warnings + more))


def extraction_mode(preset: dict[str, object]) -> str:
    extraction = preset.get("extraction") if isinstance(preset.get("extraction"), dict) else {}
    if (preset.get("strategy") or {}).get("preferred") == "api":
        return "api"
    return str(extraction.get("mode") or "selectors")


def extract_page(body: str, content: bytes, content_type: str, url: str, preset: dict[str, object], warnings: list[str]) -> list[dict[str, object]]:
    """Candidate records from one fetched page in the preset's extraction mode. Shared by both engines,
    health checks, and archive replays, so every path produces identical records."""
    extraction = preset.get("extraction", {}) if isinstance(preset.get("extraction"), dict) else {}
    fields = extraction.get("fields", []) or []
    mode = extraction_mode(preset)
    if mode == "api":
        return _extract_json_records(json.loads(body), url, extraction, fields, preset)
    if mode == "structured_data":
        from .structured import extract_structured_records

        records = extract_structured_records(body, url, extraction)
        return [_with_provenance(_project_fields(record, fields, url, preset), url, preset) for record in records]
    if mode == "article":
        from .structured import extract_article

        record = extract_article(body, url, extraction)
        if record is None:
            warnings.append("no article text found on a page")
            return []
        return [_with_provenance(_project_fields(record, fields, url, preset), url, preset)]
    if mode == "document_tables":
        if content[:5] != b"%PDF-":
            return []  # listing pages only contribute file links
        from .documents import pdf_tables

        rows, more = pdf_tables(content, url, extraction)
        warnings.extend(more)
        return [_with_provenance(_project_fields(row, fields, url, preset), url, preset) for row in rows]
    root = extraction.get("record_root") or {}
    root_css, root_xpath = root.get("css"), root.get("xpath")
    if not isinstance(root_css, str) and not isinstance(root_xpath, str):
        raise ValueError("Preset must define extraction.record_root.css")
    parser = _parser_for(extraction, fields)
    if parser == "parsel":
        return _extract_records_parsel(body, url, root_css, root_xpath, fields, preset)
    if parser == "selectolax" and isinstance(root_css, str):
        return _extract_records_selectolax(body, url, root_css, fields, preset)
    return _extract_records(BeautifulSoup(body, "html.parser"), url, root_css, fields, preset)


def _project_fields(record: dict[str, object], fields: list, url: str, preset: dict) -> dict[str, object]:
    """Selector-free modes: without declared fields keep every mapped value; with fields, pick and transform."""
    if not fields:
        return dict(record)
    projected: dict[str, object] = {}
    for field in fields:
        value = record.get(field.get("path", field["key"]))
        if value not in (None, ""):
            projected[field["key"]] = apply_field(value if isinstance(value, (int, float)) else str(value), field, url, preset)
    for key in ("structured_conflicts", "structured_syntax", "schema_type", "source_page", "source_table", "source_row"):
        if key in record:
            projected[key] = record[key]
    return projected


def _parser_for(extraction: dict, fields: list) -> str:
    has_xpath = any(isinstance(sel, dict) and sel.get("xpath") for f in fields if isinstance(f, dict) for sel in f.get("selectors", []))
    if has_xpath or (extraction.get("record_root") or {}).get("xpath"):
        return "parsel"
    return str(extraction.get("parser") or "bs4")


def validate_candidates(candidates: list[dict[str, object]], preset: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    fields = (preset.get("extraction") or {}).get("fields", [])
    valid, rejected, warnings = [], [], []
    seen: set[tuple[object, ...]] = set()
    unique_by = _unique_fields(preset)
    for candidate in candidates:
        errors = _validate_record(candidate, fields)
        if errors:
            rejected.append(candidate)
            warnings.extend(f"record rejected: {error}" for error in errors)
            continue
        key = tuple(candidate.get(f) for f in unique_by)
        if unique_by and all(v not in (None, "") for v in key):
            if key in seen:
                continue
            seen.add(key)
        valid.append(candidate)
    return valid, rejected, list(dict.fromkeys(warnings))


def strategy_rationale(preset: dict[str, object]) -> str:
    strategy = preset.get("strategy", {}) if isinstance(preset.get("strategy"), dict) else {}
    preferred = strategy.get("preferred", "http")
    if preferred == "api":
        return "Preset declares a JSON API collection; API is the preferred permitted strategy."
    return "Preset data is available in the initial HTML response, so permitted HTTP is used before any rendering."


def validate_url(url: str, preset: dict[str, object]) -> None:
    parsed = urlparse(url)
    local = parsed.hostname in _LOCAL_HOSTS
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise PolicyViolation("Only HTTPS URLs are allowed outside local fixtures")
    if parsed.username or parsed.password:
        raise PolicyViolation("URLs with embedded credentials are not allowed")
    scope = preset.get("url_scope", {}) if isinstance(preset.get("url_scope"), dict) else {}
    allowed_hosts = scope.get("allowed_hosts", [])
    if parsed.hostname not in allowed_hosts and not local:
        raise PolicyViolation("URL host is outside the preset allow-list")
    patterns = scope.get("allowed_path_patterns", [])
    # Patterns anchored with "^" are prefix rules (spec style); unanchored patterns must match the whole path.
    if patterns and not any(
        re.match(pattern, parsed.path) if pattern.startswith("^") else re.fullmatch(pattern, parsed.path)
        for pattern in patterns
    ):
        raise PolicyViolation("URL path is outside the preset scope")


def canonicalize_url(url: str, preset: dict[str, object]) -> str:
    parsed = urlparse(url)
    rules = (preset.get("url_scope") or {}).get("canonicalization", {}) if isinstance(preset.get("url_scope"), dict) else {}
    remove = set(rules.get("remove_query_parameters", []))
    query = urlencode([(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k not in remove])
    return urlunparse((parsed.scheme, (parsed.hostname or "") + (f":{parsed.port}" if parsed.port else ""), parsed.path or "/", "", query, ""))


_PSEUDO = re.compile(r"::(text|attr\(([\w-]+)\))\s*$")


def _select_value(root: Tag, selector: dict[str, object], base_url: str) -> str | None:
    css = selector.get("css")
    if not isinstance(css, str):
        return None
    attribute = selector.get("attribute")
    match = _PSEUDO.search(css)
    if match:
        css = css[: match.start()]
        attribute = match.group(2) if match.group(2) else None
    node = root.select_one(css) if css.strip() else root
    if node is None:
        return None
    value = node.get(attribute) if isinstance(attribute, str) else node.get_text(" ", strip=True)
    if isinstance(value, list):
        value = " ".join(value)
    return str(value).strip() if value is not None and str(value).strip() else None


def _extract_records(soup: BeautifulSoup, source_url: str, root_css: str, fields: list[object], preset: dict[str, object]) -> list[dict[str, object]]:
    records = []
    if not isinstance(root_css, str):
        raise ValueError("XPath record roots need the parsel parser")
    for root in soup.select(root_css):
        record: dict[str, object] = {}
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("key"), str):
                raise ValueError("Each extraction field requires a key")
            for selector in field.get("selectors", []):
                if isinstance(selector, dict) and (value := _select_value(root, selector, source_url)) is not None:
                    record[field["key"]] = apply_field(value, field, source_url, preset)
                    break
        records.append(_with_provenance(record, source_url, preset))
    return records


def _extract_records_parsel(text: str, source_url: str, root_css: str | None, root_xpath: str | None, fields: list, preset: dict) -> list[dict[str, object]]:
    from parsel import Selector

    document = Selector(text=text)
    roots = document.xpath(root_xpath) if root_xpath else document.css(root_css)
    records = []
    for root in roots:
        record: dict[str, object] = {}
        for field in fields:
            for selector in field.get("selectors", []):
                value = _parsel_value(root, selector)
                if value is not None:
                    record[field["key"]] = apply_field(value, field, source_url, preset)
                    break
        records.append(_with_provenance(record, source_url, preset))
    return records


def _parsel_value(root, selector: dict) -> str | None:
    if selector.get("xpath"):
        matches = root.xpath(str(selector["xpath"]))
    else:
        css = str(selector.get("css", ""))
        attribute = selector.get("attribute")
        match = _PSEUDO.search(css)
        if match:
            css, attribute = css[: match.start()], match.group(2)
        matches = root.css(css) if css.strip() else [root]
        if matches and isinstance(attribute, str):
            value = matches[0].attrib.get(attribute)
            return (str(value).strip() or None) if value is not None else None
    if not matches:
        return None
    first = matches[0]
    if isinstance(first.root, str):
        raw = first.get()
    else:
        raw = first.xpath("string()").get("")
    raw = " ".join(str(raw).split())
    return raw or None


def _extract_records_selectolax(text: str, source_url: str, root_css: str, fields: list, preset: dict) -> list[dict[str, object]]:
    from selectolax.parser import HTMLParser

    records = []
    for root in HTMLParser(text).css(root_css):
        record: dict[str, object] = {}
        for field in fields:
            for selector in field.get("selectors", []):
                css, attribute = str(selector.get("css", "")), selector.get("attribute")
                match = _PSEUDO.search(css)
                if match:
                    css, attribute = css[: match.start()], match.group(2)
                node = root.css_first(css) if css.strip() else root
                if node is None:
                    continue
                value = node.attributes.get(attribute) if isinstance(attribute, str) else node.text(separator=" ", strip=True)
                value = " ".join(str(value).split()) if value is not None else None
                if value:
                    record[field["key"]] = apply_field(value, field, source_url, preset)
                    break
        records.append(_with_provenance(record, source_url, preset))
    return records


def _extract_json_records(document: object, source_url: str, extraction: dict, fields: list[object], preset: dict[str, object]) -> list[dict[str, object]]:
    items = _json_path(document, extraction.get("item_path", ""))
    if extraction.get("columnar") and isinstance(items, dict):
        # {"form": ["10-K", ...], "filingDate": [...]} -> one record per index
        columns = {k: v for k, v in items.items() if isinstance(v, list)}
        length = max((len(v) for v in columns.values()), default=0)
        items = [{k: (v[i] if i < len(v) else None) for k, v in columns.items()} for i in range(length)]
    if not isinstance(items, list):
        raise ValueError("extraction.item_path must resolve to a JSON array")
    records = []
    for item in items:
        record: dict[str, object] = {}
        if extraction.get("capture_all") and isinstance(item, dict):
            for key, value in _flatten(item).items():
                if value not in (None, "") and len(record) < 200:
                    record[key] = value
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("key"), str):
                raise ValueError("Each extraction field requires a key")
            value = _json_path(item, field.get("path", field["key"]))
            if value not in (None, ""):
                record[field["key"]] = apply_field(value if isinstance(value, (int, float)) else str(value), field, source_url, preset)
        records.append(_with_provenance(record, source_url, preset))
    return records


def _flatten(item: dict, prefix: str = "", depth: int = 0) -> dict[str, object]:
    """Scalar values of an object, nested keys joined with dots. SPARQL bindings {"type", "value"} collapse."""
    flat: dict[str, object] = {}
    for key, value in item.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            if "value" in value and "type" in value:
                flat[name] = value["value"]
            elif depth < 2:
                flat.update(_flatten(value, name + ".", depth + 1))
        elif isinstance(value, list):
            scalars = [v for v in value if isinstance(v, (str, int, float))]
            if scalars:
                flat[name] = ", ".join(str(v) for v in scalars[:20])
        elif isinstance(value, bool):
            flat[name] = str(value).lower()
        elif isinstance(value, (str, int, float)):
            flat[name] = value
    return flat


def _with_provenance(record: dict[str, object], source_url: str, preset: dict[str, object]) -> dict[str, object]:
    record["source_url"] = source_url
    record["source_retrieved_at"] = datetime.now(timezone.utc).isoformat()
    record["preset_id"] = preset.get("id")
    record["preset_version"] = preset.get("version")
    record["strategy_used"] = preset.get("strategy", {}).get("preferred", "http")
    mode = (preset.get("extraction") or {}).get("mode")
    if mode and mode != "selectors":
        record["extraction_mode"] = mode
    return record


def _json_path(value: object, path: object) -> object:
    if not isinstance(path, str) or not path:
        return value
    for part in path.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        elif isinstance(value, list) and part.isdigit() and int(part) < len(value):
            value = value[int(part)]
        else:
            return None
    return value


# --- transforms and typing -------------------------------------------------------------------------

def _parse_decimal(value: str) -> str | None:
    match = re.search(r"-?\d[\d,]*(?:\.\d+)?", str(value))
    if not match:
        return None
    try:
        return str(Decimal(match.group(0).replace(",", "")))
    except InvalidOperation:
        return None


TRANSFORMS: dict[str, Callable[..., object]] = {
    "trim": lambda v, **_: str(v).strip(),
    "collapse_whitespace": lambda v, **_: " ".join(str(v).split()),
    "lowercase": lambda v, **_: str(v).lower(),
    "strip_html": lambda v, **_: " ".join(html.unescape(re.sub(r"<[^>]+>", " ", str(v))).split()),
    "to_absolute_url": lambda v, base_url, **_: urljoin(base_url, str(v)),
    "canonicalize_url": lambda v, preset, **_: canonicalize_url(str(v), preset),
    "parse_currency_amount": lambda v, **_: _parse_decimal(v),
    "parse_rating": lambda v, **_: _parse_decimal(v),
    "parse_integer": lambda v, **_: (d := _parse_decimal(v)) and str(int(Decimal(d))),
}


def _register_normalizers() -> None:
    from .normalize import transforms

    TRANSFORMS.update(transforms())


_register_normalizers()


def apply_field(value: object, field: dict, base_url: str, preset: dict[str, object]) -> object:
    for name in field.get("transforms", []):
        if name not in TRANSFORMS:
            raise ValueError(f"Unknown transform {name!r}")
        if value is None:
            break
        value = TRANSFORMS[name](value, base_url=base_url, preset=preset)
    if value is None:
        return None
    kind = field.get("type", "string")
    if kind == "integer":
        parsed = _parse_decimal(str(value))
        return int(Decimal(parsed)) if parsed is not None else None
    if kind == "decimal":
        parsed = _parse_decimal(str(value))
        return float(parsed) if parsed is not None else None
    if kind == "url":
        return urljoin(base_url, str(value))
    return str(value) if not isinstance(value, str) else value


def _validate_record(record: dict[str, object], fields: list[object]) -> tuple[str, ...]:
    errors: list[str] = []
    for field in fields:
        if not isinstance(field, dict) or not isinstance(field.get("key"), str):
            continue
        key = field["key"]
        value = record.get(key)
        if value in (None, ""):
            if field.get("required") is True:
                errors.append(f"missing required field {key}")
            continue
        rules = field.get("validate")
        if not isinstance(rules, dict):
            continue
        if isinstance(rules.get("min_length"), int) and len(str(value)) < rules["min_length"]:
            errors.append(f"field {key} shorter than {rules['min_length']}")
        if isinstance(value, (int, float)):
            if "minimum" in rules and value < rules["minimum"]:
                errors.append(f"field {key} below minimum")
            if "maximum" in rules and value > rules["maximum"]:
                errors.append(f"field {key} above maximum")
        if "allowed_hosts" in rules and urlparse(str(value)).hostname not in rules["allowed_hosts"]:
            errors.append(f"field {key} host not allowed")
    return tuple(errors)


# --- pagination ----------------------------------------------------------------------------------

def _detail_urls(soup: BeautifulSoup, current_url: str, pagination: dict, preset: dict, warnings: list[str]) -> list[str]:
    """Detail links from a listing page, restricted to the preset's scope (out-of-scope links are skipped, not followed)."""
    config = pagination.get("links") if isinstance(pagination.get("links"), dict) else {}
    css = config.get("css")
    if not isinstance(css, str):
        raise ValueError("detail_links pagination requires pagination.links.css")
    selector, attribute = css, config.get("attribute", "href")
    match = _PSEUDO.search(css)
    if match:
        selector, attribute = css[: match.start()], match.group(2) or attribute
    urls: list[str] = []
    for node in soup.select(selector):
        value = node.get(attribute)
        if not value:
            continue
        url = urljoin(current_url, str(value))
        try:
            validate_url(url, preset)
        except PolicyViolation:
            warnings.append("skipped a detail link outside the preset scope")
            continue
        if url not in urls:
            urls.append(url)
    return urls


def _next_html_url(soup: BeautifulSoup, current_url: str, pagination: dict, pages_fetched: int) -> str | None:
    kind = pagination.get("type", "none")
    if kind == "detail_links":
        # The listing itself may paginate with a next link after its detail pages are collected.
        return _next_html_url(soup, current_url, {**pagination, "type": "next_link"}, pages_fetched) if isinstance(pagination.get("next"), dict) else None
    if kind == "cursor":
        config = pagination.get("cursor")
        if not isinstance(config, dict):
            return None
        token = _select_value(soup, {"css": config.get("css"), "attribute": config.get("attribute")}, current_url)
        return _with_query(current_url, pagination.get("parameter", "cursor"), token) if token else None
    if kind == "next_link":
        config = pagination.get("next")
        if not isinstance(config, dict):
            return None
        value = _select_value(soup, {"css": config.get("css"), "attribute": config.get("attribute", "href")}, current_url)
        return urljoin(current_url, value) if value else None
    if kind == "page_parameter":
        return _with_query(current_url, pagination.get("parameter", "page"), int(pagination.get("start", 1)) + pages_fetched * int(pagination.get("step", 1)))
    if kind in ("none", None):
        return None
    raise PolicyViolation(f"Pagination type {kind!r} requires Scrape Studio's embedded WebView runtime")


def _next_api_url(document: object, current_url: str, pagination: dict, pages_fetched: int = 1) -> str | None:
    kind = pagination.get("type", "none")
    if kind == "page_parameter":
        return _with_query(current_url, pagination.get("parameter", "page"), int(pagination.get("start", 0)) + pages_fetched * int(pagination.get("step", 1)))
    if kind == "api_cursor":
        cursor = _json_path(document, pagination.get("cursor_path", "next_cursor"))
        return _with_query(current_url, pagination.get("cursor_parameter", "cursor"), cursor) if cursor not in (None, "") else None
    if kind == "next_link":
        next_url = _json_path(document, pagination.get("next_path", "next"))
        return urljoin(current_url, str(next_url)) if next_url else None
    return None


def _with_query(url: str, parameter: str, value: object) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query[parameter] = str(value)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _unique_fields(preset: dict[str, object]) -> tuple[str, ...]:
    validation = preset.get("validation", {})
    unique_by = validation.get("unique_by", []) if isinstance(validation, dict) else []
    return tuple(field for field in unique_by if isinstance(field, str))


def _minimum_coverage(preset: dict[str, object]) -> float:
    validation = preset.get("validation", {})
    value = validation.get("minimum_record_coverage", 0.0) if isinstance(validation, dict) else 0.0
    return float(value) if isinstance(value, (int, float)) else 0.0
