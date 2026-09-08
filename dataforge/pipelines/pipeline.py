"""A minimal sequential pipeline runner.

Steps are plain callables taking and returning a :class:`PipelineContext`, so
scraping, deduplication and data entry compose without any of those layers
knowing about each other.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

import pandas as pd

from dataforge.logging import get_logger

logger = get_logger(__name__)


@dataclass
class PipelineContext:
    """State threaded through a pipeline's steps."""

    frame: pd.DataFrame = field(default_factory=pd.DataFrame)
    params: dict[str, Any] = field(default_factory=dict)
    #: Per-step outputs, keyed by step name, for reporting.
    outputs: dict[str, Any] = field(default_factory=dict)

    def param(self, key: str, default: Any = None) -> Any:
        return self.params.get(key, default)


class Step(Protocol):
    """A single unit of pipeline work."""

    name: str

    def __call__(self, context: PipelineContext) -> PipelineContext: ...


@dataclass
class StepRecord:
    """What happened when one step ran."""

    name: str
    status: str
    duration_seconds: float
    error: str | None = None


@dataclass
class PipelineRun:
    """The record of a single pipeline execution."""

    run_id: str
    pipeline: str
    started_at: datetime
    finished_at: datetime | None = None
    status: str = "running"
    steps: list[StepRecord] = field(default_factory=list)
    context: PipelineContext | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "pipeline": self.pipeline,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "steps": [
                {
                    "name": s.name,
                    "status": s.status,
                    "duration_seconds": round(s.duration_seconds, 3),
                    "error": s.error,
                }
                for s in self.steps
            ],
        }


def step(name: str) -> Callable[[Callable[[PipelineContext], PipelineContext]], Step]:
    """Decorator turning a function into a named pipeline step."""

    def decorator(func: Callable[[PipelineContext], PipelineContext]) -> Step:
        func.name = name  # type: ignore[attr-defined]
        return func  # type: ignore[return-value]

    return decorator


@dataclass
class Pipeline:
    """An ordered list of steps run against a shared context."""

    name: str
    steps: list[Step] = field(default_factory=list)
    description: str = ""

    def add(self, step_callable: Step) -> Pipeline:
        """Append a step; returns self so calls chain."""
        self.steps.append(step_callable)
        return self

    def run(self, context: PipelineContext | None = None) -> PipelineRun:
        """Execute every step in order, stopping at the first failure."""
        context = context or PipelineContext()
        run = PipelineRun(
            run_id=uuid.uuid4().hex[:12],
            pipeline=self.name,
            started_at=datetime.now(UTC),
        )
        logger.info("Pipeline '%s' starting (run %s)", self.name, run.run_id)

        for current in self.steps:
            step_name = getattr(current, "name", current.__class__.__name__)
            started = time.perf_counter()
            try:
                context = current(context)
                run.steps.append(StepRecord(step_name, "succeeded", time.perf_counter() - started))
            except Exception as exc:  # noqa: BLE001 - recorded and surfaced to the caller
                run.steps.append(
                    StepRecord(
                        step_name,
                        "failed",
                        time.perf_counter() - started,
                        f"{type(exc).__name__}: {exc}",
                    )
                )
                run.status = "failed"
                run.finished_at = datetime.now(UTC)
                run.context = context
                logger.error("Pipeline '%s' failed at step '%s'", self.name, step_name)
                return run

        run.status = "succeeded"
        run.finished_at = datetime.now(UTC)
        run.context = context
        logger.info("Pipeline '%s' finished (run %s)", self.name, run.run_id)
        return run
