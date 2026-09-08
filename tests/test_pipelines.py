"""Pipeline composition, failure handling and the built-in flows."""

from __future__ import annotations

import pandas as pd

from dataforge.pipelines.pipeline import Pipeline, PipelineContext, step
from dataforge.pipelines.registry import get_pipeline, list_pipelines
from dataforge.pipelines.steps import dedupe_step, load_step, save_step


@step("double")
def double_step(context: PipelineContext) -> PipelineContext:
    context.params["n"] = context.param("n", 0) * 2
    return context


@step("boom")
def failing_step(context: PipelineContext) -> PipelineContext:
    raise RuntimeError("nope")


def test_steps_run_in_order_and_share_context():
    run = Pipeline(name="p", steps=[double_step, double_step]).run(PipelineContext(params={"n": 3}))
    assert run.status == "succeeded"
    assert run.context.params["n"] == 12


def test_a_failing_step_stops_the_pipeline_and_is_recorded():
    run = Pipeline(name="p", steps=[double_step, failing_step, double_step]).run(
        PipelineContext(params={"n": 1})
    )
    assert run.status == "failed"
    assert [s.status for s in run.steps] == ["succeeded", "failed"]
    assert "nope" in run.steps[1].error


def test_builtin_pipelines_are_registered():
    names = list_pipelines()
    assert "dedupe-files" in names
    assert "scrape-dedupe-enter" in names
    assert get_pipeline("dedupe-files").steps


def test_dedupe_files_pipeline_end_to_end(tmp_path, leads_frame):
    source = tmp_path / "leads.csv"
    leads_frame.to_csv(source, index=False)
    output = tmp_path / "clean.csv"

    run = Pipeline(name="e2e", steps=[load_step, dedupe_step, save_step]).run(
        PipelineContext(params={"inputs": [str(source)], "output": str(output), "threshold": 0.8})
    )

    assert run.status == "succeeded", run.summary()
    assert output.exists()
    assert run.context.outputs["dedupe"]["duplicates_removed"] == 3
    assert len(pd.read_csv(output)) == 3
