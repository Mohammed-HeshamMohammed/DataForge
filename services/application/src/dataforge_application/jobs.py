"""JobService: runs long work on background threads with durable state and stage events.

Handlers receive a JobContext. They must write their final output in one transaction at the end so a
cancelled or failed job never publishes partial results.
"""

from __future__ import annotations

import json
import threading
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .logs import log
from .storage import ProjectStore


class JobCancelled(Exception):
    pass


class JobValidationError(ValueError):
    """Raised by a handler's validate step; the job fails before it is queued."""


@dataclass
class _Control:
    cancel: threading.Event = field(default_factory=threading.Event)
    pause: threading.Event = field(default_factory=threading.Event)
    resume: threading.Event = field(default_factory=threading.Event)


class JobContext:
    def __init__(self, database_path: Path, job_id: str, params: dict, control: _Control, secrets: dict | None = None) -> None:
        self.store = ProjectStore(database_path)
        self.job_id = job_id
        self.params = params
        self.secrets = secrets or {}  # in memory only; never persisted or logged
        self._control = control

    def stage(self, name: str, detail: dict | None = None) -> None:
        self.store.append_event(self.job_id, "job.stage_changed", {"stage": name, **(detail or {})})

    def should_stop(self) -> bool:
        """Safe checkpoint: honours pause (blocks) and reports cancellation."""
        if self._control.pause.is_set() and not self._control.cancel.is_set():
            self.store.transition_job(self.job_id, "paused", "Paused at a safe checkpoint")
            self._control.resume.clear()
            while not (self._control.resume.wait(0.2) or self._control.cancel.is_set()):
                pass
            if not self._control.cancel.is_set():
                self._control.pause.clear()
                self.store.transition_job(self.job_id, "running", "Resumed")
        return self._control.cancel.is_set()

    def checkpoint(self) -> None:
        if self.should_stop():
            raise JobCancelled()


@dataclass(frozen=True)
class JobKind:
    run: Callable[[JobContext], dict]
    validate: Callable[[ProjectStore, dict], dict] = lambda store, params: params


class JobRunner:
    def __init__(self, database_path: Path, kinds: dict[str, JobKind]) -> None:
        self.database_path = database_path
        self.kinds = kinds
        self._controls: dict[str, _Control] = {}
        self._threads: dict[str, threading.Thread] = {}

    def submit(self, store: ProjectStore, project_id: str, kind: str, params: dict, secrets: dict | None = None) -> str:
        if kind not in self.kinds:
            raise JobValidationError(f"Unknown job kind: {kind}")
        job_id = store.create_job(project_id, kind, params)
        store.transition_job(job_id, "validating")
        try:
            params = self.kinds[kind].validate(store, params)
        except Exception as error:
            store.transition_job(job_id, "failed", str(error))
            store.set_job_outcome(job_id, error=str(error))
            raise
        store._connection.execute("UPDATE jobs SET params_json = ? WHERE id = ?", (json.dumps(params, sort_keys=True), job_id))
        store._connection.commit()
        store.transition_job(job_id, "queued")
        control = _Control()
        self._controls[job_id] = control
        thread = threading.Thread(target=self._run, args=(job_id, kind, params, control, secrets), daemon=True, name=f"job-{job_id[:8]}")
        self._threads[job_id] = thread
        thread.start()
        return job_id

    def _run(self, job_id: str, kind: str, params: dict, control: _Control, secrets: dict | None = None) -> None:
        context = JobContext(self.database_path, job_id, params, control, secrets)
        store = context.store
        started = time.monotonic()
        try:
            if control.cancel.is_set():
                raise JobCancelled()
            store.transition_job(job_id, "running")
            result = self.kinds[kind].run(context)
            if control.cancel.is_set():
                raise JobCancelled()
            store.set_job_outcome(job_id, result={**result, "duration_seconds": round(time.monotonic() - started, 3)})
            store.transition_job(job_id, "completed")
        except JobCancelled:
            store.transition_job(job_id, "cancelled", "Cancelled by user; source data unchanged")
        except Exception as error:  # noqa: BLE001 - every failure must become durable job state
            log("error", "job.failed", job_id=job_id, kind=kind, error=type(error).__name__)
            log("debug", "job.traceback", job_id=job_id, traceback=traceback.format_exc())
            store.set_job_outcome(job_id, error=str(error))
            store.transition_job(job_id, "failed", str(error))
        finally:
            store.close()
            self._controls.pop(job_id, None)

    def cancel(self, store: ProjectStore, job_id: str) -> None:
        control = self._controls.get(job_id)
        state = store.job(job_id)["state"]
        if control is None:
            raise ValueError(f"Job is not active (state: {state})")
        control.cancel.set()
        control.resume.set()

    def pause(self, job_id: str) -> None:
        self._require(job_id).pause.set()

    def resume(self, job_id: str) -> None:
        self._require(job_id).resume.set()

    def retry(self, store: ProjectStore, job_id: str, secrets: dict | None = None) -> str:
        job = store.job(job_id)
        if job is None or job["state"] not in ("failed", "cancelled"):
            raise ValueError("Only failed or cancelled jobs can be retried")
        params = {**json.loads(job["params_json"]), "retry_of": job_id}
        return self.submit(store, job["project_id"], job["kind"], params, secrets)

    def wait(self, job_id: str, timeout: float = 60.0) -> None:
        thread = self._threads.get(job_id)
        if thread is not None:
            thread.join(timeout)

    def _require(self, job_id: str) -> _Control:
        control = self._controls.get(job_id)
        if control is None:
            raise ValueError("Job is not active")
        return control


def fixture_job(context: JobContext) -> dict:
    """Fake long-running job that proves host → service → events → UI without real domain work."""
    steps = int(context.params.get("steps", 5))
    for step in range(1, steps + 1):
        context.checkpoint()
        context.stage("working", {"completed": step, "total": steps})
        time.sleep(float(context.params.get("step_seconds", 0.5)))
    return {"steps": steps}
