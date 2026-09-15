from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from . import SCHEMA_VERSION

_ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"validating", "cancelled"}),
    "validating": frozenset({"queued", "failed", "cancelled"}),
    "queued": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"paused", "completed", "failed", "cancelled"}),
    "paused": frozenset({"running", "failed", "cancelled"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}
ACTIVE_STATES = ("validating", "queued", "running", "paused")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProjectStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Callers serialize access (Service holds a lock; each job thread owns its own store),
        # so the connection may be used from whichever thread handles the current request.
        self._connection = sqlite3.connect(self.database_path, timeout=30, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        # WAL lets job threads write while the UI-facing connection reads.
        self._connection.execute("PRAGMA journal_mode=WAL")

    @property
    def project_root(self) -> Path:
        return self.database_path.parent

    def close(self) -> None:
        self._connection.close()

    def migrate(self, migrations_path: Path) -> list[str]:
        """Apply ordered migrations exactly once each. Returns the names applied in this call."""
        self._connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations (name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self._connection.execute("SELECT name FROM schema_migrations")}
        newly_applied = []
        for migration_path in sorted(migrations_path.glob("*.sql")):
            if migration_path.name in applied:
                continue
            script = migration_path.read_text(encoding="utf-8")
            try:
                self._connection.executescript(
                    f"BEGIN;\n{script}\nINSERT INTO schema_migrations(name, applied_at) VALUES ('{migration_path.name}', '{utc_now()}');\nCOMMIT;"
                )
            except Exception:
                self._connection.rollback()
                raise
            newly_applied.append(migration_path.name)
        return newly_applied

    def pending_migrations(self, migrations_path: Path) -> list[str]:
        exists = self._connection.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'").fetchone()
        applied = {row[0] for row in self._connection.execute("SELECT name FROM schema_migrations")} if exists else set()
        return [p.name for p in sorted(migrations_path.glob("*.sql")) if p.name not in applied]

    def backup(self, destination: Path) -> Path:
        """Consistent online copy via the SQLite backup API (safe with WAL)."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            self._connection.backup(target)
        finally:
            target.close()
        return destination

    def schema_version(self) -> int:
        row = self._connection.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()
        return int(row[0])

    def create_project(self, name: str, root_path: Path) -> str:
        project_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "INSERT INTO projects(id, name, root_path, created_at) VALUES (?, ?, ?, ?)",
            (project_id, name, str(root_path), timestamp),
        )
        self._connection.commit()
        return project_id

    def create_job(self, project_id: str, kind: str, params: dict[str, object] | None = None) -> str:
        job_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "INSERT INTO jobs(id, project_id, kind, state, created_at, updated_at, params_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (job_id, project_id, kind, "draft", timestamp, timestamp, json.dumps(params or {}, sort_keys=True)),
        )
        self._connection.commit()
        return job_id

    def set_job_outcome(self, job_id: str, result: dict[str, object] | None = None, error: str | None = None) -> None:
        self._connection.execute(
            "UPDATE jobs SET result_json = ?, error = ?, updated_at = ? WHERE id = ?",
            (None if result is None else json.dumps(result, sort_keys=True), error, utc_now(), job_id),
        )
        self._connection.commit()

    def list_jobs(self, project_id: str, limit: int = 50) -> list[sqlite3.Row]:
        return self._connection.execute(
            "SELECT * FROM jobs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?", (project_id, limit)
        ).fetchall()

    def job_events(self, job_id: str, after_id: int = 0) -> list[sqlite3.Row]:
        return self._connection.execute(
            "SELECT * FROM job_events WHERE job_id = ? AND id > ? ORDER BY id", (job_id, after_id)
        ).fetchall()

    def recover_interrupted_jobs(self) -> list[str]:
        """Jobs that were active when the process died cannot resume in-memory work; fail them durably."""
        placeholders = ",".join("?" * len(ACTIVE_STATES))
        rows = self._connection.execute(
            f"SELECT id, state FROM jobs WHERE state IN ({placeholders})", ACTIVE_STATES
        ).fetchall()
        for row in rows:
            self.transition_job(row["id"], "failed", "Interrupted by application restart; retry to run again")
            self.set_job_outcome(row["id"], error="Interrupted by application restart")
        return [row["id"] for row in rows]

    def append_event(self, job_id: str, event_type: str, payload: dict[str, object]) -> None:
        timestamp = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "INSERT INTO job_events(job_id, schema_version, event_type, occurred_at, payload_json) VALUES (?, ?, ?, ?, ?)",
            (job_id, SCHEMA_VERSION, event_type, timestamp, json.dumps(payload, sort_keys=True)),
        )
        self._connection.commit()

    def transition_job(self, job_id: str, new_state: str, reason: str | None = None) -> None:
        if new_state not in _ALLOWED_TRANSITIONS:
            raise ValueError(f"Unknown job state: {new_state}")
        current_job = self.job(job_id)
        if current_job is None:
            raise ValueError(f"Unknown job: {job_id}")
        current_state = current_job["state"]
        if new_state not in _ALLOWED_TRANSITIONS[current_state]:
            raise ValueError(f"Invalid job transition: {current_state} -> {new_state}")

        timestamp = datetime.now(timezone.utc).isoformat()
        payload = {"from_state": current_state, "to_state": new_state}
        if reason is not None:
            payload["reason"] = reason
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "UPDATE jobs SET state = ?, updated_at = ? WHERE id = ?",
                (new_state, timestamp, job_id),
            )
            self._connection.execute(
                "INSERT INTO job_events(job_id, schema_version, event_type, occurred_at, payload_json) VALUES (?, ?, ?, ?, ?)",
                (job_id, SCHEMA_VERSION, "job.state_changed", timestamp, json.dumps(payload, sort_keys=True)),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise

    def job(self, job_id: str) -> sqlite3.Row | None:
        return self._connection.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()

    def create_scrape_run(
        self,
        job_id: str,
        preset_id: str,
        preset_version: str,
        strategy_used: str,
        start_url: str,
    ) -> str:
        scrape_run_id = str(uuid4())
        timestamp = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "INSERT INTO scrape_runs(id, job_id, preset_id, preset_version, strategy_used, start_url, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (scrape_run_id, job_id, preset_id, preset_version, strategy_used, start_url, "started", timestamp),
        )
        self._connection.commit()
        return scrape_run_id

    def complete_scrape_run(
        self,
        scrape_run_id: str,
        pages_fetched: int,
        records_extracted: int,
        records_rejected: int,
        records_duplicate: int,
    ) -> None:
        completed_at = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "UPDATE scrape_runs SET pages_fetched = ?, records_extracted = ?, records_rejected = ?, records_duplicate = ?, status = 'completed', completed_at = ? WHERE id = ?",
            (pages_fetched, records_extracted, records_rejected, records_duplicate, completed_at, scrape_run_id),
        )
        self._connection.commit()

    def fail_scrape_run(self, scrape_run_id: str, error_type: str, message: str) -> None:
        occurred_at = datetime.now(timezone.utc).isoformat()
        self._connection.execute(
            "UPDATE scrape_runs SET status = 'failed', completed_at = ? WHERE id = ?",
            (occurred_at, scrape_run_id),
        )
        self._connection.execute(
            "INSERT INTO scrape_errors(scrape_run_id, error_type, message, occurred_at) VALUES (?, ?, ?, ?)",
            (scrape_run_id, error_type, message, occurred_at),
        )
        self._connection.commit()

    def scrape_run(self, scrape_run_id: str) -> sqlite3.Row | None:
        return self._connection.execute("SELECT * FROM scrape_runs WHERE id = ?", (scrape_run_id,)).fetchone()
