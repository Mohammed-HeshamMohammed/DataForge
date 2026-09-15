"""MatchService and ExportService: orchestrate the matching worker, persist decisions, review, and export."""

from __future__ import annotations

import csv
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from dataforge_matching import SCHEMA_VERSION as MATCH_SCHEMA_VERSION
from dataforge_matching import engine, ranking
from dataforge_matching.normalize import normalize_row

from .datasets import SENSITIVE_ROLES, latest_mapping
from .jobs import JobCancelled, JobContext, JobKind, JobValidationError
from .storage import ProjectStore, utc_now

DEFAULT_PREVIEW_SIZE = 10_000


class ReviewConflict(ValueError):
    """The item changed since the reviewer loaded it."""


def _config_hash(mapping_version_id: str, settings: dict) -> str:
    relevant = {k: v for k, v in settings.items() if k != "preview_size"}
    return hashlib.sha256(json.dumps([mapping_version_id, relevant], sort_keys=True).encode()).hexdigest()[:16]


def validate_match_job(store: ProjectStore, params: dict) -> dict:
    dataset_id = params.get("dataset_id")
    mapping = latest_mapping(store, dataset_id) if dataset_id else None
    if mapping is None:
        raise JobValidationError("Confirm a field mapping for this dataset before matching")
    run_mode = params.get("run_mode", "preview")
    if run_mode not in ("preview", "full"):
        raise JobValidationError("run_mode must be preview or full")
    settings = {**engine.DEFAULT_SETTINGS, **(params.get("settings") or {})}
    settings["preview_size"] = int(settings.get("preview_size", DEFAULT_PREVIEW_SIZE))
    request = {"schema_version": MATCH_SCHEMA_VERSION, "mapping": json.loads(mapping["mapping_json"]), "settings": {k: v for k, v in settings.items() if k != "preview_size"}}
    try:
        engine.validate_request(request)
    except engine.MatchRequestError as error:
        raise JobValidationError(str(error)) from error
    config_hash = _config_hash(mapping["id"], settings)
    if run_mode == "full":
        preview = store._connection.execute(
            """SELECT 1 FROM match_runs r JOIN jobs j ON j.id = r.job_id
               WHERE r.dataset_id = ? AND r.run_mode = 'preview' AND r.config_hash = ? AND j.state = 'completed' LIMIT 1""",
            (dataset_id, config_hash),
        ).fetchone()
        if preview is None:
            raise JobValidationError("The preview is missing or stale: run a preview with the current mapping and settings first")
    return {**params, "run_mode": run_mode, "settings": settings, "mapping_version_id": mapping["id"], "mapping_version": mapping["version"], "config_hash": config_hash}


def _load_rows(store: ProjectStore, dataset_id: str, limit: int | None, row_ids: list[str] | None = None) -> list[dict]:
    if row_ids is not None:
        wanted = set(row_ids)
    query = "SELECT id, source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number"
    args: tuple = (dataset_id,)
    if limit is not None:
        query += " LIMIT ?"
        args += (limit,)
    rows = [{"id": r[0], "row_number": r[1], "raw": json.loads(r[2])} for r in store._connection.execute(query, args)]
    return [r for r in rows if row_ids is None or r["id"] in wanted]


def _locked_groups(store: ProjectStore, dataset_id: str) -> list[list[str]]:
    return [json.loads(r[0]) for r in store._connection.execute(
        "SELECT member_row_ids_json FROM cluster_actions WHERE dataset_id = ? AND action = 'lock' AND reversed_at IS NULL ORDER BY created_at", (dataset_id,)
    )]


def _constraints(store: ProjectStore, dataset_id: str) -> list[dict]:
    return [dict(r) for r in store._connection.execute(
        "SELECT id, left_row_id, right_row_id, kind FROM match_constraints WHERE dataset_id = ? AND revoked_at IS NULL", (dataset_id,)
    )]


