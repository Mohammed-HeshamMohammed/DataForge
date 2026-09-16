"""Structured JSON logs with sensitive-value redaction.

Records go to stderr (stdout is reserved for the IPC protocol) and to a rotating file,
<app data>/DataForge/logs/service.log (5 MB x 3), unless DATAFORGE_LOG_FILE=0.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler

_SENSITIVE_KEYS = re.compile(r"(password|passwd|secret|token|cookie|authorization|api[_-]?key|session)", re.I)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_QUERY = re.compile(r"(\?)[^\s\"']+")
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}
_file_logger: logging.Logger | None = None
_file_lock = threading.Lock()


def redact(value: object, key: str = "") -> object:
    if _SENSITIVE_KEYS.search(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {k: redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _QUERY.sub(r"\1[redacted]", _PHONE.sub("[phone]", _EMAIL.sub("[email]", value)))
    return value


def _file() -> logging.Logger | None:
    global _file_logger
    if os.environ.get("DATAFORGE_LOG_FILE", "1") == "0":
        return None
    with _file_lock:
        if _file_logger is None:
            from .projects import app_data_dir

            directory = app_data_dir() / "logs"
            directory.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("dataforge.service")
            logger.propagate = False
            logger.setLevel(logging.DEBUG)
            handler = RotatingFileHandler(directory / "service.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger.addHandler(handler)
            _file_logger = logger
    return _file_logger


def log(level: str, event: str, **fields: object) -> None:
    if _LEVELS[level] < _LEVELS.get(os.environ.get("DATAFORGE_LOG_LEVEL", "info"), 20):
        return
    line = json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "level": level, "event": event, **redact(fields)}, default=str)
    print(line, file=sys.stderr, flush=True)
    try:
        logger = _file()
        if logger:
            logger.info(line)
    except OSError:
        pass  # logging must never break the service
