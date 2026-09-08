"""An HTTP spider base class with polite defaults.

Rate limiting, retries, a declared user agent and a robots.txt check are built
in rather than left to each spider, so that a new source is written by
implementing :meth:`parse` and nothing else.
"""

from __future__ import annotations

import time
import urllib.robotparser
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import httpx

from dataforge.config import get_settings
from dataforge.logging import get_logger
from dataforge.scraping.base import Spider

logger = get_logger(__name__)


class RobotsDisallowedError(RuntimeError):
    """Raised when a target URL is disallowed by the site's robots.txt."""


class HttpSpider(Spider):
    """Fetch each target URL and hand its body to :meth:`parse`."""

    name = "http"
    description = "Fetch target URLs over HTTP and parse records from each response."

    def __init__(self, context=None) -> None:
        super().__init__(context)
        settings = get_settings()
        self.timeout = settings.scrape_timeout_seconds
        self.delay = settings.scrape_delay_seconds
        self.max_retries = settings.scrape_max_retries
        self.respect_robots = settings.scrape_respect_robots
        self.headers = {"User-Agent": settings.scrape_user_agent}
        self._pages_fetched = 0
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}

    def crawl(self) -> Iterator[dict[str, Any]]:
        """Fetch every target in turn, yielding whatever :meth:`parse` returns."""
        if not self.context.targets:
            raise ValueError("No targets supplied to spider")

        with httpx.Client(
            headers=self.headers, timeout=self.timeout, follow_redirects=True
        ) as client:
            for index, url in enumerate(self.context.targets):
                if self.respect_robots and not self._robots_allows(client, url):
                    raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")
                if index:
                    time.sleep(self.delay)
                response = self._fetch(client, url)
                self._pages_fetched += 1
                yield from self.parse(response)

    def _fetch(self, client: httpx.Client, url: str) -> httpx.Response:
        """GET ``url``, retrying transient failures with exponential backoff."""
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = client.get(url)
                response.raise_for_status()
                return response
            except (httpx.HTTPError, httpx.StreamError) as exc:
                last_error = exc
                backoff = self.delay * (2**attempt)
                logger.warning("Fetch failed (%s); retrying in %.1fs", exc, backoff)
                time.sleep(backoff)
        raise RuntimeError(f"Giving up on {url} after {self.max_retries} attempts") from last_error

    def _robots_allows(self, client: httpx.Client, url: str) -> bool:
        """Check the origin's robots.txt, caching one parser per origin."""
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots_cache.get(origin)
        if parser is None:
            parser = urllib.robotparser.RobotFileParser()
            try:
                response = client.get(f"{origin}/robots.txt")
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except httpx.HTTPError:
                # No reachable robots.txt is conventionally treated as allow-all.
                parser.parse([])
            self._robots_cache[origin] = parser
        return parser.can_fetch(self.headers["User-Agent"], url)

    def parse(self, response: httpx.Response) -> Iterator[dict[str, Any]]:
        """Extract records from one response.

        The default implementation reads every HTML ``<table>`` it finds, which
        covers the common case of a listing page. Override for anything else.
        """
        yield from parse_html_tables(response.text)


def parse_html_tables(html: str) -> Iterator[dict[str, Any]]:
    """Yield a dict per row of every ``<table>`` in ``html``."""
    import pandas as pd

    try:
        tables = pd.read_html(html)
    except ValueError:
        return  # No tables on the page.
    for table in tables:
        for record in table.fillna("").to_dict(orient="records"):
            yield {str(k): v for k, v in record.items()}
