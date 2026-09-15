from datetime import datetime, timezone

from . import SCHEMA_VERSION


def health_check() -> dict[str, object]:
    return {
        "schema_version": SCHEMA_VERSION,
        "service": "scraping-worker",
        "status": "ok",
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