def run_match_job(context: JobContext) -> dict:
    store, params = context.store, context.params
    mapping_row = store._connection.execute("SELECT * FROM mapping_versions WHERE id = ?", (params["mapping_version_id"],)).fetchone()
    limit = params["settings"]["preview_size"] if params["run_mode"] == "preview" else None
    rows = _load_rows(store, params["dataset_id"], limit)
    context.stage("loading", {"rows": len(rows)})
    request = {
        "schema_version": MATCH_SCHEMA_VERSION,
        "entity_type": mapping_row["entity_type"] or "custom",
        "mapping": json.loads(mapping_row["mapping_json"]),
        "rows": rows,
        "settings": {k: v for k, v in params["settings"].items() if k != "preview_size"},
        "constraints": _constraints(store, params["dataset_id"]),
        "locked_groups": _locked_groups(store, params["dataset_id"]),
    }
    result = engine.run(request, progress=context.stage, should_stop=context.should_stop)
    if "stopped_at" in result:
        raise JobCancelled()
    context.checkpoint()
    job_id = context.job_id
    # Publish everything in one transaction: readers see all of the job output or none of it.
    with store._connection:
        store._connection.execute(
            "INSERT INTO match_runs(job_id, dataset_id, mapping_version_id, run_mode, config_hash, policy_version, row_ids_json, metrics_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (job_id, params["dataset_id"], params["mapping_version_id"], params["run_mode"], params["config_hash"], result["policy_version"],
             json.dumps([r["id"] for r in rows]), json.dumps(result["metrics"]), utc_now()),
        )
        store._connection.executemany(
            "INSERT INTO match_decisions(job_id, id, left_row_id, right_row_id, decision, score, reason, block_ids_json, evidence_json) VALUES (?,?,?,?,?,?,?,?,?)",
            [(job_id, d["id"], d["left_row_id"], d["right_row_id"], d["decision"], d["score"], d["reason"], json.dumps(d["candidate_block_ids"]), json.dumps(d["evidence"])) for d in result["decisions"]],
        )
        _write_clusters(store, job_id, result["clusters"], result["canonical"])
    return {
        "run_mode": params["run_mode"], "policy_version": result["policy_version"], "normalization_version": result["normalization_version"],
        "mapping_version": params["mapping_version"], "metrics": result["metrics"],
    }


def _write_clusters(store: ProjectStore, job_id: str, clusters: list[dict], canonical: list[dict]) -> None:
    store._connection.execute("DELETE FROM clusters WHERE job_id = ?", (job_id,))
    store._connection.execute("DELETE FROM canonical_records WHERE job_id = ?", (job_id,))
    store._connection.executemany(
        "INSERT INTO clusters(job_id, id, member_row_ids_json, member_count, confidence, status) VALUES (?,?,?,?,?,?)",
        [(job_id, c["id"], json.dumps(c["member_row_ids"]), len(c["member_row_ids"]), c["confidence"], c["status"]) for c in clusters],
    )
    store._connection.executemany(
        "INSERT INTO canonical_records(job_id, cluster_id, survivor_row_id, values_json, provenance_json, conflicts_json) VALUES (?,?,?,?,?,?)",
        [(job_id, r["cluster_id"], r["survivor_row_id"], json.dumps(r["values"], ensure_ascii=False), json.dumps(r["field_provenance"]), json.dumps(r["conflicts"])) for r in canonical],
    )


MATCH_JOB = JobKind(run=run_match_job, validate=validate_match_job)


def _run(store: ProjectStore, job_id: str):
    run = store._connection.execute("SELECT * FROM match_runs WHERE job_id = ?", (job_id,)).fetchone()
    if run is None:
        raise ValueError("Match results are not available for this job (it may still be running or has failed)")
    return run


def _pending_clause() -> str:
    return """d.decision = 'possible_match' AND NOT EXISTS (
        SELECT 1 FROM match_constraints c WHERE c.dataset_id = r.dataset_id AND c.revoked_at IS NULL
        AND c.left_row_id = d.left_row_id AND c.right_row_id = d.right_row_id)"""


