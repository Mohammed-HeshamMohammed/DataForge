"""Logging setup shared by the CLI, the web app and background jobs."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

from dataforge.config import get_settings

_CONFIGURED = False


class JsonFormatter(logging.Formatter):
    """Render records as single-line JSON for log aggregation."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging(level: str | None = None, *, json_output: bool | None = None) -> None:
    """Configure the root logger once per process."""
    global _CONFIGURED
    settings = get_settings()
    resolved_level = (level or settings.log_level).upper()
    use_json = settings.log_json if json_output is None else json_output

    handler = logging.StreamHandler(sys.stderr)
    if use_json:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-8s %(name)s | %(message)s", "%H:%M:%S")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(resolved_level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger, configuring logging on first use."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
