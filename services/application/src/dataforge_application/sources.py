"""Services for the expanded source types: project settings (contact identity, consents, capture), usage
signal records, crawl frontiers, web archive and bulk corpus jobs, watches with diffs, selector suggestions,
structured-data detection, AI draft proposals, and preset fingerprints."""

from __future__ import annotations

import hashlib
import json
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
    with make_client(None) as client:
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


_CHECKERS: dict[str, tuple[SignalChecker, object, float]] = {}
_CHECKERS_LOCK = threading.Lock()
CHECKER_TTL_SECONDS = 600


def shared_checker(purpose: str) -> SignalChecker:
    """Signals for navigation checks outside collection jobs (Scrape Studio). Cached per purpose for ten
    minutes, so each host's robots.txt, TDMRep, and ai.txt are read once per session, not once per page."""
    import time

    if purpose not in PURPOSES:
        raise ValueError(f"purpose must be one of {', '.join(PURPOSES)}")
    with _CHECKERS_LOCK:
        cached = _CHECKERS.get(purpose)
        if cached and time.monotonic() - cached[2] < CHECKER_TTL_SECONDS:
            return cached[0]
        if cached:
            cached[1].close()
        client = make_client(None, timeout=20.0)
        checker = SignalChecker(client, purpose)
        _CHECKERS[purpose] = (checker, client, time.monotonic())
        return checker


def check_navigation(url: str, purpose: str) -> dict:
    """{allowed, reason, skippable}: a robots.txt disallow on one detail link is skippable; TDMRep and AIPREF
    reservations, and robots errors, stop the whole collection."""
    try:
        shared_checker(purpose).check_url(url)
    except PolicyViolation as error:
        message = str(error)
        return {"allowed": False, "reason": message, "skippable": "robots.txt disallows this URL" in message}
    return {"allowed": True, "reason": None, "skippable": False}


def navigation_signals(purpose: str, urls: list[str]) -> list[dict]:
    checker = shared_checker(purpose)
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
            with make_client(None, cache_dir=cache_dir(store)) as client:
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
    with make_client(preset, get_settings(store)["contact_identity"], cache_dir=cache_dir(store)) as client:
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
        client = make_client(None, timeout=120)
    try:
        proposals = suggest.propose(html, url, provider, settings.get("endpoint"), settings.get("model"), bool(settings.get("remote_consent")), client)
    finally:
        if client is not None:
            client.close()
    return {"provider": provider, "proposals": proposals, "labels": {"ai_assisted": provider == "model"}}


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