def results(store: ProjectStore, job_id: str) -> dict:
    run = _run(store, job_id)
    counts = dict(store._connection.execute("SELECT decision, COUNT(*) FROM match_decisions WHERE job_id = ? GROUP BY decision", (job_id,)).fetchall())
    pending = store._connection.execute(
        f"SELECT COUNT(*) FROM match_decisions d JOIN match_runs r ON r.job_id = d.job_id WHERE d.job_id = ? AND {_pending_clause()}", (job_id,)
    ).fetchone()[0]
    reviewed = store._connection.execute("SELECT action, COUNT(*) FROM review_actions WHERE job_id = ? AND reversed_at IS NULL GROUP BY action", (job_id,)).fetchall()
    cluster_stats = store._connection.execute(
        "SELECT COUNT(*), SUM(member_count > 1), COALESCE(SUM(CASE WHEN member_count > 1 THEN member_count - 1 END), 0) FROM clusters WHERE job_id = ?", (job_id,)
    ).fetchone()
    samples = []
    for decision in ("match", "possible_match"):
        for d in store._connection.execute(
            "SELECT id, left_row_id, right_row_id, decision, score, reason FROM match_decisions WHERE job_id = ? AND decision = ? ORDER BY score DESC, id LIMIT 5",
            (job_id, decision),
        ):
            samples.append(dict(d))
    exports = [dict(e) for e in store._connection.execute("SELECT id, kind, path, sha256, row_count, is_final, include_provenance, created_at FROM exports WHERE job_id = ? ORDER BY created_at DESC", (job_id,))]
    return {
        "job_id": job_id, "dataset_id": run["dataset_id"], "run_mode": run["run_mode"], "policy_version": run["policy_version"],
        "mapping_version_id": run["mapping_version_id"], "metrics": json.loads(run["metrics_json"]),
        "decisions": counts, "pending_review": pending, "reviewed": dict(reviewed),
        "canonical_records": cluster_stats[0], "merged_groups": cluster_stats[1] or 0, "rows_suppressed": cluster_stats[2],
        "samples": samples, "exports": exports,
    }


def _sensitive_columns(store: ProjectStore, mapping_version_id: str) -> list[str]:
    row = store._connection.execute("SELECT mapping_json FROM mapping_versions WHERE id = ?", (mapping_version_id,)).fetchone()
    return sorted(col for col, role in json.loads(row["mapping_json"]).items() if role in SENSITIVE_ROLES) if row else []


def latest_ranking_model(store: ProjectStore, dataset_id: str) -> dict | None:
    row = store._connection.execute("SELECT * FROM ranking_models WHERE dataset_id = ? ORDER BY created_at DESC LIMIT 1", (dataset_id,)).fetchone()
    if row is None:
        return None
    return {**{k: row[k] for k in ("id", "model_version", "feature_version", "training_hash", "label_count", "created_at")},
            "weights": json.loads(row["weights_json"]), "evaluation": json.loads(row["evaluation_json"])}


def train_ranking(store: ProjectStore, job_id: str) -> dict:
    run = _run(store, job_id)
    labeled = store._connection.execute(
        """SELECT d.evidence_json, d.score, a.action FROM review_actions a
           JOIN match_decisions d ON d.job_id = a.job_id AND d.id = a.decision_id
           JOIN match_runs r ON r.job_id = a.job_id
           WHERE r.dataset_id = ? AND a.reversed_at IS NULL""",
        (run["dataset_id"],),
    ).fetchall()
    model = ranking.train([(json.loads(e), s, 1 if action == "merge" else 0) for e, s, action in labeled])
    model_id = str(uuid4())
    store._connection.execute(
        "INSERT INTO ranking_models(id, dataset_id, model_version, feature_version, training_hash, label_count, weights_json, evaluation_json, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (model_id, run["dataset_id"], model["model_version"], model["feature_version"], model["training_hash"], len(labeled), json.dumps(model["weights"]), json.dumps(model["evaluation"]), utc_now()),
    )
    store._connection.commit()
    return {"id": model_id, "model_version": model["model_version"], "label_count": len(labeled), "evaluation": model["evaluation"]}


