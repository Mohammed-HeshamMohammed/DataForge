"""Request and response models for the HTTP API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DedupeRequest(BaseModel):
    """Deduplicate records supplied inline as JSON."""

    records: list[dict[str, Any]] = Field(..., min_length=1)
    threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    use_model: bool = False


class DedupeResponse(BaseModel):
    """Summary statistics plus the surviving records."""

    summary: dict[str, Any]
    records: list[dict[str, Any]]


class ScrapeRequest(BaseModel):
    """Run a registered spider over a list of targets."""

    spider: str = "tabular"
    targets: list[str] = Field(..., min_length=1)
    max_records: int | None = Field(default=None, ge=1)
    options: dict[str, Any] = Field(default_factory=dict)


class ScrapeResponse(BaseModel):
    summary: dict[str, int]
    records: list[dict[str, Any]]
    errors: list[str] = Field(default_factory=list)


class PipelineRunRequest(BaseModel):
    """Run a named pipeline with a parameter bag."""

    params: dict[str, Any] = Field(default_factory=dict)


class PipelineRunResponse(BaseModel):
    run: dict[str, Any]
    outputs: dict[str, Any] = Field(default_factory=dict)
