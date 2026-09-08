"""Named pipelines, including the built-in end-to-end flows."""

from __future__ import annotations

from dataforge.pipelines.pipeline import Pipeline
from dataforge.pipelines.steps import (
    dedupe_step,
    enter_step,
    load_step,
    save_step,
    scrape_step,
)

_REGISTRY: dict[str, Pipeline] = {}


def register_pipeline(pipeline: Pipeline) -> Pipeline:
    """Register a pipeline under its name."""
    if pipeline.name in _REGISTRY:
        raise ValueError(f"Pipeline '{pipeline.name}' is already registered")
    _REGISTRY[pipeline.name] = pipeline
    return pipeline


def get_pipeline(name: str) -> Pipeline:
    """Look up a registered pipeline by name."""
    if name not in _REGISTRY:
        raise KeyError(f"Unknown pipeline '{name}'. Available: {sorted(_REGISTRY)}")
    return _REGISTRY[name]


def list_pipelines() -> dict[str, str]:
    """Return ``{name: description}`` for every registered pipeline."""
    return {name: p.description for name, p in sorted(_REGISTRY.items())}


def _register_builtins() -> None:
    """The three flows the platform ships with."""
    register_pipeline(
        Pipeline(
            name="dedupe-files",
            description="Load local files, remove duplicates, write the result.",
            steps=[load_step, dedupe_step, save_step],
        )
    )
    register_pipeline(
        Pipeline(
            name="scrape-dedupe",
            description="Scrape a source, deduplicate the records, write the result.",
            steps=[scrape_step, dedupe_step, save_step],
        )
    )
    register_pipeline(
        Pipeline(
            name="scrape-dedupe-enter",
            description="Scrape, deduplicate, then enter each record via a recipe.",
            steps=[scrape_step, dedupe_step, save_step, enter_step],
        )
    )


_register_builtins()