def review_queue(store: ProjectStore, job_id: str, offset: int = 0, limit: int = 20, order: str = "score") -> dict:
    run = _run(store, job_id)
    total = store._connection.execute(
        f"SELECT COUNT(*) FROM match_decisions d JOIN match_runs r ON r.job_id = d.job_id WHERE d.job_id = ? AND {_pending_clause()}", (job_id,)
    ).fetchone()[0]
    model = latest_ranking_model(store, run["dataset_id"]) if order == "model" else None
    if order == "model" and model is None:
        raise ValueError("No ranking model has been trained for this dataset yet")
    if model:
        # Ordering only: every pending item stays in the queue and still needs a human decision.
        pending = store._connection.execute(
            f"SELECT d.* FROM match_decisions d JOIN match_runs r ON r.job_id = d.job_id WHERE d.job_id = ? AND {_pending_clause()}", (job_id,)
        ).fetchall()
        scored = sorted(((ranking.predict(model["weights"], ranking.features(json.loads(d["evidence_json"]), d["score"])), d) for d in pending), key=lambda p: (-p[0], p[1]["id"]))
        model_scores = {d["id"]: round(p, 3) for p, d in scored}
        decisions = [d for _, d in scored[offset:offset + limit]]
    else:
        model_scores = {}
        decisions = store._connection.execute(
            f"""SELECT d.* FROM match_decisions d JOIN match_runs r ON r.job_id = d.job_id
                WHERE d.job_id = ? AND {_pending_clause()} ORDER BY d.score DESC, d.id LIMIT ? OFFSET ?""",
            (job_id, limit, offset),
        ).fetchall()
    row_ids = {d["left_row_id"] for d in decisions} | {d["right_row_id"] for d in decisions}
    rows = {}
    if row_ids:
        placeholders = ",".join("?" * len(row_ids))
        for r in store._connection.execute(f"SELECT id, source_row_number, raw_values_json FROM source_rows WHERE id IN ({placeholders})", tuple(row_ids)):
            rows[r["id"]] = {"id": r["id"], "row_number": r["source_row_number"], "raw": json.loads(r["raw_values_json"])}
    items = [{
        "decision_id": d["id"], "score": d["score"], "reason": d["reason"], "review_version": d["review_version"],
        "evidence": json.loads(d["evidence_json"]), "left": rows.get(d["left_row_id"]), "right": rows.get(d["right_row_id"]),
        "can_merge": not any(e["strength"] == "guard" for e in json.loads(d["evidence_json"])),
        "model_score": model_scores.get(d["id"]),
    } for d in decisions]
    model_info = latest_ranking_model(store, run["dataset_id"])
    return {
        "total": total, "offset": offset, "items": items, "order": order, "sensitive_columns": _sensitive_columns(store, run["mapping_version_id"]),
        "ranking_model": None if model_info is None else {k: v for k, v in model_info.items() if k != "weights"},
    }


def _recluster(store: ProjectStore, job_id: str) -> None:
    run = _run(store, job_id)
    mapping = json.loads(store._connection.execute("SELECT mapping_json FROM mapping_versions WHERE id = ?", (run["mapping_version_id"],)).fetchone()[0])
    rows = _load_rows(store, run["dataset_id"], None, json.loads(run["row_ids_json"]))
    active = {col: role for col, role in mapping.items() if role not in ("other", "ignore")}
    normalized = {r["id"]: normalize_row(r["raw"], active) for r in rows}
    decisions = [dict(d) for d in store._connection.execute("SELECT id, left_row_id, right_row_id, decision, score FROM match_decisions WHERE job_id = ?", (job_id,))]
    clusters, bridges, _ = engine.cluster([r["id"] for r in rows], normalized, decisions, _constraints(store, run["dataset_id"]), _locked_groups(store, run["dataset_id"]))
    canonical = engine.canonicalize(clusters, {r["id"]: r for r in rows}, normalized)
    store._connection.executemany(
        "UPDATE match_decisions SET decision = 'possible_match', reason = 'Needs review: would bridge two existing groups with a single link' WHERE job_id = ? AND id = ?",
        [(job_id, b) for b in bridges],
    )
    _write_clusters(store, job_id, clusters, canonical)


def submit_review(store: ProjectStore, job_id: str, decision_id: str, action: str, expected_version: int) -> dict:
    if action not in ("merge", "keep_separate"):
        raise ValueError("action must be merge or keep_separate")
    run = _run(store, job_id)
    job = store.job(job_id)
    if job["state"] != "completed":
        raise ValueError("Only completed jobs can be reviewed")
    decision = store._connection.execute("SELECT * FROM match_decisions WHERE job_id = ? AND id = ?", (job_id, decision_id)).fetchone()
    if decision is None:
        raise ValueError("Unknown review item")
    if decision["review_version"] != expected_version:
        raise ReviewConflict("This item was changed by another decision. Refresh the item before deciding.")
    if action == "merge" and any(e["strength"] == "guard" for e in json.loads(decision["evidence_json"])):
        raise ValueError("Cannot merge: a hard contradiction guard prohibits this merge")
    review_id = str(uuid4())
    with store._connection:
        updated = store._connection.execute(
            "UPDATE match_decisions SET review_version = review_version + 1 WHERE job_id = ? AND id = ? AND review_version = ?",
            (job_id, decision_id, expected_version),
        ).rowcount
        if updated != 1:
            raise ReviewConflict("This item was changed by another decision. Refresh the item before deciding.")
        store._connection.execute("INSERT INTO review_actions(id, job_id, decision_id, action, created_at) VALUES (?,?,?,?,?)", (review_id, job_id, decision_id, action, utc_now()))
        store._connection.execute(
            "INSERT INTO match_constraints(id, dataset_id, left_row_id, right_row_id, kind, review_action_id, created_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid4()), run["dataset_id"], decision["left_row_id"], decision["right_row_id"], "must_link" if action == "merge" else "must_not_link", review_id, utc_now()),
        )
        _recluster(store, job_id)
    store.append_event(job_id, "review.decision_recorded", {"decision_id": decision_id, "action": action, "review_action_id": review_id})
    return {"review_action_id": review_id, "review_version": expected_version + 1}


