"""The contract every spider implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class SpiderContext:
    """Per-run inputs handed to a spider: targets plus arbitrary options."""

    targets: list[str] = field(default_factory=list)
    options: dict[str, Any] = field(default_factory=dict)
    max_records: int | None = None

    def option(self, key: str, default: Any = None) -> Any:
        return self.options.get(key, default)


@dataclass
class ScrapeResult:
    """Records collected by a run, alongside anything that went wrong."""

    records: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    pages_fetched: int = 0

    def to_frame(self) -> pd.DataFrame:
        """Collected records as a DataFrame, ready for the dedupe engine."""
        return pd.DataFrame(self.records).fillna("") if self.records else pd.DataFrame()

    def summary(self) -> dict[str, int]:
        return {
            "records": len(self.records),
            "errors": len(self.errors),
            "pages_fetched": self.pages_fetched,
        }


class Spider(ABC):
    """Base class for a source of records.

    Subclasses implement :meth:`crawl` as a generator of record dicts; the
    driver in :meth:`run` handles the record cap and error collection so every
    spider behaves consistently.
    """

    #: Registry name, also used as the CLI argument.
    name: str = "spider"
    #: One-line description shown by ``dataforge scrape list``.
    description: str = ""

    def __init__(self, context: SpiderContext | None = None) -> None:
        self.context = context or SpiderContext()

    @abstractmethod
    def crawl(self) -> Iterator[dict[str, Any]]:
        """Yield one dict per record scraped."""

    def run(self) -> ScrapeResult:
        """Drive :meth:`crawl` to completion, honouring ``max_records``."""
        result = ScrapeResult()
        limit = self.context.max_records
        try:
            for record in self.crawl():
                result.records.append(record)
                if limit is not None and len(result.records) >= limit:
                    break
        except Exception as exc:  # noqa: BLE001 - a spider failure is a data point
            result.errors.append(f"{type(exc).__name__}: {exc}")
        result.pages_fetched = getattr(self, "_pages_fetched", result.pages_fetched)
        return result
