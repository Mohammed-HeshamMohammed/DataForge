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
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, Tag

USER_AGENT = "DataForge/0.1 (local desktop; permitted collection)"
TEST_MODE_MAX_RECORDS = 10
_LOCAL_HOSTS = {"127.0.0.1", "localhost"}
_ACCESS_STOP_CODES = {401, 402, 403, 407, 408, 425, 429, 451}
_CHALLENGE_MARKERS = ("g-recaptcha", "h-captcha", "cf-challenge", "/cdn-cgi/challenge-platform", "captcha-delivery")


class PolicyViolation(ValueError):
    """Raised when a request falls outside the declared collection policy. Never escalate; stop."""


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


def extract_html(url: str, preset: dict[str, object], max_records: int | None = None, **kwargs) -> ScrapeResult:
    return extract_html_pages(url, preset, max_records=max_records, **kwargs)


def extract_html_pages(
    url: str,
    preset: dict[str, object],
    max_records: int | None = None,
    max_pages: int | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[dict], None] = lambda event: None,
    client: httpx.Client | None = None,
    credential: str | None = None,
) -> ScrapeResult:
    validate_url(url, preset)
    auth_headers = api_auth_headers(preset, credential)
    policy = preset.get("policy", {})
    if not isinstance(policy, dict) or policy.get("requires_user_authorization_acknowledgement") is not True:
        raise PolicyViolation("Preset requires an explicit authorization acknowledgement")
    strategy = preset.get("strategy", {}).get("preferred", "http")
    if strategy not in ("http", "api"):
        raise PolicyViolation(f"Strategy {strategy!r} is not available in the HTTP runtime; use Scrape Studio for rendered pages")

    limits = preset.get("request_limits", {}) if isinstance(preset.get("request_limits"), dict) else {}
    configured_records = int(limits.get("max_records_default", 500))
    record_limit = min(max_records or configured_records, configured_records)
    configured_pages = int(limits.get("max_pages_default", 10))
    page_limit = min(max_pages or configured_pages, configured_pages)
    delay_seconds = int(limits.get("min_delay_ms", 0)) / 1000
    deadline = time.monotonic() + int(limits.get("max_duration_seconds", 900))

    extraction = preset.get("extraction", {}) if isinstance(preset.get("extraction"), dict) else {}
    fields = extraction.get("fields", [])
    if not isinstance(fields, list):
        raise ValueError("Preset extraction.fields must be a list")
    pagination = preset.get("pagination", {}) if isinstance(preset.get("pagination"), dict) else {}

    records: list[dict[str, object]] = []
    rejected = duplicates = candidate_count = pages_fetched = 0
    warnings: list[str] = []
    unique_by = _unique_fields(preset)
    seen_keys: set[tuple[object, ...]] = set()
    visited_urls: set[str] = set()
    seen_bodies: set[str] = set()
    stop_reason = "completed"
    current_url: str | None = url
    owns_client = client is None
    client = client or httpx.Client(timeout=20.0, headers={"User-Agent": USER_AGENT, **auth_headers})
    robots: dict[str, RobotFileParser | None] = {}
    try:
        while current_url:
            if len(records) >= record_limit:
                stop_reason = "max_records"
                break
            if pages_fetched >= page_limit:
                stop_reason = "max_pages"
                break
            if time.monotonic() > deadline:
                stop_reason = "max_duration"
                break
            canonical = canonicalize_url(current_url, preset)
            if canonical in visited_urls:
                stop_reason = "repeated_canonical_url"
                break
            if should_stop():
                stop_reason = "cancelled"
                break
            if pages_fetched and delay_seconds:
                time.sleep(delay_seconds)
            visited_urls.add(canonical)
            _check_robots(client, current_url, robots, preset)
            response = _get(client, current_url, preset)
            signature = hashlib.sha256(response.content).hexdigest()
            if signature in seen_bodies:
                stop_reason = "repeated_response"
                break
            seen_bodies.add(signature)

            if strategy == "api":
                document = response.json()
                candidates = _extract_json_records(document, current_url, extraction, fields, preset)
            else:
                soup = BeautifulSoup(response.text, "html.parser")
                root_css = (extraction.get("record_root") or {}).get("css")
                if not isinstance(root_css, str):
                    raise ValueError("Preset must define extraction.record_root.css")
                candidates = _extract_records(soup, current_url, root_css, fields, preset)
            candidate_count += len(candidates)
            new_records = 0
            for candidate in candidates:
                if len(records) >= record_limit:
                    break
                errors = _validate_record(candidate, fields)
                if errors:
                    rejected += 1
                    warnings.extend(f"record rejected: {error}" for error in errors)
                    continue
                key = tuple(candidate.get(field) for field in unique_by)
                if unique_by and all(value not in (None, "") for value in key):
                    if key in seen_keys:
                        duplicates += 1
                        continue
                    seen_keys.add(key)
                records.append(candidate)
                new_records += 1
            pages_fetched += 1
            on_page({"page": pages_fetched, "url": canonical, "candidates": len(candidates), "new_records": new_records, "total_records": len(records)})
            if new_records == 0 and pages_fetched > 1:
                stop_reason = "no_new_records"
                break
            if strategy == "api":
                current_url = _next_api_url(document, current_url, pagination, pages_fetched)
            else:
                current_url = _next_html_url(soup, current_url, pagination, pages_fetched)
            if current_url:
                validate_url(current_url, preset)
            elif pagination.get("type", "none") not in ("none", None) and len(records) < record_limit:
                stop_reason = "missing_continuation"
    finally:
        if owns_client:
            client.close()

    required = [f["key"] for f in fields if isinstance(f, dict) and f.get("required") is True]
    if required and candidate_count:
        coverage = sum(all(r.get(k) not in (None, "") for k in required) for r in records) / candidate_count
        minimum = _minimum_coverage(preset)
        if coverage < minimum:
            warnings.append(f"required field coverage {coverage:.2f} below minimum {minimum:.2f}")
    return ScrapeResult(
        tuple(records[:record_limit]), url, datetime.now(timezone.utc).isoformat(), strategy, pages_fetched,
        rejected, duplicates, tuple(dict.fromkeys(warnings)), stop_reason, strategy_rationale(preset),
    )


