"""Services for the expanded source types: project settings (contact identity, consents, capture), usage
signal records, crawl frontiers, web archive and bulk corpus jobs, watches with diffs, selector suggestions,
structured-data detection, AI draft proposals, and preset fingerprints."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from dataforge_scraping import archives, structured
from dataforge_scraping.diff import diff_records
from dataforge_scraping.errors import PolicyViolation
from dataforge_scraping.extraction import TEST_MODE_MAX_RECORDS, extract_page, validate_candidates
from dataforge_scraping.fetch import fetch, make_client, scheduler_for
from dataforge_scraping.resilience import coverage_drift, field_fingerprints, relocation_suggestions, suggest_from_examples
from dataforge_scraping.runtime import FrontierStore
from dataforge_scraping.signals import PURPOSES, SignalChecker

from .datasets import register_staged_rows
from .jobs import JobCancelled, JobContext, JobKind, JobValidationError
from .logs import log
from .storage import ProjectStore, utc_now

SETTING_DEFAULTS: dict[str, object] = {
    "contact_identity": {"organization": "", "email": ""},
    "network_proxy": {"enabled": False, "url": ""},
    "http_cache": {"enabled": True},
    "warc_capture": {"enabled": False, "retention_days": 30},
    "ai_suggestions": {"provider": "local_heuristic", "endpoint": "", "model": "", "remote_consent": False},
    "default_purpose": "internal_analysis",
}


# --- settings ------------------------------------------------------------------------------------

def get_settings(store: ProjectStore) -> dict:
    values = dict(SETTING_DEFAULTS)
    for row in store._connection.execute("SELECT key, value_json FROM project_settings"):
        values[row["key"]] = json.loads(row["value_json"])
    return values


def set_settings(store: ProjectStore, changes: dict) -> dict:
    for key, value in changes.items():
        if key not in SETTING_DEFAULTS:
            raise ValueError(f"Unknown setting {key!r}")
        if key == "default_purpose" and value not in PURPOSES:
            raise ValueError(f"default_purpose must be one of {', '.join(PURPOSES)}")
        if key == "contact_identity":
            value = {"organization": str(value.get("organization", "")).strip()[:120], "email": str(value.get("email", "")).strip()[:200]}
        if key == "network_proxy":
            url = str(value.get("url", "")).strip()
            parsed = urlparse(url) if url else None
            if url and (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment):
                raise ValueError("Proxy URL must be an http:// or https:// address without credentials, a query, or a fragment")
            value = {"enabled": bool(value.get("enabled")), "url": url[:500]}
            if value["enabled"] and not url:
                raise ValueError("Enter the fixed proxy URL before enabling it")
        if key == "ai_suggestions":
            value = {**SETTING_DEFAULTS["ai_suggestions"], **{k: v for k, v in value.items() if k in SETTING_DEFAULTS["ai_suggestions"]}}
            if value["provider"] not in ("local_heuristic", "model"):
                raise ValueError("ai_suggestions.provider must be local_heuristic or model")
        if key == "warc_capture":
            value = {"enabled": bool(value.get("enabled")), "retention_days": max(1, min(int(value.get("retention_days", 30)), 3650))}
        store._connection.execute(
            "INSERT INTO project_settings(key, value_json, updated_at) VALUES (?,?,?) ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, updated_at = excluded.updated_at",
            (key, json.dumps(value), utc_now()),
        )
    store._connection.commit()
    return get_settings(store)


def cache_dir(store: ProjectStore) -> Path | None:
    return store.project_root / "cache" / "http" if get_settings(store)["http_cache"].get("enabled", True) else None


def proxy_url(store: ProjectStore) -> str | None:
    """One administrator-supplied network route. It is never rotated or changed in response to a block."""
    proxy = get_settings(store)["network_proxy"]
    return str(proxy.get("url")) if proxy.get("enabled") and proxy.get("url") else None


def purge_cache(store: ProjectStore) -> dict:
    import shutil

    removed = 0
    root = store.project_root / "cache"
    if root.exists():
        removed = sum(f.stat().st_size for f in root.rglob("*") if f.is_file())
        shutil.rmtree(root, ignore_errors=True)
    return {"bytes_removed": removed}


def purge_expired_captures(store: ProjectStore) -> int:
    retention = int(get_settings(store)["warc_capture"].get("retention_days", 30))
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention)
    removed = 0
    for row in store._connection.execute("SELECT id, warc_path, created_at FROM scrape_runs WHERE warc_path IS NOT NULL").fetchall():
        if datetime.fromisoformat(row["created_at"]) < cutoff:
            Path(row["warc_path"]).unlink(missing_ok=True)
            store._connection.execute("UPDATE scrape_runs SET warc_path = NULL WHERE id = ?", (row["id"],))
            removed += 1
    store._connection.commit()
    return removed


# --- signals and frontiers -----------------------------------------------------------------------

def record_signals(store: ProjectStore, scrape_run_id: str, signals: list[dict]) -> None:
    for info in signals:
        store._connection.execute(
            "INSERT INTO usage_signals(scrape_run_id, host, robots_status, crawl_delay, tdm_reservation, tdm_policy, content_usage_json, ai_txt, signals_json, checked_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (scrape_run_id, info["host"], info["robots_status"], info.get("crawl_delay"), info.get("tdm_reservation"), info.get("tdm_policy"),
             json.dumps(info.get("content_usage") or {}), int(bool(info.get("ai_txt"))), json.dumps(info), info["checked_at"]),
        )
    store._connection.commit()


def run_signals(store: ProjectStore, scrape_run_id: str) -> list[dict]:
    return [json.loads(row["signals_json"]) for row in store._connection.execute("SELECT signals_json FROM usage_signals WHERE scrape_run_id = ? ORDER BY id", (scrape_run_id,))]


def check_signals(store: ProjectStore, url: str, purpose: str) -> dict:
    """Signals panel: robots.txt, TDMRep, AIPREF, and ai.txt for a host, evaluated for a purpose. Read-only."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError("Signals can be checked for HTTPS URLs")
    preset = {"url_scope": {"allowed_hosts": [parsed.hostname]}, "policy": {}, "request_limits": {}}
    with make_client(None, proxy_url=proxy_url(store)) as client:
        checker = SignalChecker(client, purpose)
        allowed, reason = True, None
        try:
            checker.check_url(url)
            head = fetch(client, url, preset, lambda u, p: None, check_challenge=False)
            checker.check_response(url, head, head.text[:100_000] if "html" in head.headers.get("content-type", "") else None)
        except PolicyViolation as error:
            allowed, reason = False, str(error)
        except Exception as error:  # noqa: BLE001 - network errors are reported, not raised
            allowed, reason = False, f"{type(error).__name__}: {error}"
    return {"url": f"{parsed.scheme}://{parsed.netloc}{parsed.path}", "purpose": purpose, "allowed": allowed, "reason": reason, "hosts": checker.summary()}


_CHECKERS: dict[tuple[str, str], tuple[SignalChecker, object, float]] = {}
_CHECKERS_LOCK = threading.Lock()
CHECKER_TTL_SECONDS = 600


