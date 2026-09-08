"""DataForge — a versatile data platform for collecting, matching and entering records.

The package is organised as five cooperating layers:

``dataforge.core``
    File I/O, normalisation and the deterministic deduplication engine.
``dataforge.ml``
    Pairwise feature extraction and the learned record-matching model.
``dataforge.scraping``
    Spider framework for collecting records from the web.
``dataforge.automation``
    Browser-driven data entry (Selenium) built from declarative recipes.
``dataforge.pipelines``
    Composition and scheduling of the layers above into repeatable jobs.

The FastAPI application in ``dataforge.web`` and the Typer CLI in
``dataforge.cli`` are the two front doors onto those layers.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
