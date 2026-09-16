"""The in-process httpx collection engine.

One loop drives every source type. Pages come from a start URL with pagination, a sitemap, a feed, a
scoped crawl, or llms.txt. Every request first passes scope, robots.txt, TDMRep, and AIPREF checks and
the per-host politeness clock, and every response is checked for challenges and usage signals. Records
come from the preset's extraction mode (selectors, structured data, article, document tables, API).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import quote, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .errors import PolicyViolation
from .extraction import (
    ScrapeResult, _detail_urls, _minimum_coverage, _next_api_url, _next_html_url, _unique_fields, _validate_record, _with_query,
    api_auth_headers, canonicalize_url, extract_page, extraction_mode, strategy_rationale, validate_url,
)
from .fetch import fetch, from_cache, make_client, scheduler_for
from .signals import SignalChecker

DISCOVERY_MODES = ("none", "sitemap", "feed", "crawl", "llms_txt")
_FILE_LINK = re.compile(r"\.(pdf|csv|xlsx|json)(?:$|\?)", re.I)


@dataclass
class FrontierStore:
    """Persistence hooks for crawl frontiers (the service backs these with SQLite)."""

    load: Callable[[], tuple[list[tuple[str, int]], list[str]]] = lambda: ([], [])
    add: Callable[[str, int], None] = lambda url, depth: None
    visit: Callable[[str, str], None] = lambda url, status: None


# --- request templates (open data APIs) -----------------------------------------------------------

def resolve_variables(preset: dict, variables: dict | None, record_limit: int) -> dict[str, str]:
    """Validate user variables against the preset's declared variables. Unknown variables are refused."""
    request = preset.get("request") if isinstance(preset.get("request"), dict) else {}
    declared = request.get("variables") or {}
    variables = dict(variables or {})
    unknown = set(variables) - set(declared)
    if unknown:
        raise PolicyViolation(f"Unknown request variables: {', '.join(sorted(unknown))}")
    resolved: dict[str, str] = {}
    for name, spec in declared.items():
        value = variables.get(name, spec.get("default"))
        if name == request.get("limit_variable"):
            value = min(int(value or record_limit), record_limit)
        if value in (None, ""):
            if spec.get("required"):
                raise PolicyViolation(f"Request variable '{name}' is required")
            resolved[name] = ""
            continue
        kind = spec.get("type", "string")
        if kind in ("integer", "number"):
            try:
                number = int(value) if kind == "integer" else float(value)
            except (TypeError, ValueError) as error:
                raise PolicyViolation(f"Request variable '{name}' must be a {kind}") from error
            if "minimum" in spec and number < spec["minimum"] or "maximum" in spec and number > spec["maximum"]:
                raise PolicyViolation(f"Request variable '{name}' is outside {spec.get('minimum')}..{spec.get('maximum')}")
            value = str(number)
        elif kind == "enum":
            if str(value) not in spec.get("choices", []):
                raise PolicyViolation(f"Request variable '{name}' must be one of {', '.join(spec.get('choices', []))}")
        elif kind == "sparql":
            value = sparql_with_limit(str(value), record_limit)
        else:
            value = str(value)
            if len(value) > int(spec.get("max_length", 500)):
                raise PolicyViolation(f"Request variable '{name}' is too long")
            if spec.get("pattern") and not re.fullmatch(spec["pattern"], value):
                raise PolicyViolation(f"Request variable '{name}' has an invalid format")
        resolved[name] = str(value)
    box = request.get("bbox")
    if isinstance(box, dict):
        try:
            south, west, north, east = (float(resolved[box[k]]) for k in ("south", "west", "north", "east"))
        except (KeyError, ValueError) as error:
            raise PolicyViolation("The bounding box needs south, west, north, and east") from error
        if south >= north or west >= east:
            raise PolicyViolation("The bounding box is empty: south must be below north and west left of east")
        if (north - south) * (east - west) > float(box.get("max_area_degrees", 0.25)):
            raise PolicyViolation(f"The bounding box is too large; keep it under {box.get('max_area_degrees', 0.25)} square degrees")
    return resolved


