"""Cleanup of one dataset's values before matching. The scan reads only that dataset; applying a plan writes a new,
cleaned dataset that records its parent and the changes, and the original rows stay exactly as imported."""

from __future__ import annotations

import json
from pathlib import Path

from dataforge_matching import cleanup

from .datasets import _register_rows, confirm_mapping, latest_mapping, propose_field_mappings
from .jobs import JobContext, JobKind, JobValidationError
from .storage import ProjectStore

MAX_ROWS = 500_000
DROP_REASONS = ("empty", "exact_duplicates", "test")


def _rows(store: ProjectStore, dataset_id: str) -> tuple[list[int], list[dict[str, object]]]:
    found = store._connection.execute(
        "SELECT source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number LIMIT ?", (dataset_id, MAX_ROWS + 1)
    ).fetchall()
    if len(found) > MAX_ROWS:
        raise ValueError(f"Cleanup handles up to {MAX_ROWS:,} rows per dataset")
    return [row["source_row_number"] for row in found], [json.loads(row["raw_values_json"]) for row in found]


def _roles(store: ProjectStore, dataset_id: str, columns: list[str]) -> tuple[dict[str, str], bool, bool]:
    """Column roles from the confirmed mapping, or proposed from the headers; whether the records are people;
    and whether the roles came from a confirmed mapping."""
    mapping = latest_mapping(store, dataset_id)
    if mapping is not None:
        roles = json.loads(mapping["mapping_json"])
        return roles, (mapping["entity_type"] or "person") == "person" or any(r in ("first_name", "last_name") for r in roles.values()), True
    proposals = {p.source_field: p.role for p in propose_field_mappings(columns)}
    return proposals, True, False


def scan_dataset(store: ProjectStore, dataset_id: str, region: str = "US") -> dict:
    if store._connection.execute("SELECT 1 FROM datasets WHERE id = ?", (dataset_id,)).fetchone() is None:
        raise ValueError("Dataset not found")
    _, rows = _rows(store, dataset_id)
    columns = list(dict.fromkeys(column for row in rows for column in row))
    roles, person, confirmed = _roles(store, dataset_id, columns)
    report = cleanup.scan(rows, roles, region, person)
    return {**report, "dataset_id": dataset_id, "roles": roles, "roles_confirmed": confirmed, "region": region}


def _validate_apply(store: ProjectStore, params: dict) -> dict:
    dataset = store._connection.execute("SELECT id, name FROM datasets WHERE id = ?", (params.get("dataset_id"),)).fetchone()
    if dataset is None:
        raise JobValidationError("Dataset not found")
    plan = params.get("plan")
    if not isinstance(plan, dict):
        raise JobValidationError("A cleanup plan is required")
    columns = set()
    for row in store._connection.execute("SELECT raw_values_json FROM source_rows WHERE dataset_id = ? LIMIT 200", (dataset["id"],)):
        columns.update(json.loads(row["raw_values_json"]))
    for key in ("standardize", "clear_invalid"):
        unknown = sorted(set(plan.get(key) or []) - columns)
        if unknown:
            raise JobValidationError(f"Unknown columns in {key}: {', '.join(unknown)}")
    for merge in plan.get("merge_values") or []:
        if merge.get("column") not in columns or not isinstance(merge.get("values"), list) or not isinstance(merge.get("to"), str):
            raise JobValidationError("Each merge needs a known column, the values to replace, and the value to keep")
    if set(plan.get("drop_rows") or []) - set(DROP_REASONS):
        raise JobValidationError(f"Rows can be dropped only for: {', '.join(DROP_REASONS)}")
    name = str(params.get("name") or f"{dataset['name']} (cleaned)").strip()[:200]
    return {"dataset_id": dataset["id"], "plan": plan, "name": name, "region": str(params.get("region") or "US")}


def _run_apply(context: JobContext) -> dict:
    store, params = context.store, context.params
    context.stage("reading_file")
    numbers, rows = _rows(store, params["dataset_id"])
    columns = list(dict.fromkeys(column for row in rows for column in row))
    roles, person, _ = _roles(store, params["dataset_id"], columns)
    context.checkpoint()
    context.stage("cleaning_values", {"rows": len(rows)})
    kept, cleaned, summary = cleanup.apply(rows, roles, params["plan"], params["region"], person)
    if not cleaned:
        raise ValueError("The plan removes every row; nothing would be left to save")
    context.checkpoint()
    project_id = store.job(context.job_id)["project_id"]
    derivation = {"cleanup_version": cleanup.CLEANUP_VERSION, "plan": params["plan"], "summary": summary, "region": params["region"]}
    payload = json.dumps({"derivation": derivation, "rows": cleaned}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    created = _register_rows(
        store, project_id, Path(f"{params['name']}.json"), payload, columns, cleaned, params["name"],
        kind="cleaned", row_numbers=[numbers[index] for index in kept],
    )
    store._connection.execute(
        "UPDATE datasets SET parent_dataset_id = ?, derivation_json = ? WHERE id = ?",
        (params["dataset_id"], json.dumps(derivation, sort_keys=True), created.dataset_id),
    )
    store._connection.commit()
    # The cleaned copy keeps the original's column meanings, so it can go straight to duplicate checking.
    mapping = latest_mapping(store, params["dataset_id"])
    mapping_version = None
    if mapping is not None:
        mapping_version = confirm_mapping(
            store, created.dataset_id, json.loads(mapping["mapping_json"]), mapping["entity_type"], json.loads(mapping["export_exclude_json"] or "[]"),
        ).version
    return {"dataset_id": created.dataset_id, "rows": len(cleaned), "removed_rows": len(rows) - len(cleaned), "summary": summary, "mapping_version": mapping_version}


CLEANUP_JOB = JobKind(run=_run_apply, validate=_validate_apply)
