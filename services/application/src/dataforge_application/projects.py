"""ProjectService: create/open project directories with a validated path and a recent-projects list."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .storage import ProjectStore, utc_now

DATABASE_NAME = "dataforge.sqlite3"


def app_data_dir() -> Path:
    base = os.environ.get("DATAFORGE_APP_DATA") or os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "DataForge"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_project_path(raw_path: str) -> Path:
    if not raw_path or "\x00" in raw_path:
        raise ValueError("Project path is required")
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        raise ValueError("Project path must be absolute")
    path = path.resolve()
    if path == Path(path.anchor):
        raise ValueError("A drive or filesystem root cannot be used as a project directory")
    if path.exists() and not path.is_dir():
        raise ValueError("Project path points to a file, not a directory")
    return path


def _recent_file() -> Path:
    return app_data_dir() / "recent-projects.json"


def recent_projects() -> list[dict]:
    try:
        entries = json.loads(_recent_file().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [e for e in entries if (Path(e["root_path"]) / DATABASE_NAME).exists()]


def _remember(project: dict) -> None:
    entries = [e for e in recent_projects() if e["root_path"] != project["root_path"]]
    entries.insert(0, {**project, "opened_at": utc_now()})
    _recent_file().write_text(json.dumps(entries[:10], indent=2), encoding="utf-8")


def open_project(raw_path: str, migrations_dir: Path, name: str | None = None, create: bool = False) -> tuple[ProjectStore, dict]:
    root = validate_project_path(raw_path)
    database = root / DATABASE_NAME
    if not database.exists() and not create:
        raise ValueError("No DataForge project exists at that path")
    if database.exists() and create:
        raise ValueError("A DataForge project already exists at that path; open it instead")
    existed = database.exists()
    store = ProjectStore(database)
    pending = store.pending_migrations(migrations_dir)
    if existed and pending:
        # Back up before changing the schema; a failed migration rolls back and this copy remains.
        stamp = utc_now().replace(":", "").replace("-", "").split(".")[0]
        store.backup(root / "backups" / f"dataforge-before-{pending[0].removesuffix('.sql')}-{stamp}.sqlite3")
    try:
        store.migrate(migrations_dir)
    except Exception:
        store.close()
        raise
    row = store._connection.execute("SELECT * FROM projects ORDER BY created_at LIMIT 1").fetchone()
    if row is None:
        project_id = store.create_project(name or root.name, root)
        row = store._connection.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    project = {"id": row["id"], "name": row["name"], "root_path": row["root_path"], "created_at": row["created_at"], "schema_version": store.schema_version()}
    _remember(project)
    return store, project
