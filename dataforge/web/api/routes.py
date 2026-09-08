"""JSON API endpoints backing both the HTMX UI and external callers."""

from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from dataforge.core.dedupe import DedupeConfig, Deduplicator
from dataforge.logging import get_logger
from dataforge.pipelines.pipeline import PipelineContext
from dataforge.pipelines.registry import get_pipeline, list_pipelines
from dataforge.scraping.base import SpiderContext
from dataforge.scraping.registry import get_spider, list_spiders
from dataforge.web.api.schemas import (
    DedupeRequest,
    DedupeResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    ScrapeRequest,
    ScrapeResponse,
)

logger = get_logger(__name__)
router = APIRouter()


def _build_deduplicator(threshold: float, use_model: bool) -> Deduplicator:
    """Assemble a deduplicator, loading the ML scorer only when asked."""
    scorer = None
    if use_model:
        from dataforge.ml.model import load_model

        try:
            scorer = load_model()
        except FileNotFoundError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Deduplicator(config=DedupeConfig(threshold=threshold), scorer=scorer)


@router.post("/dedupe", response_model=DedupeResponse)
async def dedupe_records(payload: DedupeRequest) -> DedupeResponse:
    """Deduplicate a batch of records supplied as JSON."""
    frame = pd.DataFrame(payload.records).fillna("")
    result = _build_deduplicator(payload.threshold, payload.use_model).run(frame)
    return DedupeResponse(summary=result.summary(), records=result.frame.to_dict(orient="records"))


@router.post("/dedupe/upload", response_model=DedupeResponse)
async def dedupe_upload(
    files: list[UploadFile] = File(...),
    threshold: float = Form(0.85),
    use_model: bool = Form(False),
) -> DedupeResponse:
    """Deduplicate one or more uploaded CSV/Excel files."""
    frames: list[pd.DataFrame] = []
    for upload in files:
        content = await upload.read()
        name = (upload.filename or "").lower()
        try:
            if name.endswith((".xlsx", ".xls")):
                frames.append(pd.read_excel(io.BytesIO(content), dtype=str).fillna(""))
            else:
                frames.append(pd.read_csv(io.BytesIO(content), dtype=str, keep_default_na=False))
        except Exception as exc:  # noqa: BLE001 - report which upload was bad
            raise HTTPException(
                status_code=400, detail=f"Could not read {upload.filename}: {exc}"
            ) from exc

    if not frames:
        raise HTTPException(status_code=400, detail="No readable files uploaded")

    combined = pd.concat(frames, ignore_index=True).fillna("")
    result = _build_deduplicator(threshold, use_model).run(combined)
    return DedupeResponse(
        summary=result.summary(),
        # Cap the inline preview; the full result belongs in a download, not a
        # JSON body that could be tens of megabytes.
        records=result.frame.head(100).to_dict(orient="records"),
    )


@router.get("/spiders")
async def get_spiders() -> dict[str, str]:
    """List every registered spider."""
    return list_spiders()


@router.post("/scrape", response_model=ScrapeResponse)
async def run_scrape(payload: ScrapeRequest) -> ScrapeResponse:
    """Run a spider over the supplied targets."""
    try:
        spider_cls = get_spider(payload.spider)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    spider = spider_cls(
        SpiderContext(
            targets=payload.targets,
            options=payload.options,
            max_records=payload.max_records,
        )
    )
    result = spider.run()
    return ScrapeResponse(summary=result.summary(), records=result.records, errors=result.errors)


@router.get("/pipelines")
async def get_pipelines() -> dict[str, str]:
    """List every registered pipeline."""
    return list_pipelines()


@router.post("/pipelines/{name}/run", response_model=PipelineRunResponse)
async def run_pipeline(name: str, payload: PipelineRunRequest) -> PipelineRunResponse:
    """Run a named pipeline and return its execution record."""
    try:
        pipeline = get_pipeline(name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    run = pipeline.run(PipelineContext(params=payload.params))
    outputs = run.context.outputs if run.context else {}
    return PipelineRunResponse(run=run.summary(), outputs=outputs)
