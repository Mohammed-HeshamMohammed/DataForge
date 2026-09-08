"""The ``dataforge`` command line, one sub-app per layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pandas as pd
import typer

from dataforge import __version__
from dataforge.config import get_settings
from dataforge.logging import configure_logging, get_logger

app = typer.Typer(help="DataForge — scrape, match, deduplicate and enter records.")
ml_app = typer.Typer(help="Train and inspect the record-matching model.")
scrape_app = typer.Typer(help="Run and list spiders.")
pipeline_app = typer.Typer(help="Run and list pipelines.")
entry_app = typer.Typer(help="Drive data-entry recipes.")

app.add_typer(ml_app, name="ml")
app.add_typer(scrape_app, name="scrape")
app.add_typer(pipeline_app, name="pipeline")
app.add_typer(entry_app, name="entry")

logger = get_logger(__name__)


def _echo_json(payload: object) -> None:
    typer.echo(json.dumps(payload, indent=2, default=str))


@app.callback()
def main(log_level: str = typer.Option("INFO", help="Logging verbosity.")) -> None:
    """Configure logging before any sub-command runs."""
    configure_logging(log_level)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(__version__)


@app.command()
def dedupe(
    inputs: list[Path] = typer.Argument(..., help="Files to deduplicate together."),
    output: Path = typer.Option(..., "--output", "-o", help="Where to write the result."),
    threshold: float = typer.Option(0.85, min=0.0, max=1.0, help="Match acceptance score."),
    use_model: bool = typer.Option(False, help="Score pairs with the trained model."),
    model_path: Optional[Path] = typer.Option(
        None, help="Model file to score with. Defaults to the configured model directory."
    ),
) -> None:
    """Deduplicate one or more files and write the surviving rows."""
    from dataforge.core.dedupe import DedupeConfig, Deduplicator
    from dataforge.core.io import load_table, save_table

    frames = [load_table(path) for path in inputs]
    combined = pd.concat(frames, ignore_index=True).fillna("")

    scorer = None
    if use_model:
        from dataforge.ml.model import load_model

        scorer = load_model(model_path)

    result = Deduplicator(config=DedupeConfig(threshold=threshold), scorer=scorer).run(combined)
    save_table(result.frame, output)
    _echo_json({**result.summary(), "output": str(output)})


@app.command()
def serve(
    host: Optional[str] = typer.Option(None, help="Bind address."),
    port: Optional[int] = typer.Option(None, help="Bind port."),
    reload: bool = typer.Option(False, help="Reload on source changes."),
) -> None:
    """Run the FastAPI web application."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "dataforge.web.app:app",
        host=host or settings.web_host,
        port=port or settings.web_port,
        reload=reload or settings.web_reload,
    )


@app.command()
def desktop() -> None:
    """Launch the Tkinter desktop client."""
    from dataforge.desktop.app import run

    run()


@ml_app.command("train")
def ml_train(
    inputs: list[Path] = typer.Argument(..., help="Files to build the training set from."),
    labels: Optional[Path] = typer.Option(
        None, help="CSV of left,right,label pairs. Omit to weakly label from the rule scorer."
    ),
    output: Optional[Path] = typer.Option(None, help="Where to save the model."),
    threshold: float = typer.Option(0.85, min=0.0, max=1.0),
) -> None:
    """Train the record-matching model and save it."""
    from dataforge.core.dedupe import DedupeConfig
    from dataforge.core.io import load_table
    from dataforge.ml.train import train_from_frame

    combined = pd.concat([load_table(p) for p in inputs], ignore_index=True).fillna("")
    label_frame = load_table(labels) if labels else None
    _, report, path = train_from_frame(
        combined, labels=label_frame, config=DedupeConfig(threshold=threshold), output_path=output
    )
    _echo_json({**report.summary(), "model_path": str(path)})


@ml_app.command("info")
def ml_info(path: Optional[Path] = typer.Option(None, help="Model file to inspect.")) -> None:
    """Show the features and threshold of a saved model."""
    from dataforge.ml.model import load_model

    model = load_model(path)
    _echo_json({"threshold": model.threshold, "features": model.feature_names})


@scrape_app.command("list")
def scrape_list() -> None:
    """List every registered spider."""
    from dataforge.scraping.registry import list_spiders

    _echo_json(list_spiders())


@scrape_app.command("run")
def scrape_run(
    spider: str = typer.Argument(..., help="Registered spider name."),
    targets: list[str] = typer.Argument(..., help="URLs to scrape."),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Where to write records."),
    max_records: Optional[int] = typer.Option(None, help="Stop after this many records."),
) -> None:
    """Run a spider and optionally write its records to a file."""
    from dataforge.core.io import save_table
    from dataforge.scraping.base import SpiderContext
    from dataforge.scraping.registry import get_spider

    spider_instance = get_spider(spider)(
        SpiderContext(targets=list(targets), max_records=max_records)
    )
    result = spider_instance.run()
    if output:
        save_table(result.to_frame(), output)
    _echo_json(
        {**result.summary(), "errors": result.errors, "output": str(output) if output else None}
    )


@pipeline_app.command("list")
def pipeline_list() -> None:
    """List every registered pipeline."""
    from dataforge.pipelines.registry import list_pipelines

    _echo_json(list_pipelines())


@pipeline_app.command("run")
def pipeline_run(
    name: str = typer.Argument(..., help="Registered pipeline name."),
    params: Optional[Path] = typer.Option(None, help="JSON file of pipeline parameters."),
) -> None:
    """Run a pipeline with an optional JSON parameter file."""
    from dataforge.pipelines.pipeline import PipelineContext
    from dataforge.pipelines.registry import get_pipeline

    payload = json.loads(params.read_text(encoding="utf-8")) if params else {}
    run = get_pipeline(name).run(PipelineContext(params=payload))
    _echo_json({**run.summary(), "outputs": run.context.outputs if run.context else {}})


@entry_app.command("run")
def entry_run(
    recipe: Path = typer.Argument(..., help="Recipe YAML file."),
    records: Path = typer.Argument(..., help="File of records to enter."),
    live: bool = typer.Option(
        False, "--live", help="Actually drive a browser. Without this, validate only."
    ),
) -> None:
    """Run a data-entry recipe over a file of records.

    Defaults to a dry run so a misconfigured recipe cannot submit to a live
    system by accident; pass ``--live`` to open a browser.
    """
    from dataforge.automation.recipe import load_recipe
    from dataforge.automation.runner import RecipeRunner
    from dataforge.core.io import load_table

    frame = load_table(records)
    result = RecipeRunner(load_recipe(recipe), dry_run=not live).run(
        frame.to_dict(orient="records")
    )
    _echo_json({**result.summary(), "dry_run": not live, "failures": result.failures})


if __name__ == "__main__":
    app()
