"""Durable definitions for constrained, row-driven browser workflows."""

from __future__ import annotations

import json
from urllib.parse import urlparse
from uuid import uuid4

from .storage import ProjectStore, utc_now

ACTION_TYPES = frozenset({"click", "fill", "wait", "read"})


def _validate_url(value: object) -> tuple[str, str]:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("Workflow start URL must be an HTTPS URL without embedded credentials")
    return url, parsed.hostname.lower()


def _validate_steps(value: object) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValueError("Add at least one workflow step")
    if len(value) > 100:
        raise ValueError("A workflow can contain at most 100 steps")
    steps: list[dict] = []
    for index, raw in enumerate(value, 1):
        if not isinstance(raw, dict) or raw.get("type") not in ACTION_TYPES:
            raise ValueError(f"Step {index} has an unsupported action")
        kind = raw["type"]
        selector = str(raw.get("selector") or "").strip()
        if kind != "wait" and not selector:
            raise ValueError(f"Step {index} needs a CSS selector")
        if len(selector) > 1000:
            raise ValueError(f"Step {index} selector is too long")
        value_text = str(raw.get("value") or "")
        if len(value_text) > 10000:
            raise ValueError(f"Step {index} value is too long")
        if kind == "wait":
            milliseconds = max(100, min(int(raw.get("milliseconds") or 1000), 30000))
            steps.append({"type": kind, "milliseconds": milliseconds})
        else:
            steps.append({"type": kind, "selector": selector, "value": value_text})
    return steps


def list_workflows(store: ProjectStore, project_id: str) -> list[dict]:
    rows = store._connection.execute(
        "SELECT * FROM automation_workflows WHERE project_id = ? ORDER BY updated_at DESC", (project_id,)
    ).fetchall()
    return [{**dict(row), "steps": json.loads(row["steps_json"])} for row in rows]


def save_workflow(store: ProjectStore, project_id: str, payload: dict) -> dict:
    name = str(payload.get("name") or "").strip()
    if not name or len(name) > 120:
        raise ValueError("Workflow name must be between 1 and 120 characters")
    start_url, host = _validate_url(payload.get("start_url"))
    steps = _validate_steps(payload.get("steps"))
    dataset_id = payload.get("dataset_id") or None
    if dataset_id and store._connection.execute("SELECT 1 FROM datasets WHERE id = ? AND project_id = ?", (dataset_id, project_id)).fetchone() is None:
        raise ValueError("Choose a dataset from the current project")
    workflow_id = str(payload.get("id") or uuid4())
    now = utc_now()
    existing = store._connection.execute("SELECT created_at FROM automation_workflows WHERE id = ? AND project_id = ?", (workflow_id, project_id)).fetchone()
    if existing:
        store._connection.execute(
            "UPDATE automation_workflows SET name=?, start_url=?, allowed_host=?, dataset_id=?, steps_json=?, updated_at=? WHERE id=? AND project_id=?",
            (name, start_url, host, dataset_id, json.dumps(steps), now, workflow_id, project_id),
        )
        created_at = existing["created_at"]
    else:
        store._connection.execute(
            "INSERT INTO automation_workflows(id, project_id, name, start_url, allowed_host, dataset_id, steps_json, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (workflow_id, project_id, name, start_url, host, dataset_id, json.dumps(steps), now, now),
        )
        created_at = now
    store._connection.commit()
    return {"id": workflow_id, "project_id": project_id, "name": name, "start_url": start_url, "allowed_host": host, "dataset_id": dataset_id, "steps": steps, "created_at": created_at, "updated_at": now}


def delete_workflow(store: ProjectStore, project_id: str, workflow_id: str) -> dict:
    cursor = store._connection.execute("DELETE FROM automation_workflows WHERE id = ? AND project_id = ?", (workflow_id, project_id))
    store._connection.commit()
    if cursor.rowcount != 1:
        raise ValueError("Unknown workflow")
    return {"deleted": workflow_id}