def undo_review(store: ProjectStore, job_id: str, review_action_id: str) -> dict:
    action = store._connection.execute("SELECT * FROM review_actions WHERE id = ? AND job_id = ? AND reversed_at IS NULL", (review_action_id, job_id)).fetchone()
    if action is None:
        raise ValueError("Review action not found or already undone")
    with store._connection:
        store._connection.execute("UPDATE review_actions SET reversed_at = ? WHERE id = ?", (utc_now(), review_action_id))
        store._connection.execute("UPDATE match_constraints SET revoked_at = ? WHERE review_action_id = ?", (utc_now(), review_action_id))
        store._connection.execute("UPDATE match_decisions SET review_version = review_version + 1 WHERE job_id = ? AND id = ?", (job_id, action["decision_id"]))
        _recluster(store, job_id)
    store.append_event(job_id, "review.decision_reversed", {"review_action_id": review_action_id})
    return {"undone": review_action_id}


def review_history(store: ProjectStore, job_id: str) -> list[dict]:
    return [dict(r) for r in store._connection.execute("SELECT * FROM review_actions WHERE job_id = ? ORDER BY created_at DESC", (job_id,))]


# --- cluster actions -------------------------------------------------------------------------------

def list_clusters(store: ProjectStore, job_id: str, offset: int = 0, limit: int = 20) -> dict:
    run = _run(store, job_id)
    total = store._connection.execute("SELECT COUNT(*) FROM clusters WHERE job_id = ? AND member_count > 1", (job_id,)).fetchone()[0]
    clusters = store._connection.execute(
        """SELECT c.*, k.survivor_row_id FROM clusters c JOIN canonical_records k ON k.job_id = c.job_id AND k.cluster_id = c.id
           WHERE c.job_id = ? AND c.member_count > 1 ORDER BY c.member_count DESC, c.id LIMIT ? OFFSET ?""",
        (job_id, limit, offset),
    ).fetchall()
    locks = {
        tuple(sorted(json.loads(r["member_row_ids_json"]))): r["id"]
        for r in store._connection.execute("SELECT id, member_row_ids_json FROM cluster_actions WHERE dataset_id = ? AND action = 'lock' AND reversed_at IS NULL", (run["dataset_id"],))
    }
    items = []
    for c in clusters:
        member_ids = json.loads(c["member_row_ids_json"])
        rows = _load_rows(store, run["dataset_id"], None, member_ids)
        items.append({
            "cluster_id": c["id"], "member_count": c["member_count"], "confidence": c["confidence"], "status": c["status"],
            "survivor_row_id": c["survivor_row_id"], "lock_action_id": locks.get(tuple(sorted(member_ids))), "members": rows,
        })
    return {"total": total, "offset": offset, "items": items, "sensitive_columns": _sensitive_columns(store, run["mapping_version_id"])}


def _completed_full_run(store: ProjectStore, job_id: str):
    run = _run(store, job_id)
    if store.job(job_id)["state"] != "completed":
        raise ValueError("Only completed jobs can be changed")
    return run


def _cluster_members(store: ProjectStore, job_id: str, cluster_id: str) -> list[str]:
    row = store._connection.execute("SELECT member_row_ids_json FROM clusters WHERE job_id = ? AND id = ?", (job_id, cluster_id)).fetchone()
    if row is None:
        raise ReviewConflict("This group changed since it was loaded. Refresh and try again.")
    return json.loads(row[0])


