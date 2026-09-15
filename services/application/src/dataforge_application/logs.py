"""Structured JSON logs to stderr (stdout is reserved for the IPC protocol), with sensitive-value redaction."""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

_SENSITIVE_KEYS = re.compile(r"(password|passwd|secret|token|cookie|authorization|api[_-]?key|session)", re.I)
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_QUERY = re.compile(r"(\?)[^\s\"']+")
_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


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


def log(level: str, event: str, **fields: object) -> None:
    if _LEVELS[level] < _LEVELS.get(os.environ.get("DATAFORGE_LOG_LEVEL", "info"), 20):
        return
    record = {"ts": datetime.now(timezone.utc).isoformat(), "level": level, "event": event, **redact(fields)}
    print(json.dumps(record, default=str), file=sys.stderr, flush=True)
