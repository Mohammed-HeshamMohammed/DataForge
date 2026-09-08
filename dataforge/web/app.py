"""Application factory and page routes for the HTMX front end."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from dataforge import __version__
from dataforge.config import get_settings
from dataforge.logging import configure_logging
from dataforge.pipelines.registry import list_pipelines
from dataforge.scraping.registry import list_spiders

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def create_app() -> FastAPI:
    """Build the FastAPI application."""
    settings = get_settings()
    configure_logging()
    settings.ensure_directories()

    app = FastAPI(
        title="DataForge",
        description="Scrape, match, deduplicate and enter records.",
        version=__version__,
    )
    app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    from dataforge.web.api.routes import router as api_router

    app.include_router(api_router, prefix="/api")

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/")
    async def index(request: Request):
        """The dashboard: upload files, inspect spiders and pipelines."""
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "version": __version__,
                "spiders": list_spiders(),
                "pipelines": list_pipelines(),
                "threshold": settings.match_threshold,
            },
        )

    return app


app = create_app()