def split_cluster(store: ProjectStore, job_id: str, cluster_id: str, row_ids: list[str]) -> dict:
    run = _completed_full_run(store, job_id)
    members = _cluster_members(store, job_id, cluster_id)
    selected = [r for r in members if r in set(row_ids)]
    remaining = [r for r in members if r not in set(row_ids)]
    if not selected or not remaining:
        raise ValueError("Select at least one member to separate, and leave at least one member in the group")
    if any(tuple(sorted(g)) == tuple(sorted(members)) for g in _locked_groups(store, run["dataset_id"])):
        raise ValueError("Unlock this group before splitting it")
    action_id = str(uuid4())
    with store._connection:
        store._connection.execute(
            "INSERT INTO cluster_actions(id, job_id, dataset_id, action, member_row_ids_json, separated_row_ids_json, created_at) VALUES (?,?,?,?,?,?,?)",
            (action_id, job_id, run["dataset_id"], "split", json.dumps(members), json.dumps(selected), utc_now()),
        )
        store._connection.executemany(
            "INSERT INTO match_constraints(id, dataset_id, left_row_id, right_row_id, kind, cluster_action_id, created_at) VALUES (?,?,?,?,?,?,?)",
            [(str(uuid4()), run["dataset_id"], *sorted((a, b)), "must_not_link", action_id, utc_now()) for a in selected for b in remaining],
        )
        _recluster(store, job_id)
    store.append_event(job_id, "cluster.split", {"cluster_action_id": action_id, "separated": len(selected)})
    return {"cluster_action_id": action_id}


def lock_cluster(store: ProjectStore, job_id: str, cluster_id: str) -> dict:
    run = _completed_full_run(store, job_id)
    members = _cluster_members(store, job_id, cluster_id)
    action_id = str(uuid4())
    with store._connection:
        store._connection.execute(
            "INSERT INTO cluster_actions(id, job_id, dataset_id, action, member_row_ids_json, created_at) VALUES (?,?,?,?,?,?)",
            (action_id, job_id, run["dataset_id"], "lock", json.dumps(members), utc_now()),
        )
        _recluster(store, job_id)
    store.append_event(job_id, "cluster.locked", {"cluster_action_id": action_id, "members": len(members)})
    return {"cluster_action_id": action_id}


def undo_cluster_action(store: ProjectStore, job_id: str, cluster_action_id: str) -> dict:
    """Unlock a group or reverse a split."""
    action = store._connection.execute("SELECT * FROM cluster_actions WHERE id = ? AND job_id = ? AND reversed_at IS NULL", (cluster_action_id, job_id)).fetchone()
    if action is None:
        raise ValueError("Group action not found or already reversed")
    with store._connection:
        store._connection.execute("UPDATE cluster_actions SET reversed_at = ? WHERE id = ?", (utc_now(), cluster_action_id))
        store._connection.execute("UPDATE match_constraints SET revoked_at = ? WHERE cluster_action_id = ?", (utc_now(), cluster_action_id))
        _recluster(store, job_id)
    store.append_event(job_id, "cluster.action_reversed", {"cluster_action_id": cluster_action_id, "action": action["action"]})
    return {"reversed": cluster_action_id}


def cluster_history(store: ProjectStore, job_id: str) -> list[dict]:
    return [dict(r) for r in store._connection.execute("SELECT * FROM cluster_actions WHERE job_id = ? ORDER BY created_at DESC", (job_id,))]


# --- exports ---------------------------------------------------------------------------------------

def _write_csv(path: Path, header: list[str], rows: list[list[object]]) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    temporary.replace(path)


