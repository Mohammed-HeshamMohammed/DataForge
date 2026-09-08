"""Spider contract, registry and HTML table parsing."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from dataforge.scraping.base import ScrapeResult, Spider, SpiderContext
from dataforge.scraping.registry import get_spider, list_spiders, register_spider


class CountingSpider(Spider):
    name = "test-counting"
    description = "Yields numbered records."

    def crawl(self) -> Iterator[dict[str, Any]]:
        for i in range(10):
            yield {"n": i}


class BrokenSpider(Spider):
    name = "test-broken"
    description = "Fails partway through."

    def crawl(self) -> Iterator[dict[str, Any]]:
        yield {"n": 0}
        raise ValueError("scrape failed")


def test_max_records_caps_the_run():
    result = CountingSpider(SpiderContext(max_records=3)).run()
    assert len(result.records) == 3


def test_a_crawl_error_is_captured_rather_than_raised():
    result = BrokenSpider().run()
    assert result.records == [{"n": 0}]
    assert "scrape failed" in result.errors[0]


def test_results_convert_to_a_frame():
    frame = ScrapeResult(records=[{"a": 1}, {"a": 2}]).to_frame()
    assert list(frame["a"]) == [1, 2]


def test_registry_exposes_the_bundled_spider():
    assert "tabular" in list_spiders()
    assert get_spider("tabular").name == "tabular"


def test_unknown_spider_raises():
    with pytest.raises(KeyError):
        get_spider("does-not-exist")


def test_registering_a_duplicate_name_is_rejected():
    @register_spider
    class First(Spider):
        name = "test-dupe"

        def crawl(self):
            yield {}

    with pytest.raises(ValueError):

        @register_spider
        class Second(Spider):
            name = "test-dupe"

            def crawl(self):
                yield {}