def shared_checker(purpose: str, fixed_proxy: str | None = None) -> SignalChecker:
    """Signals for navigation checks outside collection jobs (Scrape Studio). Cached per purpose for ten
    minutes, so each host's robots.txt, TDMRep, and ai.txt are read once per session, not once per page."""
    import time

    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {', '.join(PURPOSES)}")
    key = (purpose, fixed_proxy or "")
    with _CHECKERS_LOCK:
        cached = _CHECKERS.get(key)
        if cached and time.monotonic() - cached[2] < CHECKER_TTL_SECONDS:
            return cached[0]
        if cached:
            cached[1].close()
        client = make_client(None, timeout=20.0, proxy_url=fixed_proxy)
        checker = SignalChecker(client, purpose)
        _CHECKERS[key] = (checker, client, time.monotonic())
        return checker


def check_navigation(store: ProjectStore, url: str, purpose: str) -> dict:
    """{allowed, reason, skippable}: a robots.txt disallow on one detail link is skippable; TDMRep and AIPREF
    reservations, and robots errors, stop the whole collection."""
    try:
        shared_checker(purpose, proxy_url(store)).check_url(url)
    except PolicyViolation as error:
        message = str(error)
        return {"allowed": False, "reason": message, "skippable": "robots.txt disallows this URL" in message}
    return {"allowed": True, "reason": None, "skippable": False}


def navigation_signals(store: ProjectStore, purpose: str, urls: list[str]) -> list[dict]:
    checker = shared_checker(purpose, proxy_url(store))
    hosts = {urlparse(u).netloc for u in urls}
    return [info for info in checker.summary() if info["host"] in hosts]


def frontier_store(store: ProjectStore, job_id: str) -> FrontierStore:
    connection = store._connection

    def load():
        rows = connection.execute("SELECT url, depth, status FROM crawl_frontier WHERE job_id = ?", (job_id,)).fetchall()
        return [(r["url"], r["depth"]) for r in rows if r["status"] == "pending"], [r["url"] for r in rows if r["status"] != "pending"]

    def add(url: str, depth: int) -> None:
        connection.execute("INSERT OR IGNORE INTO crawl_frontier(job_id, url, depth, status, discovered_at) VALUES (?,?,?,?,?)", (job_id, url, depth, "pending", utc_now()))
        connection.commit()

    def visit(url: str, status: str) -> None:
        connection.execute("UPDATE crawl_frontier SET status = ?, visited_at = ? WHERE job_id = ? AND url = ?", (status, utc_now(), job_id, url))
        connection.commit()

    return FrontierStore(load, add, visit)


def copy_frontier(store: ProjectStore, from_job: str, to_job: str) -> None:
    """A retried crawl resumes where the failed or cancelled one stopped."""
    store._connection.execute(
        "INSERT OR IGNORE INTO crawl_frontier(job_id, url, depth, status, discovered_at, visited_at) SELECT ?, url, depth, status, discovered_at, visited_at FROM crawl_frontier WHERE job_id = ?",
        (to_job, from_job),
    )
    store._connection.commit()


# --- web archives (Track F) ----------------------------------------------------------------------

_ARCHIVE_PRESET_BASE = {
    "policy": {"requires_user_authorization_acknowledgement": True, "robots_policy": "respect"},
    "request_limits": {"max_concurrency": 1, "min_delay_ms": 1500, "max_pages_default": 200, "max_records_default": 5000, "max_duration_seconds": 1800},
    "url_scope": {"allowed_hosts": list(archives.ARCHIVE_HOSTS), "allowed_path_patterns": []},
    "strategy": {"preferred": "http", "allowed": ["http"]},
}


