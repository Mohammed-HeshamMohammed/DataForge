"""Run pipelines on a schedule.

Wraps APScheduler so that the web app, the CLI and a container entrypoint all
schedule jobs the same way. APScheduler is imported lazily so the automation
extra stays optional.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from dataforge.logging import get_logger
from dataforge.pipelines.pipeline import PipelineContext, PipelineRun
from dataforge.pipelines.registry import get_pipeline

logger = get_logger(__name__)


@dataclass
class ScheduledJob:
    """A pipeline bound to a cron expression."""

    job_id: str
    pipeline: str
    cron: str
    params: dict[str, Any]


class Scheduler:
    """A thin, testable wrapper around APScheduler's background scheduler."""

    def __init__(self) -> None:
        self._scheduler: Any | None = None
        self.jobs: dict[str, ScheduledJob] = {}
        self.history: list[PipelineRun] = []

    def _ensure_scheduler(self) -> Any:
        if self._scheduler is None:
            try:
                from apscheduler.schedulers.background import BackgroundScheduler
            except ImportError as exc:
                raise RuntimeError(
                    "Scheduling requires the 'scheduler' extra: pip install 'dataforge[scheduler]'"
                ) from exc
            self._scheduler = BackgroundScheduler()
        return self._scheduler

    def add_job(
        self, job_id: str, pipeline: str, cron: str, params: dict[str, Any] | None = None
    ) -> ScheduledJob:
        """Schedule ``pipeline`` on a 5-field cron expression."""
        from apscheduler.triggers.cron import CronTrigger

        params = params or {}
        # Validate both the pipeline and the cron string before scheduling, so
        # a typo surfaces here rather than silently at the first fire time.
        get_pipeline(pipeline)
        trigger = CronTrigger.from_crontab(cron)

        scheduler = self._ensure_scheduler()
        scheduler.add_job(
            self._make_runner(pipeline, params), trigger=trigger, id=job_id, replace_existing=True
        )
        job = ScheduledJob(job_id=job_id, pipeline=pipeline, cron=cron, params=params)
        self.jobs[job_id] = job
        logger.info("Scheduled '%s' as %s (%s)", pipeline, job_id, cron)
        return job

    def remove_job(self, job_id: str) -> None:
        """Unschedule a job, ignoring one that is already gone."""
        if job_id in self.jobs:
            self._ensure_scheduler().remove_job(job_id)
            del self.jobs[job_id]

    def _make_runner(self, pipeline: str, params: dict[str, Any]) -> Callable[[], None]:
        """Build the zero-argument callable APScheduler will invoke."""

        def run() -> None:
            run_record = get_pipeline(pipeline).run(PipelineContext(params=dict(params)))
            self.history.append(run_record)

        return run

    def start(self) -> None:
        self._ensure_scheduler().start()

    def shutdown(self, wait: bool = True) -> None:
        if self._scheduler is not None and self._scheduler.running:
            self._scheduler.shutdown(wait=wait)
