"""Bundled spiders. Importing this package registers each one."""

from dataforge.scraping.spiders import tabular  # noqa: F401

__all__ = ["tabular"]
