"""The Overview tab: one read-only snapshot of a project - what was collected and from where, how much detail it
carries, what is running or failed, what waits for review, which presets and watches need attention, and how
much disk the project uses. Everything comes from the project database and folder; nothing is fetched."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, datasets, matching
from .storage import ProjectStore

ACTIVE_STATES = ("validating", "queued", "running", "paused")
JOB_LABELS = {"scrape": "Scrape", "scrape_rendered": "Studio scrape", "archive_query": "Archive", "bulk_import": "Bulk import",
              "dataset_import": "Import", "match": "Match", "fixture": "Test job"}
STORAGE_AREAS = {"artifacts": "Imported and staged files", "cache": "HTTP cache", "captures": "WARC captures", "exports": "Exports",
                 "downloads": "Linked files", "engine": "Scrapy engine state", "backups": "Database backups", "fixtures": "Captured fixtures"}


def _parse(timestamp: str | None) -> datetime | None:
    if not timestamp:
        return None
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _folder_size(path: Path, limit: int = 200_000) -> int:
    total, seen = 0, 0
    stack = [path]
    while stack and seen < limit:
        try:
            entries = list(os.scandir(stack.pop()))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


def _job_line(job: dict) -> str:
    """A one-line summary of a job's outcome for lists."""
    result = job.get("result") or {}
    if job["state"] == "failed":
        return str(job.get("error") or "Failed")[:200]
    if job["kind"] in ("scrape", "scrape_rendered", "archive_query", "bulk_import"):
        parts = [f"{result.get('records_extracted', 0):,} records"]
        pages = ((result.get("details") or {}).get("detail_pages") or {}).get("fetched")
        if pages:
            parts.append(f"{pages:,} detail pages")
        if result.get("run_mode") == "test":
            parts.append("test")
        return " · ".join(parts)
    if job["kind"] == "match":
        metrics = result.get("metrics") or {}
        decisions = metrics.get("decisions") or {}
        return f"{metrics.get('input_rows', 0):,} rows · {decisions.get('match', 0):,} matches · {decisions.get('possible_match', 0):,} to review"
    if job["kind"] == "dataset_import":
        return f"{result.get('row_count', 0):,} rows"
    return job["state"]