def create_export(store: ProjectStore, job_id: str, include_provenance: bool = True, allow_unresolved: bool = False) -> dict:
    run = _run(store, job_id)
    if store.job(job_id)["state"] != "completed":
        raise ValueError("Exports are only available for completed jobs")
    if run["run_mode"] != "full":
        raise ValueError("Previews cannot be exported; start a full job first")
    summary = results(store, job_id)
    unresolved = summary["pending_review"]
    if unresolved and not allow_unresolved:
        raise ValueError(f"{unresolved} review items are unresolved. Resolve them or explicitly export with unresolved records.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = store.project_root / "exports" / job_id / (stamp + ("-unresolved" if unresolved else ""))
    directory.mkdir(parents=True, exist_ok=True)
    rows = {r["id"]: r for r in _load_rows(store, run["dataset_id"], None, json.loads(run["row_ids_json"]))}
    columns = list(dict.fromkeys(col for r in rows.values() for col in r["raw"]))
    clusters = {c["id"]: c for c in store._connection.execute("SELECT * FROM clusters WHERE job_id = ?", (job_id,))}
    canonical = store._connection.execute("SELECT * FROM canonical_records WHERE job_id = ? ORDER BY cluster_id", (job_id,)).fetchall()
    status_col = ["export_status"] if unresolved else []
    status_val = ["unresolved_review_pending"] if unresolved else []
    meta = ["cluster_id", "member_count", "match_confidence", "source_row_ids"]

    canonical_rows, clean_rows = [], []
    for record in canonical:
        cluster = clusters[record["cluster_id"]]
        members = json.loads(cluster["member_row_ids_json"])
        values = json.loads(record["values_json"])
        base = [record["cluster_id"], cluster["member_count"], cluster["confidence"], ";".join(members)]
        provenance = [record["provenance_json"]] if include_provenance else []
        canonical_rows.append([values.get(c, "") for c in columns] + base + provenance + status_val)
        survivor = rows[record["survivor_row_id"]]
        clean_rows.append([survivor["raw"].get(c, "") for c in columns] + base + ([survivor["id"], survivor["row_number"]] if include_provenance else []) + status_val)
    prov_header = ["field_provenance"] if include_provenance else []
    outputs = {
        "canonical": ("canonical.csv", columns + meta + prov_header + status_col, canonical_rows),
        "clean": ("clean.csv", columns + meta + (["survivor_row_id", "source_row_number"] if include_provenance else []) + status_col, clean_rows),
        "original": ("original.csv", ["source_row_id", "source_row_number"] + columns,
                     [[r["id"], r["row_number"]] + [r["raw"].get(c, "") for c in columns] for r in sorted(rows.values(), key=lambda r: r["row_number"])]),
    }
    decisions = store._connection.execute("SELECT id, left_row_id, right_row_id, decision, score, reason, block_ids_json, evidence_json FROM match_decisions WHERE job_id = ? AND (decision != 'non_match' OR reason LIKE 'Cannot auto-merge%')", (job_id,)).fetchall()
    audit = {
        "schema_version": 1, "job_id": job_id, "dataset_id": run["dataset_id"], "mapping_version_id": run["mapping_version_id"],
        "policy_version": run["policy_version"], "is_final": not unresolved, "unresolved_review_items": unresolved, "metrics": summary["metrics"],
        "decisions": [{**{k: d[k] for k in ("id", "left_row_id", "right_row_id", "decision", "score", "reason")}, "candidate_block_ids": json.loads(d["block_ids_json"]), "evidence": json.loads(d["evidence_json"])} for d in decisions],
        "review_actions": review_history(store, job_id),
        "cluster_actions": cluster_history(store, job_id),
    }

    created = []
    with store._connection:
        for kind, (filename, header, data) in outputs.items():
            path = directory / filename
            _write_csv(path, header, data)
            created.append(_register_export(store, job_id, kind, path, len(data), not unresolved, include_provenance))
        audit_path = directory / "audit.json"
        audit_path.with_suffix(".tmp").write_text(json.dumps(audit, indent=2, ensure_ascii=False), encoding="utf-8")
        audit_path.with_suffix(".tmp").replace(audit_path)
        created.append(_register_export(store, job_id, "audit", audit_path, len(audit["decisions"]), not unresolved, include_provenance))
    store.append_event(job_id, "export.created", {"directory": str(directory), "is_final": not unresolved})
    return {"directory": str(directory), "is_final": not unresolved, "files": created}


def _register_export(store: ProjectStore, job_id: str, kind: str, path: Path, row_count: int, is_final: bool, include_provenance: bool) -> dict:
    record = {
        "id": str(uuid4()), "job_id": job_id, "kind": kind, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "row_count": row_count, "is_final": int(is_final), "include_provenance": int(include_provenance), "created_at": utc_now(),
    }
    store._connection.execute(f"INSERT INTO exports({', '.join(record)}) VALUES ({', '.join('?' * len(record))})", tuple(record.values()))
    return record