def sparql_with_limit(query: str, cap: int) -> str:
    """Read-only SPARQL with a LIMIT no larger than the record cap."""
    if len(query) > 20_000:
        raise PolicyViolation("SPARQL query is too long")
    stripped = re.sub(r"(?m)^\s*#[^\n]*$", "", query)
    if not re.search(r"^\s*(prefix[^\n]*\n\s*)*(select|construct|ask|describe)\b", stripped, re.I | re.S):
        raise PolicyViolation("Only read-only SPARQL queries (SELECT, CONSTRUCT, ASK, DESCRIBE) are allowed")
    match = re.search(r"\blimit\s+(\d+)\s*(offset\s+\d+\s*)?$", stripped.strip(), re.I)
    if match:
        if int(match.group(1)) > cap:
            return stripped.strip()[: match.start(1)] + str(cap) + stripped.strip()[match.end(1):]
        return stripped.strip()
    return stripped.strip() + f"\nLIMIT {cap}"


def _render(template: str, values: dict[str, str], encode: bool) -> str:
    def replace(match: re.Match) -> str:
        value = values.get(match.group(1), "")
        return quote(value, safe="") if encode else value

    return re.sub(r"\{\{\s*([a-zA-Z_][\w]*)\s*\}\}", replace, template)


def build_request(start_url: str, preset: dict, variables: dict[str, str]) -> tuple[str, str, bytes | None, dict[str, str]]:
    request = preset.get("request") if isinstance(preset.get("request"), dict) else {}
    url = start_url or _render(str(request.get("url_template", "")), variables, encode=True)
    if not url:
        raise PolicyViolation("A start URL is required")
    method = str(request.get("method", "GET")).upper()
    if method not in ("GET", "POST"):
        raise PolicyViolation("Only GET and POST requests are allowed")
    body, headers = None, {}
    if method == "POST":
        template = str(request.get("body_template", ""))
        content_type = str(request.get("content_type", "application/x-www-form-urlencoded"))
        if request.get("form_field"):
            # The whole rendered template is one form field (for example Overpass QL in `data`). Variables are
            # validated numbers, enums, or patterns before rendering.
            from urllib.parse import urlencode

            body = urlencode({str(request["form_field"]): _render(template, variables, encode=False)}).encode()
            content_type = "application/x-www-form-urlencoded"
        elif content_type == "application/json":
            body = json.dumps(json.loads(_render(template, {k: json.dumps(v)[1:-1] for k, v in variables.items()}, encode=False))).encode()
        elif content_type == "application/x-www-form-urlencoded":
            body = _render(template, variables, encode=True).encode()
        else:
            body = _render(template, variables, encode=False).encode()
        headers["Content-Type"] = content_type
    if request.get("accept"):
        headers["Accept"] = str(request["accept"])
    return url, method, body, headers


# --- the collection loop --------------------------------------------------------------------------

