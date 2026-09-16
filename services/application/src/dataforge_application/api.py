"""Versioned command API shared by the desktop host (stdio) and the browser dev bridge (HTTP).

Request:  {"schema_version": 1, "command": "dataset.list", "payload": {...}}
Response: {"schema_version": 1, "ok": true, "result": ...} or {"schema_version": 1, "ok": false, "error": {"code", "message"}}
Long-running commands return a job_id immediately; progress is read from durable job events.
"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from . import SCHEMA_VERSION
from . import datasets, matching, projects, scraping, sources
from .contracts import health_check
from .jobs import JobContext, JobKind, JobRunner, JobValidationError, fixture_job
from .logs import log
from .storage import ProjectStore

REPO_ROOT = Path(__file__).resolve().parents[4]


class CommandError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _job_dict(row) -> dict:
    job = dict(row)
    job["params"] = json.loads(job.pop("params_json") or "{}")
    job["params"].pop("resolved_preset", None)  # large; available through preset.list
    job["result"] = json.loads(job.pop("result_json")) if job.get("result_json") else None
    return job


class Service:
    def __init__(self, migrations_dir: Path | None = None, presets_dir: Path | None = None, start_watch_scheduler: bool = False) -> None:
        self.start_watch_scheduler = start_watch_scheduler
        self.migrations_dir = migrations_dir or REPO_ROOT / "migrations"
        self.presets_dir = presets_dir or REPO_ROOT / "packages" / "presets"
        self.store: ProjectStore | None = None
        self.project: dict | None = None
        self.runner: JobRunner | None = None
        self._lock = threading.RLock()
        self.watch_scheduler: sources.WatchScheduler | None = None
        self.commands: dict[str, Callable[[dict], object]] = {
            "health.check": lambda p: health_check().to_dict(),
            "project.create": lambda p: self._open(p.get("path", ""), p.get("name"), create=True),
            "project.open": lambda p: self._open(p.get("path", "")),
            "project.current": lambda p: self.project,
            "project.recent": lambda p: projects.recent_projects(),
            "project.summary": self._project_summary,
            "dataset.import": self._dataset_import,
            "dataset.list": lambda p: datasets.list_datasets(self._store(), self._project_id()),
            "dataset.rows": lambda p: datasets.dataset_rows(self._store(), p["dataset_id"], int(p.get("offset", 0)), min(int(p.get("limit", 50)), 500)),
            "dataset.profile": self._dataset_profile,
            "dataset.mapping": self._dataset_mapping,
            "dataset.confirm_mapping": lambda p: asdict(datasets.confirm_mapping(self._store(), p["dataset_id"], p["mapping"], p.get("entity_type"), p.get("export_exclude"))),
            "dataset.mapping_flags": lambda p: matching.mapping_flags(self._store(), p["dataset_id"]),
            "dataset.delete": lambda p: datasets.delete_dataset(self._store(), p["dataset_id"]) or {"deleted": p["dataset_id"]},
            "job.list": lambda p: [_job_dict(r) for r in self._store().list_jobs(self._project_id(), int(p.get("limit", 50)))],
            "job.get": self._job_get,
            "job.start_fixture": lambda p: {"job_id": self._submit("fixture", {"steps": int(p.get("steps", 8)), "step_seconds": float(p.get("step_seconds", 0.75))})},
            "job.cancel": lambda p: self._runner().cancel(self._store(), p["job_id"]) or {"job_id": p["job_id"]},
            "job.pause": lambda p: self._runner().pause(p["job_id"]) or {"job_id": p["job_id"]},
            "job.resume": lambda p: self._runner().resume(p["job_id"]) or {"job_id": p["job_id"]},
            "job.retry": lambda p: {"job_id": self._runner().retry(self._store(), p["job_id"], self._secrets(p))},
            "preset.list": lambda p: scraping.list_presets(self._store(), self.presets_dir),
            "preset.validate": lambda p: {"errors": scraping.validate_preset(p["preset"])},
            "preset.save_custom": lambda p: scraping.save_custom_preset(self._store(), self.presets_dir, p["preset"]),
            "preset.health_check": lambda p: scraping.run_health_checks(self._store(), self.presets_dir, p.get("preset_id")),
            "preset.packages": lambda p: scraping.list_packages(self._store()),
            "preset.install_package": lambda p: scraping.install_package(self._store(), self.presets_dir, p["path"]),
            "preset.rollback_package": lambda p: scraping.rollback_package(self._store(), p["name"]),
            "preset.export_custom": lambda p: scraping.export_custom_preset(self._store(), p["preset_id"], p["preset_version"]),
            "preset.import_custom": lambda p: scraping.import_custom_preset(self._store(), self.presets_dir, p["document"]),
            "scrape.create_job": lambda p: {"job_id": self._submit("scrape", p, self._secrets(p))},
            "scrape.check_url": self._scrape_check_url,
            "scrape.stage_rendered": lambda p: {"job_id": self._submit("scrape_rendered", p)},
            "scrape.check_signals": lambda p: sources.check_signals(self._store(), p["url"], p.get("purpose") or sources.get_settings(self._store())["default_purpose"]),
            "scrape.run_signals": lambda p: sources.run_signals(self._store(), self._scrape_run_id(p["job_id"])),
            "scrape.detect_structured": lambda p: sources.detect_structured(*sources.page_html(p, self._store(), scraping.validate_url)),
            "scrape.suggest_selectors": lambda p: sources.suggest_selectors(*sources.page_html(p, self._store(), scraping.validate_url), p.get("examples") or {}),
            "scrape.propose_presets": lambda p: sources.propose_presets(self._store(), *sources.page_html(p, self._store(), scraping.validate_url), p.get("provider")),
            "preset.maintenance_report": self._maintenance_report,
            "archive.create_job": lambda p: {"job_id": self._submit("archive_query", p)},
            "bulk.create_job": lambda p: {"job_id": self._submit("bulk_import", p)},
            "watch.create": lambda p: sources.create_watch(self._store(), self._project_id(), p, scraping.make_scrape_kind(self.presets_dir).validate),
            "watch.list": lambda p: sources.list_watches(self._store(), self._project_id()),
            "watch.get": lambda p: sources.get_watch(self._store(), p["watch_id"]),
            "watch.set_status": lambda p: sources.set_watch_status(self._store(), p["watch_id"], p["status"]),
            "watch.run_now": self._watch_run_now,
            "dataset.diff": lambda p: sources.diff_datasets(self._store(), p["before_dataset_id"], p["after_dataset_id"], list(p["unique_by"])),
            "settings.get": lambda p: sources.get_settings(self._store()),
            "settings.update": lambda p: sources.set_settings(self._store(), p.get("changes") or {}),
            "cache.purge": lambda p: sources.purge_cache(self._store()),
            "match.create_job": lambda p: {"job_id": self._submit("match", p)},
            "match.results": lambda p: matching.results(self._store(), p["job_id"]),
            "match.review_queue": lambda p: matching.review_queue(self._store(), p["job_id"], int(p.get("offset", 0)), min(int(p.get("limit", 20)), 100), p.get("order", "score")),
            "match.train_ranking": lambda p: matching.train_ranking(self._store(), p["job_id"]),
            "match.clusters": lambda p: matching.list_clusters(self._store(), p["job_id"], int(p.get("offset", 0)), min(int(p.get("limit", 20)), 100)),
            "match.split_cluster": lambda p: matching.split_cluster(self._store(), p["job_id"], p["cluster_id"], list(p["row_ids"])),
            "match.lock_cluster": lambda p: matching.lock_cluster(self._store(), p["job_id"], p["cluster_id"]),
            "match.undo_cluster_action": lambda p: matching.undo_cluster_action(self._store(), p["job_id"], p["cluster_action_id"]),
            "match.submit_review": lambda p: matching.submit_review(self._store(), p["job_id"], p["decision_id"], p["action"], int(p["expected_version"]), p.get("values")),
            "match.set_canonical_value": lambda p: matching.set_canonical_value(self._store(), p["job_id"], p["cluster_id"], p["column"], p["row_id"]),
            "match.undo_canonical_value": lambda p: matching.undo_canonical_value(self._store(), p["job_id"], p["override_id"]),
            "match.flag_mapping": lambda p: matching.flag_mapping(self._store(), p["job_id"], p["column"], str(p.get("note", "")), p.get("decision_id")),
            "match.undo_review": lambda p: matching.undo_review(self._store(), p["job_id"], p["review_action_id"]),
            "match.review_history": lambda p: matching.review_history(self._store(), p["job_id"]),
            "export.create": lambda p: matching.create_export(self._store(), p["job_id"], bool(p.get("include_provenance", True)), bool(p.get("allow_unresolved", False))),
        }

    # --- plumbing -------------------------------------------------------------------------------
    def handle(self, request: dict) -> dict:
        command = request.get("command")
        try:
            if request.get("schema_version", SCHEMA_VERSION) != SCHEMA_VERSION:
                raise CommandError("unsupported_schema", f"Unsupported schema_version {request.get('schema_version')}")
            handler = self.commands.get(command)
            if handler is None:
                raise CommandError("unknown_command", f"Unknown command {command!r}")
            with self._lock:
                result = handler(request.get("payload") or {})
            return {"schema_version": SCHEMA_VERSION, "ok": True, "result": result}
        except CommandError as error:
            code, message = error.code, str(error)
        except matching.ReviewConflict as error:
            code, message = "review_conflict", str(error)
        except (JobValidationError, ValueError) as error:
            code, message = "invalid_request", str(error)
        except KeyError as error:
            code, message = "invalid_request", f"Missing field {error}"
        except Exception as error:  # noqa: BLE001
            log("error", "command.failed", command=command, error=type(error).__name__, message=str(error))
            code, message = "internal_error", f"{type(error).__name__}: {error}"
        return {"schema_version": SCHEMA_VERSION, "ok": False, "error": {"code": code, "message": message}}

    def _store(self) -> ProjectStore:
        if self.store is None:
            raise CommandError("no_project", "Open or create a project first")
        return self.store

    def _project_id(self) -> str:
        self._store()
        return self.project["id"]

    def _runner(self) -> JobRunner:
        self._store()
        return self.runner

    def _submit(self, kind: str, params: dict, secrets: dict | None = None) -> str:
        return self._runner().submit(self._store(), self._project_id(), kind, params, secrets)

    @staticmethod
    def _secrets(payload: dict) -> dict:
        """The host injects credential_secret from the OS credential store; it must never reach job params or logs."""
        secret = payload.pop("credential_secret", None)
        return {"credential": secret} if secret else {}

    def _scrape_check_url(self, payload: dict) -> dict:
        """Scope check before Scrape Studio navigates the embedded WebView."""
        preset = payload.get("preset")
        if not isinstance(preset, dict):
            preset = scraping.resolve_preset(self._store(), self.presets_dir, payload["preset_id"], payload["preset_version"])
        if "webview" not in preset.get("strategy", {}).get("allowed", []):
            return {"allowed": False, "reason": "This preset does not allow embedded WebView rendering"}
        try:
            scraping.validate_url(payload["url"], scraping.resolve_for_url(preset, payload.get("scope_url") or payload["url"]))
        except (scraping.PolicyViolation, ValueError) as error:
            return {"allowed": False, "reason": str(error)}
        return {"allowed": True, "reason": None}

    def _scrape_run_id(self, job_id: str) -> str:
        row = self._store()._connection.execute("SELECT id FROM scrape_runs WHERE job_id = ? ORDER BY created_at DESC LIMIT 1", (job_id,)).fetchone()
        if row is None:
            raise CommandError("not_found", "That job has no scrape run")
        return row["id"]

    def _watch_run_now(self, payload: dict) -> dict:
        store = self._store()
        watch = store._connection.execute("SELECT * FROM watches WHERE id = ?", (payload["watch_id"],)).fetchone()
        if watch is None:
            raise CommandError("not_found", "Unknown watch")
        return {"job_id": sources.start_watch_run(store, dict(watch), lambda params: self._submit("scrape", params))}

    def _maintenance_report(self, payload: dict) -> dict:
        store = self._store()
        preset = scraping.resolve_preset(store, self.presets_dir, payload["preset_id"], payload["preset_version"])
        html, url = sources.page_html({**payload, "preset": scraping.resolve_for_url(preset, payload.get("url") or "https://fixture.invalid/")} if not payload.get("html") else payload, store, scraping.validate_url)
        return sources.maintenance_report(store, preset, html, url)

    def _open(self, path: str, name: str | None = None, create: bool = False) -> dict:
        if self.runner and any(self.runner._controls):
            raise CommandError("jobs_active", "Wait for active jobs to finish or cancel them before switching projects")
        store, project = projects.open_project(path, self.migrations_dir, name, create)
        if self.store:
            self.store.close()
        self.store, self.project = store, project
        recovered = store.recover_interrupted_jobs()
        self.runner = JobRunner(store.database_path, {
            "fixture": JobKind(run=fixture_job),
            "dataset_import": JobKind(run=self._run_import, validate=self._validate_import),
            "scrape": scraping.make_scrape_kind(self.presets_dir),
            "archive_query": sources.make_archive_kind(lambda store, pid, ver: scraping.resolve_preset(store, self.presets_dir, pid, ver)),
            "bulk_import": sources.make_bulk_kind(),
            "scrape_rendered": scraping.make_rendered_kind(self.presets_dir),
            "match": matching.MATCH_JOB,
        })
        sources.purge_expired_captures(store)
        if self.watch_scheduler is None and self.start_watch_scheduler:
            self.watch_scheduler = sources.WatchScheduler(self)
            self.watch_scheduler.start()
        return {**project, "recovered_jobs": recovered}

    def open_recent(self) -> None:
        recent = projects.recent_projects()
        if recent:
            try:
                self._open(recent[0]["root_path"])
            except Exception as error:  # noqa: BLE001 - startup must still succeed
                log("warning", "project.reopen_failed", error=str(error))

    # --- commands -------------------------------------------------------------------------------
    @staticmethod
    def _validate_import(store: ProjectStore, params: dict) -> dict:
        path = Path(str(params.get("path", "")))
        if path.suffix.lower() not in (".csv", ".json", ".xlsx"):
            raise JobValidationError("Supported import formats are CSV, JSON, and XLSX")
        if not path.is_absolute() or not path.is_file():
            raise JobValidationError("Import path must be an absolute path to an existing file")
        return {**params, "path": str(path.resolve())}

    @staticmethod
    def _run_import(context: JobContext) -> dict:
        context.stage("reading_file")
        project_id = context.store.job(context.job_id)["project_id"]
        imported = datasets.import_file(context.store, project_id, Path(context.params["path"]), context.params.get("name"))
        return asdict(imported)

    def _dataset_import(self, payload: dict) -> dict:
        return {"job_id": self._submit("dataset_import", payload)}

    def _dataset_profile(self, payload: dict) -> dict:
        store = self._store()
        profiles = datasets.profile_dataset(store, payload["dataset_id"])
        proposals = {p.source_field: p for p in datasets.propose_field_mappings([p.name for p in profiles])}
        return {
            "columns": [{
                "name": p.name, "null_rate": round(p.null_rate, 4), "distinct_count": p.distinct_count, "sample_values": list(p.sample_values),
                "proposed_role": proposals[p.name].role, "confidence": proposals[p.name].confidence, "candidates": list(proposals[p.name].candidates),
            } for p in profiles],
            "sensitive_roles": sorted(datasets.SENSITIVE_ROLES),
        }

    def _dataset_mapping(self, payload: dict) -> dict | None:
        row = datasets.latest_mapping(self._store(), payload["dataset_id"])
        return None if row is None else {"id": row["id"], "version": row["version"], "mapping": json.loads(row["mapping_json"]), "entity_type": row["entity_type"], "export_exclude": json.loads(row["export_exclude_json"] or "[]"), "created_at": row["created_at"]}

    def _project_summary(self, payload: dict) -> dict:
        """Sidebar numbers: per-dataset match/review progress (latest full job) and active jobs."""
        store, project_id = self._store(), self._project_id()
        latest_full = {
            row["dataset_id"]: row["job_id"]
            for row in store._connection.execute(
                """SELECT r.dataset_id, r.job_id FROM match_runs r JOIN jobs j ON j.id = r.job_id
                   WHERE j.project_id = ? AND r.run_mode = 'full' AND j.state = 'completed' ORDER BY j.created_at""",
                (project_id,),
            )
        }
        datasets_out, totals = [], {"pending_review": 0, "reviewed": 0, "safe_matches": 0, "rows": 0}
        for dataset in datasets.list_datasets(store, project_id):
            entry = {k: dataset[k] for k in ("id", "name", "kind", "row_count", "mapping_version")}
            totals["rows"] += dataset["row_count"]
            job_id = latest_full.get(dataset["id"])
            if job_id:
                summary = matching.results(store, job_id)
                reviewed = sum(summary["reviewed"].values())
                entry.update(job_id=job_id, pending_review=summary["pending_review"], reviewed=reviewed,
                             safe_matches=summary["decisions"].get("match", 0), canonical_records=summary["canonical_records"])
                totals["pending_review"] += summary["pending_review"]
                totals["reviewed"] += reviewed
                totals["safe_matches"] += entry["safe_matches"]
            datasets_out.append(entry)
        jobs = [_job_dict(r) for r in store.list_jobs(project_id, 100)]
        return {
            "project": self.project, "datasets": datasets_out, "totals": totals,
            "active_jobs": [j for j in jobs if j["state"] in ("validating", "queued", "running", "paused")],
            "recent_failed": [j for j in jobs if j["state"] == "failed"][:3],
            "job_counts": {state: sum(1 for j in jobs if j["state"] == state) for state in ("completed", "failed", "cancelled")},
        }

    def _job_get(self, payload: dict) -> dict:
        store = self._store()
        row = store.job(payload["job_id"])
        if row is None:
            raise CommandError("not_found", "Unknown job")
        events = [{**dict(e), "payload": json.loads(e["payload_json"])} for e in store.job_events(payload["job_id"], int(payload.get("after_event_id", 0)))]
        for event in events:
            event.pop("payload_json")
        return {**_job_dict(row), "events": events}
