"""A worked example spider: scrape HTML tables from a list of URLs.

Copy this module as the starting point for a real source — the only method a
new spider has to define is :meth:`parse`.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx

from dataforge.scraping.http import HttpSpider, parse_html_tables
from dataforge.scraping.registry import register_spider


@register_spider
class TabularSpider(HttpSpider):
    """Collect every HTML table row from each target URL."""

    name = "tabular"
    description = "Scrape rows from every HTML <table> on each target URL."

    def parse(self, response: httpx.Response) -> Iterator[dict[str, Any]]:
        source = str(response.url)
        for record in parse_html_tables(response.text):
            # Tag each record with its origin so scraped batches stay traceable
            # once several sources are merged for deduplication.
            record.setdefault("source_url", source)
            yield record