def make_archive_kind(presets_resolver) -> JobKind:
    def validate(store: ProjectStore, params: dict) -> dict:
        if params.get("policy_acknowledgement") is not True:
            raise JobValidationError("Confirm that you are authorized to collect this data and accept the archive's terms")
        if params.get("archive") not in ("wayback", "common_crawl"):
            raise JobValidationError("archive must be wayback or common_crawl")
        pattern = str(params.get("url_pattern", "")).strip()
        if not pattern or " " in pattern or len(pattern) > 500:
            raise JobValidationError("Enter a URL or URL pattern such as example.com/products/*")
        purpose = params.get("purpose") or get_settings(store)["default_purpose"]
        if purpose not in PURPOSES:
            raise JobValidationError("Choose a collection purpose")
        preset = presets_resolver(store, params.get("preset_id", ""), params.get("preset_version", ""))
        if preset["errors"]:
            raise JobValidationError("Preset is invalid: " + "; ".join(preset["errors"]))
        if params.get("compare") and params.get("archive") != "wayback":
            raise JobValidationError("Comparing captures over time uses the Wayback Machine")
        if params.get("compare") and not (preset.get("validation") or {}).get("unique_by"):
            raise JobValidationError("Comparing captures needs a preset with validation.unique_by, so records can be matched across versions")
        run_mode = params.get("run_mode", "test")
        limit = min(int(params.get("max_captures") or 50), 500)
        if run_mode == "test":
            # Tests read at most 10 captures; a comparison needs enough history to find two versions of a page.
            limit = min(limit, 50 if params.get("compare") else TEST_MODE_MAX_RECORDS)
        return {**params, "url_pattern": pattern, "purpose": purpose, "run_mode": run_mode, "max_captures": limit,
                "resolved_preset": {k: v for k, v in preset.items() if k not in ("errors", "health_status")}}

    def run(context: JobContext) -> dict:
        params, store = context.params, context.store
        preset = params["resolved_preset"]
        archive_preset = {**_ARCHIVE_PRESET_BASE, "id": "archive." + params["archive"], "version": "1.0.0"}
        run_id = store.create_scrape_run(context.job_id, preset["id"], preset["version"], "archive", params["url_pattern"])
        store._connection.execute("UPDATE scrape_runs SET run_mode = ?, purpose = ?, source_kind = 'archive' WHERE id = ?", (params["run_mode"], params["purpose"], run_id))
        store._connection.commit()
        allow = lambda url, _p: _archive_url_ok(url)  # noqa: E731
        scheduler = scheduler_for(archive_preset)
        candidates, warnings = [], []
        captures_read = 0
        try:
            with make_client(None, cache_dir=cache_dir(store), proxy_url=proxy_url(store)) as client:
                checker = SignalChecker(client, params["purpose"], "respect", scheduler=scheduler)

                def get(url: str, headers: dict | None = None):
                    checker.check_url(url)
                    return fetch(client, url, archive_preset, allow, scheduler, headers=headers, check_challenge=False)

                context.stage("finding_captures", {"archive": params["archive"]})
                if params["archive"] == "wayback":
                    captures = archives.parse_wayback_cdx(get(archives.wayback_query_url(params["url_pattern"], params["max_captures"], params.get("from_date"), params.get("to_date"))).text)
                else:
                    collections = archives.parse_collinfo(get(f"{archives.COMMON_CRAWL_INDEX}/collinfo.json").text)
                    crawl = next((c for c in collections if c["id"] == params.get("crawl")), collections[0]) if collections else None
                    if crawl is None:
                        raise ValueError("Common Crawl index list is empty")
                    captures = archives.parse_common_crawl_cdx(get(archives.common_crawl_query_url(crawl["cdx_api"], params["url_pattern"], params["max_captures"])).text, crawl["id"])
                captures = [c for c in captures if (c.mime or "").startswith("text/html") or not c.mime][: params["max_captures"]]
                roles: dict[tuple[str, str], str] = {}
                if params.get("compare"):
                    # Earliest and latest distinct capture of each URL: "what changed on this page since then".
                    by_url: dict[str, list] = {}
                    for capture in captures:
                        by_url.setdefault(capture.url, []).append(capture)
                    captures = []
                    for versions in by_url.values():
                        if len(versions) < 2:
                            continue
                        versions.sort(key=lambda c: c.timestamp)
                        captures += [versions[0], versions[-1]]
                        roles[(versions[0].url, versions[0].timestamp)] = "before"
                        roles[(versions[-1].url, versions[-1].timestamp)] = "after"
                    if not captures:
                        warnings.append("No URL has two different captures to compare in that range")
                for index, capture in enumerate(captures, start=1):
                    context.checkpoint()
                    if params["archive"] == "wayback":
                        response = get(archives.wayback_snapshot_url(capture))
                        body, headers = response.content, {k.lower(): v for k, v in response.headers.items()}
                    else:
                        url, range_header = archives.warc_range(capture)
                        try:
                            body, headers = archives.read_warc_record(get(url, range_header).content)
                        except PolicyViolation as error:
                            if "robots.txt" in str(error):
                                raise PolicyViolation(
                                    f"Found {len(captures)} Common Crawl captures, but data.commoncrawl.org disallows automated fetching in robots.txt, "
                                    "so DataForge will not read the WARC records. Use the Wayback Machine source, or obtain the files through Common Crawl's own download channels."
                                ) from error
                            raise
                    captures_read += 1
                    text = body.decode("utf-8", "replace")
                    page = extract_page(text, body, headers.get("content-type", "text/html"), capture.url, preset, warnings)
                    for record in page:
                        record.update(capture.provenance())
                        if roles:
                            record["archive_role"] = roles.get((capture.url, capture.timestamp), "after")
                    candidates.extend(page)
                    context.stage("page_extracted", {"page": index, "captures": len(captures), "candidates": len(page)})
                signals = checker.summary()
        except JobCancelled:
            store.fail_scrape_run(run_id, "cancelled", "Cancelled by user")
            raise
        except Exception as error:
            store.fail_scrape_run(run_id, "policy_violation" if isinstance(error, PolicyViolation) else type(error).__name__, str(error))
            raise
        archive_diff = None
        if params.get("compare"):
            unique_by = (preset.get("validation") or {}).get("unique_by") or []
            before = [c for c in candidates if c.get("archive_role") == "before"]
            after = [c for c in candidates if c.get("archive_role") == "after"]
            archive_diff = diff_records(before, after, unique_by, ignore={"archive_capture_time", "archive_digest", "archive_role", "archive", "archive_crawl"})
            candidates = after  # the staged dataset is the newest version
        valid, rejected, more = validate_candidates(candidates, preset)
        limit = TEST_MODE_MAX_RECORDS if params["run_mode"] == "test" else preset["request_limits"]["max_records_default"]
        records = valid[:limit]
        dataset_id = None
        if params["run_mode"] == "full" and records:
            dataset_id = register_staged_rows(store, store.job(context.job_id)["project_id"], records, f"archive-{params['archive']}-{context.job_id[:8]}.json",
                                              params.get("dataset_name") or f"{params['archive']} archive: {params['url_pattern'][:40]}").dataset_id
        store.complete_scrape_run(run_id, captures_read, len(records), len(rejected), len(candidates) - len(valid) - len(rejected))
        record_signals(store, run_id, signals)
        return {"run_mode": params["run_mode"], "archive": params["archive"], "captures_read": captures_read, "records_extracted": len(records),
                "records_rejected": len(rejected), "warnings": list(dict.fromkeys(warnings + more))[:50], "sample_records": records[:TEST_MODE_MAX_RECORDS],
                "dataset_id": dataset_id, "signals": signals, "stop_reason": "completed", "engine": "httpx", "archive_diff": archive_diff}

    return JobKind(run=run, validate=validate)


