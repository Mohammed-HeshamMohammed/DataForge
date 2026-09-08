"""Web scraping layer: the spider contract, an HTTP base class and a registry."""

from dataforge.scraping.base import ScrapeResult, Spider, SpiderContext
from dataforge.scraping.http import HttpSpider
from dataforge.scraping.registry import get_spider, list_spiders, register_spider

__all__ = [
    "HttpSpider",
    "ScrapeResult",
    "Spider",
    "SpiderContext",
    "get_spider",
    "list_spiders",
    "register_spider",
]