def collect(
    start_url: str,
    preset: dict,
    max_records: int | None = None,
    max_pages: int | None = None,
    should_stop: Callable[[], bool] = lambda: False,
    on_page: Callable[[dict], None] = lambda event: None,
    client: httpx.Client | None = None,
    credential: str | None = None,
    contact: dict | None = None,
    cache_dir: Path | None = None,
    capture=None,
    purpose: str | None = None,
    variables: dict | None = None,
    frontier_store: FrontierStore | None = None,
    include_local_signals: bool = False,
    sleep: Callable[[float], None] = time.sleep,
) -> ScrapeResult:
    policy = preset.get("policy", {})
    if not isinstance(policy, dict) or policy.get("requires_user_authorization_acknowledgement") is not True:
        raise PolicyViolation("Preset requires an explicit authorization acknowledgement")
    strategy = (preset.get("strategy") or {}).get("preferred", "http")
    if strategy not in ("http", "api"):
        raise PolicyViolation(f"Strategy {strategy!r} is not available in the HTTP runtime; use Scrape Studio for rendered pages")

    limits = preset.get("request_limits", {}) if isinstance(preset.get("request_limits"), dict) else {}
    configured_records = int(limits.get("max_records_default", 500))
    record_limit = min(max_records or configured_records, configured_records)
    configured_pages = int(limits.get("max_pages_default", 10))
    page_limit = min(max_pages or configured_pages, configured_pages)
    deadline = time.monotonic() + int(limits.get("max_duration_seconds", 900))

    resolved_variables = resolve_variables(preset, variables, record_limit)
    url, method, body, request_headers = build_request(start_url, preset, resolved_variables)
    validate_url(url, preset)
    integration = (preset.get("strategy") or {}).get("api_integration") or {}
    query_auth = integration.get("parameter") if integration.get("auth") == "query_param" and credential else None
    if integration.get("auth") == "query_param" and not credential and not integration.get("optional"):
        raise PolicyViolation("This API preset requires a saved credential; add one in Settings")

    owns_client = client is None
    client = client or make_client(preset, contact, api_auth_headers(preset, credential), cache_dir)
    if not owns_client:
        client.headers.update(api_auth_headers(preset, credential))
    scheduler = scheduler_for(preset)
    signals = SignalChecker(client, purpose or policy.get("purpose") or "internal_analysis", policy.get("robots_policy", "respect"), include_local_signals, scheduler)

    extraction = preset.get("extraction", {}) if isinstance(preset.get("extraction"), dict) else {}
    fields = extraction.get("fields", []) or []
    pagination = preset.get("pagination", {}) if isinstance(preset.get("pagination"), dict) else {}
    discovery = preset.get("discovery", {}) if isinstance(preset.get("discovery"), dict) else {}
    mode = discovery.get("mode", "none")
    unique_by = _unique_fields(preset)

    state = {"cached": 0, "discovered": 0}
    records: list[dict] = []
    files: list[dict] = []
    warnings: list[str] = []
    seen_keys: set[tuple] = set()
    rejected = duplicates = candidate_count = pages_fetched = 0
    stop_reason = "completed"

    def get(target: str, *, method: str = "GET", content: bytes | None = None, headers: dict | None = None, content_page: bool = True) -> httpx.Response:
        validate_url(target, preset)
        signals.check_url(target)
        request_url = _with_query(target, query_auth, credential) if query_auth else target
        response = fetch(client, request_url, preset, validate_url, scheduler, method=method, content=content, headers=headers, sleep=sleep)
        if from_cache(response):
            state["cached"] += 1
        html = response.text[:100_000] if "html" in response.headers.get("content-type", "") else None
        signals.check_response(target, response, html)
        if capture is not None:
            capture.record(target, response.status_code, response.reason_phrase, dict(response.headers), response.content)
        return response

    def budget_left() -> str | None:
        if len(records) >= record_limit:
            return "max_records"
        if pages_fetched >= page_limit:
            return "max_pages"
        if time.monotonic() > deadline:
            return "max_duration"
        if should_stop():
            return "cancelled"
        return None

    def accept(candidates: list[dict]) -> int:
        nonlocal rejected, duplicates, candidate_count
        candidate_count += len(candidates)
        added = 0
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
            added += 1
        return added

    def page_records(response: httpx.Response, page_url: str) -> list[dict]:
        content_type = response.headers.get("content-type", "")
        if "markdown" in content_type or page_url.endswith(".md"):
            return [_markdown_record(response.text, page_url, preset)]
        page = extract_page(response.text if "pdf" not in content_type else "", response.content, content_type, page_url, preset, warnings)
        if mode_is_documents and "html" in content_type and discovery.get("file_links", True):
            page.extend(document_links(response.text, page_url))
        return page

    mode_is_documents = extraction_mode(preset) == "document_tables"

    def document_links(html: str, page_url: str) -> list[dict]:
        from .documents import check_document

        found: list[dict] = []
        for link in dict.fromkeys(urljoin(page_url, a.get("href")) for a in BeautifulSoup(html, "html.parser").select("a[href]") if _FILE_LINK.search(a.get("href") or "")):
            if budget_left() or len(files) >= int(discovery.get("max_files", 20)):
                break
            try:
                validate_url(link, preset)
            except PolicyViolation:
                warnings.append("skipped a file link outside the preset scope")
                continue
            response = get(link)
            try:
                kind = check_document(response.content)
            except ValueError as error:
                warnings.append(str(error))
                continue
            state["discovered"] += 1
            if kind == "pdf":
                found.extend(extract_page("", response.content, "application/pdf", link, preset, warnings))
            elif kind == "csv":
                found.extend(_csv_rows(response.content, link, preset))
            elif kind == "json":
                files.append({"url": link, "kind": kind, "sha256": hashlib.sha256(response.content).hexdigest(), "content": response.content})
            else:
                files.append({"url": link, "kind": kind, "sha256": hashlib.sha256(response.content).hexdigest(), "content": response.content})
        return found

    try:
        if mode in (None, "none"):
            current: str | None = url
            visited: set[str] = set()
            seen_bodies: set[str] = set()
            while current:
                stop_reason = budget_left() or ""
                if stop_reason:
                    break
                canonical = canonicalize_url(current, preset)
                if canonical in visited:
                    stop_reason = "repeated_canonical_url"
                    break
                visited.add(canonical)
                first = current == url and pages_fetched == 0
                response = get(current, method=method if first else "GET", content=body if first else None, headers=request_headers if first else None)
                signature = hashlib.sha256(response.content).hexdigest()
                if signature in seen_bodies:
                    stop_reason = "repeated_response"
                    break
                seen_bodies.add(signature)
                if strategy == "api":
                    document = response.json()
                    candidates = extract_page(response.text, response.content, "application/json", current, preset, warnings)
                    soup = None
                elif pagination.get("type") == "detail_links":
                    soup = BeautifulSoup(response.text, "html.parser")
                    candidates = []
                    for detail_url in _detail_urls(soup, current, pagination, preset, warnings):
                        if len(records) + len(candidates) >= record_limit or should_stop() or time.monotonic() > deadline:
                            break
                        canonical_detail = canonicalize_url(detail_url, preset)
                        if canonical_detail in visited:
                            continue
                        visited.add(canonical_detail)
                        detail = get(detail_url)
                        candidates.extend(page_records(detail, detail_url)[:1])
                else:
                    soup = BeautifulSoup(response.text, "html.parser") if "html" in response.headers.get("content-type", "html") else None
                    candidates = page_records(response, current)
                new_records = accept(candidates)
                pages_fetched += 1
                on_page({"page": pages_fetched, "url": canonical, "candidates": len(candidates), "new_records": new_records, "total_records": len(records), "cached": from_cache(response)})
                if new_records == 0 and pages_fetched > 1:
                    stop_reason = "no_new_records"
                    break
                if strategy == "api":
                    current = _next_api_url(document, current, pagination, pages_fetched)
                else:
                    current = _next_html_url(soup, current, pagination, pages_fetched) if soup is not None else None
                if current:
                    validate_url(current, preset)
                elif pagination.get("type", "none") not in ("none", None) and len(records) < record_limit:
                    stop_reason = "missing_continuation"
            stop_reason = stop_reason or "completed"
        elif mode == "feed":
            response = get(url, headers=request_headers)
            from .discovery import parse_feed

            entries = parse_feed(response.content, url)
            state["discovered"] = len(entries)
            pages_fetched += 1
            if (discovery.get("feed") or {}).get("records", True):
                candidates = [_feed_record(entry, url, preset) for entry in entries]
                on_page({"page": 1, "url": canonicalize_url(url, preset), "candidates": len(candidates), "new_records": accept(candidates), "total_records": len(records)})
                stop_reason = "max_records" if len(records) >= record_limit and len(entries) > record_limit else "completed"
            else:
                counter = {"pages": 0}
                stop_reason = _visit_pages((e["link"] for e in entries if e.get("link")), get, page_records, accept, budget_left_pages(lambda: counter["pages"], page_limit, records, record_limit, deadline, should_stop), on_page, preset, warnings, counter)
                pages_fetched += counter["pages"]
        elif mode == "sitemap":
            from .discovery import iter_sitemap_urls

            config = discovery.get("sitemap") or {}
            origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            if config.get("urls"):
                sitemap_urls = [urljoin(origin, u) for u in config["urls"]]
            elif urlparse(url).path.endswith((".xml", ".xml.gz", ".txt")):
                sitemap_urls = [url]
            else:
                sitemap_urls = signals.for_url(url).sitemaps or [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]

            def sitemap_get(target: str) -> httpx.Response | None:
                try:
                    return get(target, content_page=False)
                except httpx.HTTPStatusError:
                    warnings.append(f"sitemap not available: {urlparse(target).path}")
                    return None

            def in_scope(target: str) -> bool:
                try:
                    validate_url(target, preset)
                    return True
                except PolicyViolation:
                    return False

            discovered = iter_sitemap_urls(sitemap_urls, sitemap_get, in_scope, config, warnings, int(config.get("max_urls", page_limit)))

            def counted():
                for item in discovered:
                    state["discovered"] += 1
                    yield item.url

            counter = {"pages": 0}
            stop_reason = _visit_pages(counted(), get, page_records, accept, budget_left_pages(lambda: counter["pages"], page_limit, records, record_limit, deadline, should_stop), on_page, preset, warnings, counter)
            pages_fetched = counter["pages"]
        elif mode == "crawl":
            from .discovery import Frontier, extract_links

            config = discovery.get("crawl") or {}
            store = frontier_store or FrontierStore()
            frontier = Frontier(url, int(config.get("max_depth", 2)), bool(config.get("same_host_only", True)), config.get("link_pattern"), config.get("exclude_pattern"), on_add=store.add, on_visit=store.visit)
            pending, done = store.load()
            frontier.restore(pending, done)
            frontier.seed()
            extract_pattern = re.compile(config["extract_pattern"]) if config.get("extract_pattern") else None
            while True:
                stop_reason = budget_left() or ""
                if stop_reason:
                    break
                item = frontier.next()
                if item is None:
                    stop_reason = "frontier_exhausted"
                    break
                target, depth = item
                try:
                    validate_url(target, preset)
                    response = get(target)
                except PolicyViolation as error:
                    if "robots.txt disallows" in str(error) or "outside" in str(error):
                        warnings.append(f"skipped {urlparse(target).path}: {str(error).removeprefix('Collection stopped: ')}")
                        frontier.visited(target, "skipped")
                        continue
                    raise
                except httpx.HTTPStatusError as error:
                    warnings.append(f"skipped {urlparse(target).path}: HTTP {error.response.status_code}")
                    frontier.visited(target, "failed")
                    continue
                pages_fetched += 1
                candidates: list[dict] = []
                path = urlparse(target).path + (f"?{urlparse(target).query}" if urlparse(target).query else "")
                if extract_pattern is None or extract_pattern.search(path):
                    candidates = page_records(response, target)
                added = accept(candidates)
                if "html" in response.headers.get("content-type", "") and depth < frontier.max_depth:
                    for link in extract_links(response.text, target):
                        try:
                            validate_url(link, preset)
                        except PolicyViolation:
                            continue
                        if frontier.add(link, depth + 1):
                            state["discovered"] += 1
                frontier.visited(target, "done")
                on_page({"page": pages_fetched, "url": canonicalize_url(target, preset), "depth": depth, "candidates": len(candidates), "new_records": added, "total_records": len(records), "frontier": len(frontier.queue)})
        elif mode == "llms_txt":
            from .discovery import parse_llms_txt

            origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
            response = get(origin + "/llms.txt", content_page=False)
            links = [link for link in parse_llms_txt(response.text, origin) if not link["optional"] or (discovery.get("llms_txt") or {}).get("include_optional")]
            state["discovered"] = len(links)
            counter = {"pages": 0}

            def in_scope_links():
                for link in links:
                    try:
                        validate_url(link["url"], preset)
                        yield link["url"]
                    except PolicyViolation:
                        warnings.append("skipped an llms.txt link outside the preset scope")

            stop_reason = _visit_pages(in_scope_links(), get, page_records, accept, budget_left_pages(lambda: counter["pages"], page_limit, records, record_limit, deadline, should_stop), on_page, preset, warnings, counter)
            pages_fetched = counter["pages"]
        else:
            raise ValueError(f"Unknown discovery mode {mode!r}")
    finally:
        if owns_client:
            client.close()

    required = [f["key"] for f in fields if isinstance(f, dict) and f.get("required") is True]
    if required and candidate_count:
        coverage = sum(all(r.get(k) not in (None, "") for k in required) for r in records) / candidate_count
        minimum = _minimum_coverage(preset)
        if coverage < minimum:
            warnings.append(f"required field coverage {coverage:.2f} below minimum {minimum:.2f}")
    for info in signals.summary():
        warnings.extend(info["warnings"])
    return ScrapeResult(
        tuple(records[:record_limit]), url, datetime.now(timezone.utc).isoformat(), strategy, pages_fetched,
        rejected, duplicates, tuple(dict.fromkeys(warnings)), stop_reason, strategy_rationale(preset),
        engine="httpx", signals=tuple(signals.summary()), cached_responses=state["cached"], discovered_urls=state["discovered"], files=tuple(files),
    )