def build(store: ProjectStore, project: dict, presets: list[dict], days: int = 14, tz_offset_minutes: int = 0) -> dict:
    connection = store._connection
    project_id = project["id"]
    now = datetime.now(timezone.utc)
    local = timedelta(minutes=-int(tz_offset_minutes))  # JavaScript's getTimezoneOffset is UTC minus local
    today = (now + local).date()
    day_keys = [(today - timedelta(days=offset)).isoformat() for offset in range(days - 1, -1, -1)]
    activity = {day: {"date": day, "records": 0, "runs": 0, "failed": 0, "detail_pages": 0} for day in day_keys}

    runs = [dict(row) for row in connection.execute(
        """SELECT r.*, j.state AS job_state, j.result_json FROM scrape_runs r JOIN jobs j ON j.id = r.job_id
           WHERE j.project_id = ? ORDER BY r.created_at DESC""", (project_id,))]
    errors = {row["scrape_run_id"]: row["message"] for row in connection.execute(
        "SELECT scrape_run_id, message FROM scrape_errors WHERE scrape_run_id IN (SELECT r.id FROM scrape_runs r JOIN jobs j ON j.id = r.job_id WHERE j.project_id = ?)",
        (project_id,))}
    collection = {"runs": len(runs), "full_runs": 0, "test_runs": 0, "failed_runs": 0, "records": 0, "records_last_7_days": 0, "records_previous_7_days": 0,
                  "pages": 0, "detail_pages": 0, "hosts": 0}
    by_source: dict[str, dict] = {}
    by_host: dict[str, dict] = {}
    by_preset: dict[str, dict] = {}
    week_ago, two_weeks_ago = now - timedelta(days=7), now - timedelta(days=14)
    for run in runs:
        created = _parse(run["created_at"])
        full = run.get("run_mode", "full") == "full"
        completed = run["status"] == "completed"
        collection["full_runs" if full else "test_runs"] += 1
        if run["status"] == "failed":
            collection["failed_runs"] += 1
        if created is not None:
            day = (created + local).date().isoformat()
            if day in activity:
                activity[day]["runs"] += 1
                activity[day]["failed"] += run["status"] == "failed"
                if full and completed:
                    activity[day]["records"] += run["records_extracted"]
                    activity[day]["detail_pages"] += run.get("detail_pages") or 0
        if not (full and completed):
            continue
        records = run["records_extracted"]
        collection["records"] += records
        collection["pages"] += run["pages_fetched"]
        collection["detail_pages"] += run.get("detail_pages") or 0
        if created is not None and created >= week_ago:
            collection["records_last_7_days"] += records
        elif created is not None and created >= two_weeks_ago:
            collection["records_previous_7_days"] += records
        source = run.get("source_kind") or "website"
        entry = by_source.setdefault(source, {"source": source, "records": 0, "runs": 0})
        entry["records"] += records
        entry["runs"] += 1
        host = urlparse(run["start_url"]).hostname or run["start_url"][:60] or "—"
        entry = by_host.setdefault(host, {"host": host, "records": 0, "runs": 0, "last_run": run["created_at"]})
        entry["records"] += records
        entry["runs"] += 1
        entry = by_preset.setdefault(run["preset_id"], {"preset_id": run["preset_id"], "version": run["preset_version"], "records": 0, "runs": 0, "last_run": run["created_at"]})
        entry["records"] += records
        entry["runs"] += 1
    collection["hosts"] = len(by_host)

    recent_runs = []
    for run in runs[:8]:
        result = json.loads(run["result_json"]) if run.get("result_json") else {}
        recent_runs.append({
            "job_id": run["job_id"], "preset_id": run["preset_id"], "preset_version": run["preset_version"], "source_kind": run.get("source_kind"),
            "host": urlparse(run["start_url"]).hostname or "", "run_mode": run.get("run_mode"), "status": run["status"], "engine": run.get("engine"),
            "records": run["records_extracted"], "pages": run["pages_fetched"], "detail_pages": run.get("detail_pages") or 0,
            "detail_level": run.get("detail_level"), "detail_fields": run.get("detail_fields") or 0, "created_at": run["created_at"],
            "dataset_id": result.get("dataset_id"), "stop_reason": result.get("stop_reason"), "error": errors.get(run["id"]),
        })

    # datasets, mapping, and review progress (latest completed full match per dataset)
    all_datasets = datasets.list_datasets(store, project_id)
    latest_match = {row["dataset_id"]: row["job_id"] for row in connection.execute(
        """SELECT r.dataset_id, r.job_id FROM match_runs r JOIN jobs j ON j.id = r.job_id
           WHERE j.project_id = ? AND r.run_mode = 'full' AND j.state = 'completed' ORDER BY j.created_at""", (project_id,))}
    review = {"pending": 0, "reviewed": 0, "safe_matches": 0, "canonical_records": 0, "matched_datasets": 0}
    dataset_rows = []
    for dataset in all_datasets:
        entry = {k: dataset[k] for k in ("id", "name", "kind", "row_count", "column_count", "created_at", "mapping_version")}
        job_id = latest_match.get(dataset["id"])
        if job_id:
            summary = matching.results(store, job_id)
            reviewed = sum(summary["reviewed"].values())
            entry.update(match_job_id=job_id, pending_review=summary["pending_review"], reviewed=reviewed, canonical_records=summary["canonical_records"])
            review["pending"] += summary["pending_review"]
            review["reviewed"] += reviewed
            review["safe_matches"] += summary["decisions"].get("match", 0)
            review["canonical_records"] += summary["canonical_records"]
            review["matched_datasets"] += 1
        dataset_rows.append(entry)
    exports = connection.execute(
        "SELECT COUNT(*) AS n, MAX(e.created_at) AS last FROM exports e JOIN jobs j ON j.id = e.job_id WHERE j.project_id = ?", (project_id,)).fetchone()

    # jobs
    jobs = [dict(row) for row in store.list_jobs(project_id, 200)]
    for job in jobs:
        job["params"] = json.loads(job.pop("params_json") or "{}")
        job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
    job_view = lambda job: {"id": job["id"], "kind": job["kind"], "label": JOB_LABELS.get(job["kind"], job["kind"]), "state": job["state"],  # noqa: E731
                            "created_at": job["created_at"], "updated_at": job["updated_at"], "summary": _job_line(job),
                            "dataset_id": (job["result"] or {}).get("dataset_id") or job["params"].get("dataset_id")}
    recent_failed = [job_view(j) for j in jobs if j["state"] == "failed" and (_parse(j["updated_at"]) or now) >= week_ago]
    job_counts = {state: sum(1 for j in jobs if j["state"] == state) for state in ("completed", "failed", "cancelled", *ACTIVE_STATES)}

    # watches
    watch_rows = [dict(row) for row in connection.execute("SELECT * FROM watches WHERE project_id = ? ORDER BY next_run_at", (project_id,))]
    watches = []
    for watch in watch_rows:
        last = connection.execute("SELECT w.diff_json, j.state FROM watch_runs w JOIN jobs j ON j.id = w.job_id WHERE w.watch_id = ? ORDER BY w.id DESC LIMIT 1",
                                  (watch["id"],)).fetchone()
        last_job = connection.execute("SELECT state FROM jobs WHERE id = ?", (watch["last_job_id"],)).fetchone() if watch.get("last_job_id") else None
        diff = json.loads(last["diff_json"]) if last and last["diff_json"] else None
        watches.append({"id": watch["id"], "name": watch["name"], "status": watch["status"], "interval_minutes": watch["interval_minutes"],
                        "next_run_at": watch["next_run_at"], "last_state": last_job["state"] if last_job else None, "last_changes": (diff or {}).get("counts")})

    # presets
    preset_counts = {status: 0 for status in ("active", "degraded", "disabled", "deprecated")}
    attention_presets = []
    for preset in presets:
        status = preset.get("status", "active")
        preset_counts[status] = preset_counts.get(status, 0) + 1
        if status in ("degraded", "disabled") and len(attention_presets) < 6:
            failures = (preset.get("health_status") or {}).get("failures") or []
            attention_presets.append({"id": preset["id"], "version": preset["version"], "display_name": preset.get("display_name", preset["id"]),
                                      "status": status, "reason": failures[0] if failures else None})
    custom_presets = sum(1 for p in presets if p.get("source") == "custom")

    # sites that limit collection (latest signals per host)
    limits = []
    for row in connection.execute(
            """SELECT s.host, s.robots_status, s.tdm_reservation, s.signals_json, MAX(s.checked_at) AS checked_at FROM usage_signals s
               JOIN scrape_runs r ON r.id = s.scrape_run_id JOIN jobs j ON j.id = r.job_id WHERE j.project_id = ? GROUP BY s.host""", (project_id,)):
        reasons = []
        if row["robots_status"] in ("forbidden", "unreachable"):
            reasons.append("robots.txt unavailable, treated as disallow-all")
        if row["tdm_reservation"] == 1:
            reasons.append("reserves text-and-data-mining rights (TDMRep)")
        signals = json.loads(row["signals_json"] or "{}")
        if signals.get("crawl_delay"):
            reasons.append(f"asks for {signals['crawl_delay']:g} s between requests")
        if reasons:
            limits.append({"host": row["host"], "reasons": reasons, "checked_at": row["checked_at"]})

    # storage
    root = Path(project["root_path"])
    database = sum(p.stat().st_size for p in root.glob("*.sqlite3*") if p.is_file()) if root.is_dir() else 0
    areas = [{"area": area, "label": label, "bytes": _folder_size(root / area)} for area, label in STORAGE_AREAS.items() if (root / area).is_dir()]
    areas = [a for a in areas if a["bytes"]]
    storage = {"database_bytes": database, "areas": sorted(areas, key=lambda a: -a["bytes"]), "total_bytes": database + sum(a["bytes"] for a in areas)}

    # what needs the user, most urgent first
    attention = []
    if recent_failed:
        attention.append({"kind": "failed_jobs", "severity": "critical", "count": len(recent_failed), "target": "scraping",
                          "message": f"{len(recent_failed)} job{'s' if len(recent_failed) != 1 else ''} failed in the last 7 days", "detail": recent_failed[0]["summary"]})
    for preset in attention_presets:
        attention.append({"kind": "preset", "severity": "critical" if preset["status"] == "disabled" else "warning", "count": 1, "target": "settings",
                          "message": f"{preset['display_name']} is {preset['status']}", "detail": preset["reason"]})
    if review["pending"]:
        attention.append({"kind": "review", "severity": "warning", "count": review["pending"], "target": "match",
                          "message": f"{review['pending']:,} possible match{'es' if review['pending'] != 1 else ''} wait for review", "detail": None})
    unmapped = [d for d in dataset_rows if not d["mapping_version"]]
    if unmapped:
        attention.append({"kind": "mapping", "severity": "info", "count": len(unmapped), "target": "datasets", "dataset_id": unmapped[0]["id"],
                          "message": f"{len(unmapped)} dataset{'s' if len(unmapped) != 1 else ''} need{'' if len(unmapped) != 1 else 's'} a confirmed mapping before matching",
                          "detail": ", ".join(d["name"] for d in unmapped[:3])})
    failing_watches = [w for w in watches if w["last_state"] == "failed" and w["status"] == "active"]
    if failing_watches:
        attention.append({"kind": "watch", "severity": "warning", "count": len(failing_watches), "target": "scraping",
                          "message": f"{len(failing_watches)} watch{'es' if len(failing_watches) != 1 else ''} failed on the last run", "detail": failing_watches[0]["name"]})
    paused = [j for j in jobs if j["state"] == "paused"]
    if paused:
        attention.append({"kind": "paused", "severity": "info", "count": len(paused), "target": "scraping",
                          "message": f"{len(paused)} job{'s are' if len(paused) != 1 else ' is'} paused", "detail": JOB_LABELS.get(paused[0]["kind"], paused[0]["kind"])})

    has_match = bool(latest_match) or any(j["kind"] == "match" and j["state"] == "completed" for j in jobs)
    checklist = [
        {"id": "collect", "label": "Import a file or collect from a website", "done": bool(all_datasets), "target": "scraping"},
        {"id": "details", "label": "Collect with Record detail: Full for item pages", "done": any(r.get("detail_pages") for r in runs), "target": "scraping"},
        {"id": "mapping", "label": "Confirm a dataset's field mapping", "done": any(d["mapping_version"] for d in dataset_rows), "target": "datasets"},
        {"id": "match", "label": "Run matching and deduplication", "done": has_match, "target": "match"},
        {"id": "review", "label": "Review uncertain matches", "done": has_match and review["pending"] == 0 and review["reviewed"] > 0, "target": "match"},
        {"id": "export", "label": "Export traceable results", "done": bool(exports["n"]), "target": "match"},
    ]

    return {
        "generated_at": now.isoformat(),
        "app": {"version": __version__},
        "project": {"id": project_id, "name": project["name"], "root_path": project["root_path"], "created_at": project.get("created_at")},
        "collection": collection,
        "activity": list(activity.values()),
        "by_source": sorted(by_source.values(), key=lambda s: -s["records"]),
        "top_hosts": sorted(by_host.values(), key=lambda h: -h["records"])[:6],
        "top_presets": sorted(by_preset.values(), key=lambda p: -p["records"])[:6],
        "recent_runs": recent_runs,
        "datasets": {"count": len(all_datasets), "rows": sum(d["row_count"] for d in all_datasets), "scraped": sum(d["kind"] == "scrape" for d in all_datasets),
                     "imported": sum(d["kind"] != "scrape" for d in all_datasets), "unmapped": len(unmapped), "items": dataset_rows[:8]},
        "review": {**review, "exports": exports["n"], "last_export_at": exports["last"]},
        "jobs": {"counts": job_counts, "active": [job_view(j) for j in jobs if j["state"] in ACTIVE_STATES][:6], "recent_failed": recent_failed[:5],
                 "recent": [job_view(j) for j in jobs[:6]]},
        "watches": {"active": sum(w["status"] == "active" for w in watches), "paused": sum(w["status"] == "paused" for w in watches), "items": watches[:6]},
        "presets": {"counts": preset_counts, "total": len(presets), "custom": custom_presets, "attention": attention_presets},
        "site_limits": limits[:6],
        "storage": storage,
        "attention": attention,
        "checklist": checklist,
    }
