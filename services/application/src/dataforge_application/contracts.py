from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from . import SCHEMA_VERSION


@dataclass(frozen=True)
class HealthResult:
    schema_version: int
    service: str
    status: str
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "service": self.service,
            "status": self.status,
            "checked_at": self.checked_at,
        }


def health_check() -> HealthResult:
    return HealthResult(
        schema_version=SCHEMA_VERSION,
        service="application",
        status="ok",
        checked_at=datetime.now(timezone.utc).isoformat(),
    )