def budget_left_pages(pages: Callable[[], int], page_limit: int, records: list, record_limit: int, deadline: float, should_stop: Callable[[], bool]) -> Callable[[], str | None]:
    def check() -> str | None:
        if len(records) >= record_limit:
            return "max_records"
        if pages() >= page_limit:
            return "max_pages"
        if time.monotonic() > deadline:
            return "max_duration"
        if should_stop():
            return "cancelled"
        return None

    return check


def _visit_pages(urls, get, page_records, accept, budget_left, on_page, preset, warnings, counter) -> str:
    seen: set[str] = set()
    for target in urls:
        reason = budget_left()
        if reason:
            return reason
        canonical = canonicalize_url(target, preset)
        if canonical in seen:
            continue
        seen.add(canonical)
        try:
            response = get(target)
        except PolicyViolation as error:
            if "robots.txt disallows" in str(error):
                warnings.append(f"skipped {urlparse(target).path}: robots.txt disallows it")
                continue
            raise
        except httpx.HTTPStatusError as error:
            warnings.append(f"skipped {urlparse(target).path}: HTTP {error.response.status_code}")
            continue
        counter["pages"] += 1
        candidates = page_records(response, target)
        on_page({"page": counter["pages"], "url": canonical, "candidates": len(candidates), "new_records": accept(candidates), "total_records": None})
    return budget_left() or "completed"


def _feed_record(entry: dict, feed_url: str, preset: dict) -> dict:
    from .extraction import _project_fields, _with_provenance

    fields = (preset.get("extraction") or {}).get("fields", []) or []
    return _with_provenance(_project_fields({k: v for k, v in entry.items() if v not in (None, "")}, fields, feed_url, preset), feed_url, preset)


def _markdown_record(text: str, url: str, preset: dict) -> dict:
    from .extraction import _project_fields, _with_provenance

    title = next((line.lstrip("# ").strip() for line in text.splitlines() if line.startswith("#")), None)
    fields = (preset.get("extraction") or {}).get("fields", []) or []
    return _with_provenance(_project_fields({"title": title, "url": url, "text": text[:100_000], "format": "markdown"}, fields, url, preset), url, preset)


def _csv_rows(content: bytes, url: str, preset: dict) -> list[dict]:
    from .extraction import _with_provenance

    text = content.decode("utf-8-sig", "replace")
    reader = csv.DictReader(io.StringIO(text))
    return [_with_provenance({**{k or f"column_{i}": v for i, (k, v) in enumerate(row.items(), 1)}, "source_row": n}, url, preset) for n, row in enumerate(reader, 1) if n <= 100_000]