def api_auth_headers(preset: dict[str, object], credential: str | None) -> dict[str, str]:
    """Authorized API credentials go only in headers to the preset's own hosts (redirects out of scope are refused)."""
    integration = (preset.get("strategy") or {}).get("api_integration") if isinstance(preset.get("strategy"), dict) else None
    if not isinstance(integration, dict) or not integration.get("auth"):
        return {}
    if not credential:
        raise PolicyViolation("This API preset requires a saved credential; add one in Settings")
    if integration["auth"] == "bearer":
        return {"Authorization": f"Bearer {credential}"}
    return {str(integration["header_name"]): credential}


def extract_document(text: str, source_url: str, preset: dict[str, object]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[str]]:
    """Extract and validate records from an already-loaded document (fixtures, rendered WebView pages).

    Returns (valid records, rejected candidates, warnings). No network access.
    """
    extraction = preset.get("extraction", {}) if isinstance(preset.get("extraction"), dict) else {}
    fields = extraction.get("fields", [])
    if preset.get("strategy", {}).get("preferred") == "api":
        candidates = _extract_json_records(json.loads(text), source_url, extraction, fields, preset)
    else:
        root_css = (extraction.get("record_root") or {}).get("css")
        if not isinstance(root_css, str):
            raise ValueError("Preset must define extraction.record_root.css")
        candidates = _extract_records(BeautifulSoup(text, "html.parser"), source_url, root_css, fields, preset)
    return validate_candidates(candidates, preset)


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


def _get(client: httpx.Client, url: str, preset: dict[str, object]) -> httpx.Response:
    for _ in range(5):
        response = client.get(url, follow_redirects=False)
        if response.status_code in _ACCESS_STOP_CODES:
            raise PolicyViolation(f"Collection stopped on access or rate-limit response: {response.status_code}")
        if response.is_redirect:
            url = urljoin(url, response.headers.get("location", ""))
            validate_url(url, preset)  # never follow a redirect out of scope
            continue
        response.raise_for_status()
        lowered = response.text[:200_000].lower()
        if any(marker in lowered for marker in _CHALLENGE_MARKERS):
            raise PolicyViolation("Collection stopped: the page presented an access challenge (CAPTCHA/bot check)")
        return response
    raise PolicyViolation("Collection stopped: too many redirects")


def _check_robots(client: httpx.Client, url: str, cache: dict[str, RobotFileParser | None], preset: dict[str, object]) -> None:
    parsed = urlparse(url)
    policy = preset.get("policy", {}) if isinstance(preset.get("policy"), dict) else {}
    if parsed.hostname in _LOCAL_HOSTS or policy.get("robots_policy", "respect") != "respect":
        return
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in cache:
        parser = RobotFileParser()
        try:
            response = client.get(origin + "/robots.txt", follow_redirects=True)
        except httpx.HTTPError:
            response = None
        if response is not None and response.status_code in (401, 403):
            parser.disallow_all = True
        elif response is not None and response.status_code == 200:
            parser.parse(response.text.splitlines())
        else:
            parser.allow_all = True
        cache[origin] = parser
    if not cache[origin].can_fetch(USER_AGENT, url):
        raise PolicyViolation("Collection stopped: robots.txt disallows this URL")


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


def _extract_json_records(document: object, source_url: str, extraction: dict, fields: list[object], preset: dict[str, object]) -> list[dict[str, object]]:
    items = _json_path(document, extraction.get("item_path", ""))
    if not isinstance(items, list):
        raise ValueError("extraction.item_path must resolve to a JSON array")
    records = []
    for item in items:
        record: dict[str, object] = {}
        for field in fields:
            if not isinstance(field, dict) or not isinstance(field.get("key"), str):
                raise ValueError("Each extraction field requires a key")
            value = _json_path(item, field.get("path", field["key"]))
            if value not in (None, ""):
                record[field["key"]] = apply_field(value if isinstance(value, (int, float)) else str(value), field, source_url, preset)
        records.append(_with_provenance(record, source_url, preset))
    return records


def _with_provenance(record: dict[str, object], source_url: str, preset: dict[str, object]) -> dict[str, object]:
    record["source_url"] = source_url
    record["source_retrieved_at"] = datetime.now(timezone.utc).isoformat()
    record["preset_id"] = preset.get("id")
    record["preset_version"] = preset.get("version")
    record["strategy_used"] = preset.get("strategy", {}).get("preferred", "http")
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

def _next_html_url(soup: BeautifulSoup, current_url: str, pagination: dict, pages_fetched: int) -> str | None:
    kind = pagination.get("type", "none")
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
    raise PolicyViolation(f"Pagination type {kind!r} requires the embedded WebView runtime")


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