def _archive_url_ok(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in archives.ARCHIVE_HOSTS:
        raise PolicyViolation("Archive requests may only go to the archive's own hosts")


# --- bulk corpora: Web Data Commons N-Quads ------------------------------------------------------

def make_bulk_kind() -> JobKind:
    def validate(store: ProjectStore, params: dict) -> dict:
        path = Path(str(params.get("path", "")))
        if not path.is_absolute() or not path.is_file():
            raise JobValidationError("Choose a downloaded Web Data Commons .nq or .nq.gz file")
        types = params.get("schema_types") or []
        if not types or not all(isinstance(t, str) for t in types):
            raise JobValidationError("Choose at least one schema.org type, for example LocalBusiness")
        return {**params, "path": str(path.resolve()), "max_records": min(int(params.get("max_records") or 100_000), 1_000_000),
                "domain_suffix": str(params.get("domain_suffix") or "").lower().strip(), "run_mode": params.get("run_mode", "test")}

    def run(context: JobContext) -> dict:
        params, store = context.params, context.store
        limit = TEST_MODE_MAX_RECORDS if params["run_mode"] == "test" else params["max_records"]
        records: list[dict] = []
        pages = 0
        for page_url, entities in archives.iter_nquad_pages(archives.open_lines(Path(params["path"]))):
            pages += 1
            if pages % 2000 == 0:
                context.checkpoint()
                context.stage("reading_file", {"pages": pages, "records": len(records)})
            host = (urlparse(page_url).hostname or "").lower()
            if params["domain_suffix"] and not host.endswith(params["domain_suffix"]):
                continue
            for record in structured.entity_records(entities, params["schema_types"]):
                record.update(source_url=page_url, source_kind="web_data_commons", source_file=Path(params["path"]).name)
                records.append(record)
            if len(records) >= limit:
                break
        records = records[:limit]
        dataset_id = None
        if params["run_mode"] == "full" and records:
            dataset_id = register_staged_rows(store, store.job(context.job_id)["project_id"], records, f"wdc-{context.job_id[:8]}.json",
                                              params.get("dataset_name") or f"Web Data Commons {', '.join(params['schema_types'])}").dataset_id
        return {"run_mode": params["run_mode"], "pages_read": pages, "records_extracted": len(records), "sample_records": records[:TEST_MODE_MAX_RECORDS], "dataset_id": dataset_id}

    return JobKind(run=run, validate=validate)


# --- watches and diffs (Track I) -----------------------------------------------------------------

def create_watch(store: ProjectStore, project_id: str, payload: dict, validate_scrape) -> dict:
    params = {k: v for k, v in payload.get("params", {}).items() if k not in ("credential_secret",)}
    params["run_mode"] = "full"
    validate_scrape(store, dict(params))  # fail now, not at the first scheduled run
    interval = int(payload.get("interval_minutes", 1440))
    if interval < 15:
        raise ValueError("Watches run at most every 15 minutes")
    watch_id = str(uuid4())
    now = utc_now()
    store._connection.execute(
        "INSERT INTO watches(id, project_id, name, params_json, interval_minutes, status, next_run_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (watch_id, project_id, str(payload.get("name") or "Watch")[:120], json.dumps(params), interval, "active", now, now, now),
    )
    store._connection.commit()
    return get_watch(store, watch_id)


def get_watch(store: ProjectStore, watch_id: str) -> dict:
    row = store._connection.execute("SELECT * FROM watches WHERE id = ?", (watch_id,)).fetchone()
    if row is None:
        raise ValueError("Unknown watch")
    runs = [dict(r) | {"diff": json.loads(r["diff_json"]) if r["diff_json"] else None} for r in store._connection.execute("SELECT * FROM watch_runs WHERE watch_id = ? ORDER BY id DESC LIMIT 10", (watch_id,))]
    for run in runs:
        run.pop("diff_json", None)
    watch = dict(row)
    watch["params"] = json.loads(watch.pop("params_json"))
    watch["runs"] = runs
    return watch


def list_watches(store: ProjectStore, project_id: str) -> list[dict]:
    return [get_watch(store, row["id"]) for row in store._connection.execute("SELECT id FROM watches WHERE project_id = ? AND status != 'stopped' ORDER BY created_at", (project_id,))]


def set_watch_status(store: ProjectStore, watch_id: str, status: str) -> dict:
    if status not in ("active", "paused", "stopped"):
        raise ValueError("status must be active, paused, or stopped")
    store._connection.execute("UPDATE watches SET status = ?, updated_at = ?, next_run_at = CASE WHEN ? = 'active' THEN ? ELSE next_run_at END WHERE id = ?", (status, utc_now(), status, utc_now(), watch_id))
    store._connection.commit()
    return get_watch(store, watch_id)


def due_watches(store: ProjectStore, project_id: str) -> list[dict]:
    now = utc_now()
    return [dict(r) for r in store._connection.execute("SELECT * FROM watches WHERE project_id = ? AND status = 'active' AND (next_run_at IS NULL OR next_run_at <= ?)", (project_id, now))]


def start_watch_run(store: ProjectStore, watch: dict, submit) -> str:
    params = json.loads(watch["params_json"])
    active = store._connection.execute("SELECT 1 FROM jobs WHERE id = ? AND state IN ('validating','queued','running','paused')", (watch.get("last_job_id") or "",)).fetchone()
    next_run = (datetime.now(timezone.utc) + timedelta(minutes=int(watch["interval_minutes"]))).isoformat()
    if active:
        store._connection.execute("UPDATE watches SET next_run_at = ? WHERE id = ?", (next_run, watch["id"]))
        store._connection.commit()
        return watch["last_job_id"]
    job_id = submit({**params, "watch_id": watch["id"]})
    store._connection.execute("UPDATE watches SET last_job_id = ?, next_run_at = ?, updated_at = ? WHERE id = ?", (job_id, next_run, utc_now(), watch["id"]))
    store._connection.commit()
    return job_id


def record_watch_run(store: ProjectStore, watch_id: str, job_id: str, dataset_id: str | None, records: list[dict], preset: dict) -> dict | None:
    previous = store._connection.execute("SELECT dataset_id FROM watch_runs WHERE watch_id = ? AND dataset_id IS NOT NULL ORDER BY id DESC LIMIT 1", (watch_id,)).fetchone()
    diff = None
    unique_by = (preset.get("validation") or {}).get("unique_by") or []
    if previous and unique_by and dataset_id:
        diff = diff_datasets(store, previous["dataset_id"], dataset_id, unique_by)  # both sides as stored, so types compare equally
    elif previous and unique_by:
        diff = diff_records(diff_datasets_rows(store, previous["dataset_id"]), records, unique_by)
    store._connection.execute("INSERT INTO watch_runs(watch_id, job_id, dataset_id, previous_dataset_id, diff_json, created_at) VALUES (?,?,?,?,?,?)",
                              (watch_id, job_id, dataset_id, previous["dataset_id"] if previous else None, json.dumps(diff) if diff else None, utc_now()))
    store._connection.commit()
    return diff


def diff_datasets_rows(store: ProjectStore, dataset_id: str) -> list[dict]:
    return [json.loads(r["raw_values_json"]) for r in store._connection.execute("SELECT raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number", (dataset_id,))]


def diff_datasets(store: ProjectStore, before_id: str, after_id: str, unique_by: list[str]) -> dict:
    return diff_records(diff_datasets_rows(store, before_id), diff_datasets_rows(store, after_id), unique_by)


class WatchScheduler:
    """Runs due watches while the app is open (local-first: nothing runs when DataForge is closed)."""

    def __init__(self, service, interval_seconds: float = 30.0) -> None:
        self.service = service
        self.interval = interval_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watch-scheduler")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def tick(self) -> list[str]:
        started = []
        with self.service._lock:
            if self.service.store is None:
                return started
            store, project_id = self.service.store, self.service.project["id"]
            for watch in due_watches(store, project_id):
                try:
                    started.append(start_watch_run(store, watch, lambda params: self.service._submit("scrape", params)))
                except Exception as error:  # noqa: BLE001 - one broken watch must not stop the others
                    log("warning", "watch.run_failed", watch_id=watch["id"], error=str(error))
                    store._connection.execute("UPDATE watches SET next_run_at = ? WHERE id = ?", ((datetime.now(timezone.utc) + timedelta(minutes=int(watch["interval_minutes"]))).isoformat(), watch["id"]))
                    store._connection.commit()
        return started

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            self.tick()


# --- suggestions, detection, fingerprints --------------------------------------------------------

def page_html(payload: dict, store: ProjectStore, validate) -> tuple[str, str]:
    """Suggestion commands take page HTML from Scrape Studio (already rendered in the permitted WebView)
    or fetch one permitted page with the policy client."""
    url = str(payload.get("url", ""))
    if payload.get("html"):
        html = str(payload["html"])
        if len(html) > 5_000_000:
            raise ValueError("Page is too large to analyse")
        return html, url
    preset = payload.get("preset")
    if not isinstance(preset, dict):
        raise ValueError("Provide page html or a preset to fetch the page with")
    validate(url, preset)
    purpose = payload.get("purpose") or get_settings(store)["default_purpose"]
    with make_client(preset, get_settings(store)["contact_identity"], cache_dir=cache_dir(store), proxy_url=proxy_url(store)) as client:
        checker = SignalChecker(client, purpose, preset.get("policy", {}).get("robots_policy", "respect"))
        checker.check_url(url)
        response = fetch(client, url, preset, validate)
        checker.check_response(url, response, response.text[:100_000])
        return response.text, url


def detect_structured(html: str, url: str) -> dict:
    return structured.detect(html, url)


def suggest_selectors(html: str, url: str, examples: dict) -> dict:
    if not examples:
        raise ValueError("Type at least one example value you can see on the page")
    return suggest_from_examples(html, url, {str(k)[:40]: str(v)[:500] for k, v in examples.items()})


def propose_presets(store: ProjectStore, html: str, url: str, provider: str | None = None) -> dict:
    from dataforge_scraping import suggest

    settings = get_settings(store)["ai_suggestions"]
    provider = provider or settings["provider"]
    client = None
    if provider == "model":
        client = make_client(None, timeout=120, proxy_url=proxy_url(store))
    try:
        proposals = suggest.propose(html, url, provider, settings.get("endpoint"), settings.get("model"), bool(settings.get("remote_consent")), client)
    finally:
        if client is not None:
            client.close()
    return {"provider": provider, "proposals": proposals, "labels": {"ai_assisted": provider == "model"}}


SITE_FIELDS = {
    "marketplace": ["title", "price", "currency", "availability", "rating", "review_count", "seller", "url"],
    "real_estate": ["address", "building_name", "house_number", "street", "unit", "neighborhood", "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude", "longitude", "price", "property_type", "bedrooms", "bathrooms", "area", "parcel_or_listing_id", "agent", "url"],
    "jobs": ["title", "company", "location", "salary", "employment_type", "posted_at", "description", "url"],
    "local_directory": ["name", "category", "address", "street", "neighborhood", "district", "city", "county", "region", "country", "country_code", "postal_code", "latitude", "longitude", "phone", "rating", "review_count", "website", "url"],
    "community": ["title", "author", "score", "published_at", "community", "url"],
    "publisher": ["headline", "author", "published_at", "updated_at", "section", "summary", "url"],
    "media": ["title", "creator", "published_at", "rating", "duration", "description", "url"],
    "developer": ["name", "owner", "version", "language", "stars", "downloads", "description", "url"],
}

# Host recognition does not bypass a site's access rules. It gives automatic detection a useful
# vocabulary and field plan before selector discovery examines the fetched or rendered page.
KNOWN_SITE_PROFILES = (
    ("amazon", "Amazon", "marketplace", ("amazon.com", "amazon.ca", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.it", "amazon.es", "amazon.com.au", "amazon.co.jp", "amazon.in", "amazon.com.mx", "amazon.com.br"), True, None),
    ("ebay", "eBay", "marketplace", ("ebay.com", "ebay.co.uk", "ebay.ca", "ebay.com.au", "ebay.de", "ebay.fr", "ebay.it", "ebay.es"), True, None),
    ("walmart", "Walmart", "marketplace", ("walmart.com", "walmart.ca", "walmart.com.mx"), True, None),
    ("target", "Target", "marketplace", ("target.com",), True, None),
    ("bestbuy", "Best Buy", "marketplace", ("bestbuy.com", "bestbuy.ca"), True, None),
    ("etsy", "Etsy", "marketplace", ("etsy.com",), True, None),
    ("aliexpress", "AliExpress", "marketplace", ("aliexpress.com", "aliexpress.us"), True, None),
    ("newegg", "Newegg", "marketplace", ("newegg.com", "newegg.ca"), True, None),
    ("zillow", "Zillow", "real_estate", ("zillow.com",), True, None),
    ("realtor", "Realtor.com", "real_estate", ("realtor.com",), True, None),
    ("redfin", "Redfin", "real_estate", ("redfin.com",), True, None),
    ("trulia", "Trulia", "real_estate", ("trulia.com",), True, None),
    ("homes", "Homes.com", "real_estate", ("homes.com",), True, None),
    ("apartments", "Apartments.com", "real_estate", ("apartments.com",), True, None),
    ("loopnet", "LoopNet", "real_estate", ("loopnet.com",), True, None),
    ("har", "HAR.com", "real_estate", ("har.com",), True, None),
    ("estately", "Estately", "real_estate", ("estately.com",), True, None),
    ("weichert", "Weichert", "real_estate", ("weichert.com",), True, None),
    ("coldwell_banker", "Coldwell Banker", "real_estate", ("coldwellbankerhomes.com",), True, None),
    ("remax", "RE/MAX", "real_estate", ("remax.com",), True, None),
    ("opendoor", "Opendoor", "real_estate", ("opendoor.com",), True, None),
    ("streeteasy", "StreetEasy", "real_estate", ("streeteasy.com",), True, None),
    ("hotpads", "HotPads", "real_estate", ("hotpads.com",), True, None),
    ("apartmentguide", "ApartmentGuide", "real_estate", ("apartmentguide.com",), True, None),
    ("apartmentfinder", "ApartmentFinder", "real_estate", ("apartmentfinder.com",), True, None),
    ("landwatch", "LandWatch", "real_estate", ("landwatch.com",), True, None),
    ("realtytrac", "RealtyTrac", "real_estate", ("realtytrac.com",), True, None),
    ("showcase", "Showcase", "real_estate", ("showcase.com",), True, None),
    ("vrm", "VRM Properties (VA homes)", "real_estate", ("vrmproperties.com",), True, None),
    ("craigslist", "Craigslist", "real_estate", ("craigslist.org",), True, None),
    ("newhomesource", "NewHomeSource", "real_estate", ("newhomesource.com",), True, None),
    ("propertyshark", "PropertyShark", "real_estate", ("propertyshark.com",), True, None),
    ("indeed", "Indeed", "jobs", ("indeed.com", "indeed.co.uk", "indeed.ca", "indeed.com.au", "indeed.de", "indeed.fr", "indeed.co.in"), True, None),
    ("linkedin_jobs", "LinkedIn Jobs", "jobs", ("linkedin.com",), True, "/jobs"),
    ("glassdoor", "Glassdoor", "jobs", ("glassdoor.com", "glassdoor.co.uk", "glassdoor.ca"), True, None),
    ("ziprecruiter", "ZipRecruiter", "jobs", ("ziprecruiter.com",), True, None),
    ("wellfound", "Wellfound", "jobs", ("wellfound.com",), True, None),
    ("monster", "Monster", "jobs", ("monster.com", "monster.co.uk", "monster.ca", "monster.de", "monster.fr"), True, None),
    ("yelp", "Yelp", "local_directory", ("yelp.com", "yelp.co.uk", "yelp.ca", "yelp.com.au"), True, None),
    ("yellow_pages", "Yellow Pages", "local_directory", ("yellowpages.com", "yellowpages.ca"), True, None),
    ("tripadvisor", "Tripadvisor", "local_directory", ("tripadvisor.com", "tripadvisor.co.uk", "tripadvisor.ca"), True, None),
    ("foursquare", "Foursquare", "local_directory", ("foursquare.com", "4sq.com"), True, None),
    ("reddit", "Reddit", "community", ("reddit.com",), True, None),
    ("wikipedia", "Wikipedia", "publisher", ("wikipedia.org",), False, None),
    ("medium", "Medium", "publisher", ("medium.com",), False, None),
    ("youtube", "YouTube", "media", ("youtube.com", "youtu.be"), True, None),
    ("imdb", "IMDb", "media", ("imdb.com",), True, None),
    ("rottentomatoes", "Rotten Tomatoes", "media", ("rottentomatoes.com",), True, None),
    ("github", "GitHub", "developer", ("github.com",), True, None),
    ("stackoverflow", "Stack Overflow", "developer", ("stackoverflow.com",), False, None),
    ("producthunt", "Product Hunt", "developer", ("producthunt.com",), True, None),
    ("npm", "npm", "developer", ("npmjs.com",), True, None),
    ("major_publisher", "Major publisher", "publisher", ("nytimes.com", "washingtonpost.com", "theguardian.com", "bbc.com", "bbc.co.uk", "reuters.com", "apnews.com", "cnn.com", "bloomberg.com", "forbes.com", "wsj.com", "ft.com"), False, None),
)

# Country storefronts share the same extraction family. Keeping aliases separate from the
# profile tuple makes additions reviewable without duplicating the profile metadata.
SITE_DOMAIN_ALIASES = {
    "amazon": ("amazon.eg", "amazon.ae", "amazon.sa", "amazon.nl", "amazon.pl", "amazon.se", "amazon.com.be", "amazon.com.tr", "amazon.sg", "amazon.ie", "amazon.co.za"),
    "ebay": ("ebay.at", "ebay.be", "ebay.ch", "ebay.ie", "ebay.nl", "ebay.pl", "ebay.com.hk", "ebay.ph", "ebay.co.jp", "ebay.vn"),
    "glassdoor": ("glassdoor.com.au", "glassdoor.de", "glassdoor.fr", "glassdoor.co.in"),
    "monster": ("monster.ie", "monster.co.in", "monster.com.sg"),
    "yelp": ("yelp.de", "yelp.fr", "yelp.es", "yelp.it", "yelp.co.jp"),
    "yellow_pages": ("yellowpages.com.au",),
    "tripadvisor": ("tripadvisor.com.eg", "tripadvisor.com.au", "tripadvisor.de", "tripadvisor.fr", "tripadvisor.it", "tripadvisor.es", "tripadvisor.in", "tripadvisor.jp"),
}

SITE_EXAMPLE_URLS = {
    "amazon": "https://www.amazon.com/s?k=laptop",
    "ebay": "https://www.ebay.com/sch/i.html?_nkw=laptop",
    "walmart": "https://www.walmart.com/search?q=laptop",
    "target": "https://www.target.com/s?searchTerm=laptop",
    "bestbuy": "https://www.bestbuy.com/site/searchpage.jsp?st=laptop",
    "etsy": "https://www.etsy.com/search?q=lamp",
    "aliexpress": "https://www.aliexpress.com/w/wholesale-laptop.html",
    "newegg": "https://www.newegg.com/p/pl?d=laptop",
    "zillow": "https://www.zillow.com/houston-tx/",  # an area page: robots.txt excludes /homes/ and ?searchQueryState addresses
    "realtor": "https://www.realtor.com/realestateandhomes-search/",
    "redfin": "https://www.redfin.com/city/",
    "trulia": "https://www.trulia.com/for_sale/",
    "homes": "https://www.homes.com/homes-for-sale/",
    "apartments": "https://www.apartments.com/",
    "loopnet": "https://www.loopnet.com/search/commercial-real-estate/",
    "propertyshark": "https://www.propertyshark.com/mason/",
    "har": "https://www.har.com/houston/realestate/for_sale",
    "estately": "https://www.estately.com/TX/Houston",
    "weichert": "https://www.weichert.com/TX/Harris/Houston/",
    "coldwell_banker": "https://www.coldwellbankerhomes.com/tx/houston/",
    "remax": "https://www.remax.com/homes-for-sale/tx/houston/city/4835000",
    "opendoor": "https://www.opendoor.com/homes/houston",
    "streeteasy": "https://streeteasy.com/for-sale/nyc",
    "hotpads": "https://hotpads.com/houston-tx/apartments-for-rent",
    "apartmentguide": "https://www.apartmentguide.com/apartments/Texas/Houston/",
    "apartmentfinder": "https://www.apartmentfinder.com/Texas/Houston-Apartments",
    "landwatch": "https://www.landwatch.com/texas-land-for-sale/houston",
    "realtytrac": "https://www.realtytrac.com/houston-tx/",
    "showcase": "https://www.showcase.com/tx/houston/commercial-real-estate/for-rent/",
    "vrm": "https://www.vrmproperties.com/Properties-For-Sale?city=Houston&state=TX",
    "craigslist": "https://houston.craigslist.org/search/apa",
    "newhomesource": "https://www.newhomesource.com/communities/tx/houston-area",
    "indeed": "https://www.indeed.com/jobs?q=engineer",
    "linkedin_jobs": "https://www.linkedin.com/jobs/search/",
    "glassdoor": "https://www.glassdoor.com/Job/jobs.htm",
    "ziprecruiter": "https://www.ziprecruiter.com/jobs-search",
    "wellfound": "https://wellfound.com/jobs",
    "monster": "https://www.monster.com/jobs/search",
    "google_maps": "https://www.google.com/maps/search/cafes",
    "yelp": "https://www.yelp.com/search?find_desc=cafe",
    "yellow_pages": "https://www.yellowpages.com/search?search_terms=cafe",
    "tripadvisor": "https://www.tripadvisor.com/Search?q=cafe",
    "foursquare": "https://foursquare.com/explore",
    "reddit": "https://www.reddit.com/r/data/",
    "wikipedia": "https://en.wikipedia.org/wiki/Web_scraping",
    "medium": "https://medium.com/tag/data",
    "major_publisher": "https://www.reuters.com/world/",
    "youtube": "https://www.youtube.com/results?search_query=data",
    "imdb": "https://www.imdb.com/search/title/",
    "rottentomatoes": "https://www.rottentomatoes.com/browse/movies_at_home/",
    "github": "https://github.com/openai",
    "stackoverflow": "https://stackoverflow.com/questions",
    "producthunt": "https://www.producthunt.com/topics/developer-tools",
    "npm": "https://www.npmjs.com/search?q=react",
}


# How Scrape Studio's built-in listing collector handles a site, as tested (full, first_page, page_only, untested).
SITE_COLLECTOR_SUPPORT = {
    "zillow": ("full", "Result pages, map pins, and home details; sweep ZIP pages for more map pins. Map-move addresses are excluded by robots.txt."),
    "trulia": ("full", "Result pages and details."),
    "amazon": ("full", "Search result pages up to the last page, and product details."),
    "har": ("full", "Search pages read from the result cards; result pages, details, 120 homes a page."),
    "estately": ("full", "Result pages and details."),
    "weichert": ("full", "Result pages and details, with MLS number and year built."),
    "coldwell_banker": ("full", "Result pages and details."),
    "remax": ("first_page", "Loads later result pages in the browser: open them yourself and use Add this page."),
    "opendoor": ("full", "Result pages plus map pins."),
    "streeteasy": ("full", "Result pages and details."),
    "hotpads": ("full", "Rentals with price ranges; result pages."),
    "apartmentguide": ("full", "Rentals; result pages."),
    "apartmentfinder": ("full", "Rentals; result pages."),
    "landwatch": ("full", "Land listings; result pages."),
    "realtytrac": ("full", "Investment and foreclosure listings; result pages."),
    "showcase": ("full", "Commercial space for lease; prices are per square foot per year."),
    "vrm": ("full", "Homes the Department of Veterans Affairs is selling."),
    "craigslist": ("page_only", "Craigslist's terms forbid automated browsing: only pages you open are read."),
    "newhomesource": ("untested", "Sometimes answers with a bot check, which stops collection."),
}


def site_catalog() -> list[dict]:
    """Return the site families that automatic URL detection can recognize."""
    entries = [
        {
            "site_id": site_id,
            "site_name": name,
            "site_category": category,
            "domains": list(dict.fromkeys((*domains, *SITE_DOMAIN_ALIASES.get(site_id, ())))),
            "recommended_method": "visual_studio" if rendered else "structured_or_article",
            "suggested_fields": list(SITE_FIELDS[category]),
            "requires_rendered": rendered,
            "example_url": SITE_EXAMPLE_URLS[site_id],
            **({"collector_support": SITE_COLLECTOR_SUPPORT[site_id][0], "collector_note": SITE_COLLECTOR_SUPPORT[site_id][1]} if site_id in SITE_COLLECTOR_SUPPORT else {}),
        }
        for site_id, name, category, domains, rendered, _required_path in KNOWN_SITE_PROFILES
    ]
    entries.append({
        "site_id": "google_maps", "site_name": "Google Maps", "site_category": "local_directory",
        "domains": ["google.com", "maps.google.com"], "recommended_method": "visual_studio",
        "suggested_fields": list(SITE_FIELDS["local_directory"]), "requires_rendered": True,
        "example_url": SITE_EXAMPLE_URLS["google_maps"],
    })
    return sorted(entries, key=lambda entry: (entry["site_category"], entry["site_name"].lower()))


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def identify_known_site(url: str) -> dict | None:
    """Classify supported site families without making a network request."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    path = parsed.path.lower()
    google_host = host.startswith("google.") or host.startswith("www.google.") or host.startswith("maps.google.")
    if google_host and (host.startswith("maps.") or path == "/maps" or path.startswith("/maps/")):
        return {
            "site_id": "google_maps", "site_name": "Google Maps", "site_category": "local_directory",
            "recommended_method": "visual_studio", "suggested_fields": SITE_FIELDS["local_directory"], "requires_rendered": True,
        }
    for site_id, name, category, domains, rendered, required_path in KNOWN_SITE_PROFILES:
        recognized_domains = (*domains, *SITE_DOMAIN_ALIASES.get(site_id, ()))
        if any(_host_matches(host, domain) for domain in recognized_domains) and (not required_path or path == required_path or path.startswith(required_path + "/")):
            return {
                "site_id": site_id, "site_name": name, "site_category": category,
                "recommended_method": "visual_studio" if rendered else "structured_or_article",
                "suggested_fields": SITE_FIELDS[category], "requires_rendered": rendered,
            }
    return None


def detect_url(store: ProjectStore, url: str, purpose: str, presets: list[dict], validate) -> dict:
    """Fetch one permitted URL, identify its source type, and recommend or generate a working preset."""
    url = str(url).strip()
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter a complete http:// or https:// URL")
    if purpose not in PURPOSES:
        raise ValueError("Choose a collection purpose")
    site_profile = identify_known_site(url)
    probe = {
        "url_scope": {"allowed_hosts": [parsed.hostname], "allowed_path_patterns": []},
        "policy": {"requires_user_authorization_acknowledgement": True, "robots_policy": "respect"},
        "request_limits": {"max_concurrency": 1, "min_delay_ms": 500, "max_pages_default": 1, "max_records_default": 10, "max_duration_seconds": 60},
    }
    validate(url, probe)
    with make_client(probe, get_settings(store)["contact_identity"], cache_dir=cache_dir(store), timeout=30.0, proxy_url=proxy_url(store)) as client:
        checker = SignalChecker(client, purpose, "respect")
        checker.check_url(url)
        response = fetch(client, url, probe, validate)
        content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
        checker.check_response(url, response, response.text[:100_000] if "html" in content_type else None)

    body = response.content
    text = response.text
    prefix = body.lstrip()[:5000].lower()
    path = parsed.path.lower()
    active = [p for p in presets if not p.get("errors") and p.get("status") != "disabled"]

    def existing(preset_id: str) -> dict | None:
        return next((p for p in active if p.get("id") == preset_id), None)

    host_specific = [p for p in active if parsed.hostname in (p.get("url_scope") or {}).get("allowed_hosts", [])]
    result = {"url": url, "content_type": content_type, "confidence": "high", "reason": "", "source": "website", "preset_id": None, "preset_version": None, "draft_preset": None, "requires_rendered": False}

    if body[:5] == b"%PDF-" or "pdf" in content_type or path.endswith(".pdf"):
        choice = existing("generic.document_tables")
        result.update(source="documents", reason="PDF document detected")
    elif b"<oai-pmh" in prefix or "verb=identify" in url.lower() or re.search(r"(^|[/_.-])oai([/_.-]|$)", path):
        choice = existing("generic.oai_pmh")
        result.update(source="repository", reason="OAI-PMH repository endpoint detected")
    elif b"<urlset" in prefix or b"<sitemapindex" in prefix or "sitemap" in path:
        choice = existing("generic.sitemap_structured") or existing("generic.sitemap_article")
        result.update(source="sitemap", reason="XML sitemap detected")
    elif b"<rss" in prefix or b"<feed" in prefix or any(token in content_type for token in ("rss", "atom")):
        choice = existing("generic.feed")
        result.update(source="feed", reason="RSS or Atom feed detected")
    elif "xml" in content_type or prefix.startswith(b"<?xml") or (prefix.startswith(b"<") and path.endswith((".xml", ".soap"))):
        choice = existing("generic.xml")
        result.update(source="xml", reason="XML or SOAP records detected")
    elif "json" in content_type or prefix.startswith((b"{", b"[")) or path.endswith(".json"):
        choice = host_specific[0] if host_specific else None
        result.update(source="api", reason=f"JSON API detected{f' for {parsed.hostname}' if choice else ''}")
        if choice is None:
            result["draft_preset"] = _json_draft(json.loads(text), url)
    elif path.endswith((".csv", ".xlsx", ".xls", ".jsonl", ".ndjson", ".parquet", ".docx", ".zip", ".gz")) or content_type in ("text/csv", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/vnd.apache.parquet"):
        choice = existing("generic.document_tables")
        result.update(source="documents", reason="Downloadable data file detected")
    else:
        from dataforge_scraping import suggest

        proposals = suggest.propose_local(text, url)
        passing = [proposal for proposal in proposals if suggest.evaluate(proposal, text, url).get("passed")]
        if passing:
            result["draft_preset"] = passing[0]["preset"]
            detected = passing[0]["source"]
            result.update(source="website", reason="Structured records detected" if detected == "structured_data" else "Repeated record cards detected")
            choice = None
        else:
            from bs4 import BeautifulSoup

            soup = BeautifulSoup(text, "html.parser")
            visible_text = " ".join(soup.get_text(" ", strip=True).split())
            requires_rendered = len(visible_text) < 500 and len(soup.select("script[src], script[type='module']")) >= 2
            choice = existing("generic.html_list") if requires_rendered else existing("generic.article")
            result.update(
                source="website", confidence="medium", requires_rendered=requires_rendered,
                reason="JavaScript-rendered page detected; use the embedded Scrape Studio" if requires_rendered else "HTML page detected; article extraction is safest without repeated records",
            )

    if not site_profile and ("NewsArticle" in text or 'property="article:publisher"' in text or "property='article:publisher'" in text):
        site_profile = {
            "site_id": "publisher", "site_name": parsed.hostname, "site_category": "publisher",
            "recommended_method": "structured_or_article", "suggested_fields": SITE_FIELDS["publisher"], "requires_rendered": False,
        }
    if site_profile:
        profile_rendered = bool(site_profile.pop("requires_rendered"))
        result.update(site_profile)
        result["requires_rendered"] = bool(result["requires_rendered"] or profile_rendered)
        result["reason"] = f"{site_profile['site_category'].replace('_', ' ').title()} profile matched; {result['reason'][0].lower() + result['reason'][1:]}"
        if profile_rendered and choice and choice.get("id") == "generic.article":
            choice = existing("generic.html_list") or choice

    if choice:
        result.update(preset_id=choice["id"], preset_version=choice["version"])
    result["signals"] = checker.summary()
    return result


def _json_draft(document: object, url: str) -> dict:
    """Create a bounded capture-all API preset around the first JSON record array."""
    def find(node: object, path: str = "", depth: int = 0) -> str | None:
        if isinstance(node, list) and (not node or isinstance(node[0], dict)):
            return path
        if isinstance(node, dict) and depth < 4:
            for key, value in node.items():
                found = find(value, f"{path}.{key}".strip("."), depth + 1)
                if found is not None:
                    return found
        return None

    parsed = urlparse(url)
    slug = re.sub(r"[^a-z0-9]+", "_", (parsed.hostname or "api").lower()).strip("_")[:30] or "api"
    item_path = find(document)
    if item_path is None:
        raise ValueError("JSON response has no array of record objects")
    return {
        "id": f"custom.detected.{slug}", "version": "1.0.0", "display_name": f"Detected API: {parsed.hostname}", "page_type": "api_collection", "status": "active",
        "owner": "local-user", "description": "Generated from a URL inspection. Review and test before a full run.", "category": "custom",
        "policy": {"collection_basis": "public_api", "requires_user_authorization_acknowledgement": True, "robots_policy": "respect", "authentication": "forbidden", "captcha_or_access_challenge": "stop", "paywall_or_rate_limit": "stop", "personal_data_classification": "unknown"},
        "request_limits": {"max_concurrency": 1, "min_delay_ms": 1000, "max_pages_default": 1, "max_records_default": 1000, "max_duration_seconds": 900},
        "url_scope": {"allowed_hosts": [parsed.hostname], "allowed_path_patterns": []}, "strategy": {"preferred": "api", "allowed": ["api"]},
        "pagination": {"type": "none"}, "extraction": {"item_path": item_path, "capture_all": True, "fields": []}, "validation": {"minimum_record_coverage": 0.0, "unique_by": []},
    }


def save_fingerprints(store: ProjectStore, preset: dict, fixtures: dict[str, str]) -> list[dict]:
    """After a passing health check: remember what each field looked like, for relocation suggestions later."""
    saved = []
    for path, text in fixtures.items():
        if text.startswith("base64:") or path.endswith((".json", ".pdf")):
            continue
        prints = field_fingerprints(text, preset)
        records = [r for r in _fixture_records(text, preset)]
        coverage = _coverage(records, preset)
        store._connection.execute(
            "INSERT OR REPLACE INTO preset_fingerprints(preset_id, preset_version, fixture, fingerprints_json, coverage_json, created_at) VALUES (?,?,?,?,?,?)",
            (preset["id"], preset["version"], path, json.dumps(prints), json.dumps(coverage), utc_now()),
        )
        saved.append({"fixture": path, "fields": sorted(prints)})
    store._connection.commit()
    return saved


def health_suggestions(store: ProjectStore, preset: dict, fixtures: dict[str, str]) -> list[dict]:
    """Suggested selector fixes for a failing health check, from the newest fingerprints of any version of the preset."""
    row = store._connection.execute(
        "SELECT fingerprints_json FROM preset_fingerprints WHERE preset_id = ? ORDER BY created_at DESC LIMIT 1", (preset["id"],)
    ).fetchone()
    if row is None:
        return []
    prints = json.loads(row["fingerprints_json"])
    out = []
    for path, text in fixtures.items():
        if text.startswith("base64:") or path.endswith((".json", ".pdf")):
            continue
        for suggestion in relocation_suggestions(text, preset, prints):
            out.append({"fixture": path, **suggestion})
    return out


def fixture_from_capture(store: ProjectStore, job_id: str, url: str | None = None) -> dict:
    """Write a sanitized fixture from a job's opt-in WARC capture into the project (`project:fixtures/...`)."""
    from dataforge_scraping.fixtures import sanitize_html

    row = store._connection.execute("SELECT warc_path, preset_id FROM scrape_runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
    if row is None or not row["warc_path"] or not Path(row["warc_path"]).is_file():
        raise ValueError("That job has no page capture. Turn on page capture in Settings → Collection and run it again.")
    chosen = None
    for target, body, headers in archives.replay_warc(Path(row["warc_path"])):
        if "html" not in headers.get("content-type", "html"):
            continue
        if url is None or target == url:
            chosen = (target, body, headers)
            break
    if chosen is None:
        raise ValueError("No captured HTML page matches that URL")
    target, body, headers = chosen
    html, redactions = sanitize_html(body.decode("utf-8", "replace"))
    digest = hashlib.sha256(html.encode("utf-8")).hexdigest()[:16]
    relative = Path("fixtures") / "captured" / str(row["preset_id"]).replace(".", "_") / f"{digest}.html"
    destination = store.project_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(html, encoding="utf-8")
    return {"fixture": "project:" + relative.as_posix(), "source_url": target, "bytes": len(html.encode("utf-8")), "redactions": redactions}


def maintenance_report(store: ProjectStore, preset: dict, html: str, url: str) -> dict:
    """Compare a current page against stored fingerprints and coverage: suggested fixes and drift. Never applied."""
    rows = store._connection.execute("SELECT fingerprints_json, coverage_json FROM preset_fingerprints WHERE preset_id = ? AND preset_version = ?", (preset["id"], preset["version"])).fetchall()
    if not rows:
        raise ValueError("No fingerprints stored for this preset yet; run its health check first")
    prints, baseline = json.loads(rows[0]["fingerprints_json"]), json.loads(rows[0]["coverage_json"])
    records = _fixture_records(html, preset, url)
    current = _coverage(records, preset)
    return {"records": len(records), "coverage": current, "drift": coverage_drift(baseline, current), "suggestions": relocation_suggestions(html, preset, prints),
            "page_sha256": hashlib.sha256(html.encode()).hexdigest()}


def _fixture_records(html: str, preset: dict, url: str = "https://fixture.invalid/") -> list[dict]:
    warnings: list[str] = []
    try:
        return extract_page(html, html.encode(), "text/html", url, preset, warnings)
    except Exception:  # noqa: BLE001 - a broken selector means zero records
        return []


def _coverage(records: list[dict], preset: dict) -> dict[str, float]:
    fields = [f["key"] for f in (preset.get("extraction") or {}).get("fields", [])]
    return {key: round(sum(1 for r in records if r.get(key) not in (None, "")) / len(records), 3) if records else 0.0 for key in fields}
