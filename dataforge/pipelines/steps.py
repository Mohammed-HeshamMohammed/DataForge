"""Ready-made steps that wire the platform's layers into pipelines."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dataforge.automation.recipe import load_recipe
from dataforge.automation.runner import RecipeRunner
from dataforge.core.dedupe import DedupeConfig, Deduplicator
from dataforge.core.io import load_table, save_table
from dataforge.logging import get_logger
from dataforge.pipelines.pipeline import PipelineContext, step
from dataforge.scraping.base import SpiderContext
from dataforge.scraping.registry import get_spider

logger = get_logger(__name__)


@step("load")
def load_step(context: PipelineContext) -> PipelineContext:
    """Read every path in ``params['inputs']`` into one frame."""
    paths = context.param("inputs", [])
    if not paths:
        raise ValueError("load step requires params['inputs']")
    frames = [load_table(p) for p in paths]
    context.frame = pd.concat(frames, ignore_index=True).fillna("")
    context.outputs["load"] = {"files": len(frames), "rows": len(context.frame)}
    return context


@step("scrape")
def scrape_step(context: PipelineContext) -> PipelineContext:
    """Run a registered spider and put its records into the context frame."""
    spider_cls = get_spider(context.param("spider", "tabular"))
    spider = spider_cls(
        SpiderContext(
            targets=context.param("targets", []),
            options=context.param("spider_options", {}),
            max_records=context.param("max_records"),
        )
    )
    result = spider.run()
    context.frame = result.to_frame()
    context.outputs["scrape"] = result.summary()
    return context


@step("dedupe")
def dedupe_step(context: PipelineContext) -> PipelineContext:
    """Deduplicate the context frame, optionally with a trained ML model."""
    config = DedupeConfig(
        threshold=context.param("threshold", DedupeConfig.threshold),
        blocking_key_length=context.param("blocking_key_length", DedupeConfig.blocking_key_length),
    )

    scorer = None
    if context.param("use_model", False):
        from dataforge.ml.model import load_model

        scorer = load_model(context.param("model_path"))

    result = Deduplicator(config=config, scorer=scorer).run(context.frame)
    context.frame = result.frame
    context.outputs["dedupe"] = result.summary()
    return context


@step("save")
def save_step(context: PipelineContext) -> PipelineContext:
    """Write the context frame to ``params['output']``."""
    output = context.param("output")
    if not output:
        raise ValueError("save step requires params['output']")
    path = save_table(context.frame, Path(output), context.param("output_format"))
    context.outputs["save"] = {"path": str(path), "rows": len(context.frame)}
    return context


@step("enter")
def enter_step(context: PipelineContext) -> PipelineContext:
    """Feed the context frame into a data-entry recipe.

    Defaults to a dry run: a pipeline never submits to a live system unless
    ``params['dry_run']`` is explicitly set to False.
    """
    recipe_path = context.param("recipe")
    if not recipe_path:
        raise ValueError("enter step requires params['recipe']")
    recipe = load_recipe(recipe_path)
    runner = RecipeRunner(recipe, dry_run=context.param("dry_run", True))
    result = runner.run(context.frame.to_dict(orient="records"))
    context.outputs["enter"] = result.summary()
    return context
