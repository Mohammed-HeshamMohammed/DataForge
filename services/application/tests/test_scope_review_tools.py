from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from dataforge_application.api import Service
from dataforge_application.storage import ProjectStore

from test_workflows import call, ok, wait

GOLDEN = Path(__file__).parent / "golden"


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Scope")
    return svc


def write_csv(path: Path, rows: list[list[str]]) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    return path


def import_csv(service: Service, path: Path) -> str:
    return wait(service, ok(service, "dataset.import", path=str(path))["job_id"])["result"]["dataset_id"]


def run_full(service: Service, **params) -> str:
    wait(service, ok(service, "match.create_job", run_mode="preview", **params)["job_id"])
    job = wait(service, ok(service, "match.create_job", run_mode="full", **params)["job_id"])
    assert job["state"] == "completed", job
    return job["id"]


def read_csv(path: str) -> list[dict]:
    with open(path, encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def test_cross_dataset_matching_with_source_trust_and_exclusions(service: Service, tmp_path: Path) -> None:
    crm = import_csv(service, write_csv(tmp_path / "crm.csv", [["Full Name", "Mobile", "Street", "ZIP", "SSN"], ["Ada Lovelace", "(512) 555-0182", "123 Main St", "78701", "000-00-0001"]]))
    leads = import_csv(service, write_csv(tmp_path / "leads.csv", [
        ["Name", "Phone", "Address", "Zip", "Notes"],
        ["Ada Lovelace", "512-555-0182", "123 Main Street", "78701", "hot"],
        ["Bob Stone", "2125550100", "9 Oak Ave", "10001", ""],
    ]))
    ok(service, "dataset.confirm_mapping", dataset_id=crm, entity_type="person", mapping={"Full Name": "name", "Mobile": "phone", "Street": "address", "ZIP": "postal_code", "SSN": "identifier"}, export_exclude=["SSN"])
    ok(service, "dataset.confirm_mapping", dataset_id=leads, entity_type="business", mapping={"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"})
    assert "same entity type" in call(service, "match.create_job", dataset_id=crm, compare_dataset_id=leads, run_mode="preview")["error"]["message"]
    ok(service, "dataset.confirm_mapping", dataset_id=leads, entity_type="person", mapping={"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"})

    job_id = run_full(service, dataset_id=crm, compare_dataset_id=leads, settings={"source_trust": [crm, leads]})
    results = ok(service, "match.results", job_id=job_id)
    assert results["compare_dataset_id"] == leads
    assert results["metrics"]["decisions_by_scope"]["across"]["match"] == 1
    assert results["canonical_records"] == 2

    export = ok(service, "export.create", job_id=job_id)
    files = {f["kind"]: f["path"] for f in export["files"]}
    canonical = read_csv(files["canonical"])
    ada = next(r for r in canonical if r["member_count"] == "2")
    assert ada["Full Name"] == "Ada Lovelace" and ada["canonical.phone"] == "(512) 555-0182" and ada["Notes"] == "hot"
    assert "SSN" not in canonical[0] and "canonical.identifier" not in canonical[0]
    assert "SSN" not in ada["field_provenance"]
    original = read_csv(files["original"])
    assert {r["source_dataset"] for r in original} == {"crm", "leads"} and len(original) == 3
    assert all("000-00-0001" not in json.dumps(r) for r in canonical + original)

    # Preview staleness includes the comparison scope and trust order.
    assert "stale" in call(service, "match.create_job", dataset_id=crm, compare_dataset_id=leads, run_mode="full", settings={"source_trust": [leads, crm]})["error"]["message"]


def people_dataset(service: Service, tmp_path: Path) -> str:
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", [
        ["Name", "Phone", "Address", "Zip"],
        ["Grace Hopper", "(212) 555-0100", "1 Navy Way", "10001"],
        ["G. Hopper", "(212) 555-0100", "", ""],
        ["Ada Lovelace", "5125550182", "123 Main St", "78701"],
        ["Ada King", "5125550182", "123 Main St", "78701"],
    ]))
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person", mapping={"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"})
    return dataset_id


def test_merge_with_chosen_values_set_value_undo_and_mapping_flags(service: Service, tmp_path: Path) -> None:
    dataset_id = people_dataset(service, tmp_path)
    job_id = run_full(service, dataset_id=dataset_id)
    item = next(i for i in ok(service, "match.review_queue", job_id=job_id)["items"] if "G. Hopper" in (i["left"]["raw"]["Name"], i["right"]["raw"]["Name"]))
    short_row = item["left"] if item["left"]["raw"]["Name"] == "G. Hopper" else item["right"]
    assert item["left"]["source_name"] == "people"
    assert "must come from" in call(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="merge", expected_version=item["review_version"], values={"Name": "not-a-member"})["error"]["message"]

    merged = ok(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="merge", expected_version=item["review_version"], values={"Name": short_row["id"]})
    group = next(g for g in ok(service, "match.clusters", job_id=job_id)["items"] if short_row["id"] in [m["id"] for m in g["members"]])
    assert group["canonical_values"]["Name"] == "G. Hopper" and group["field_provenance"]["Name"]["rule"] == "reviewer_choice"

    other = next(m for m in group["members"] if m["id"] != short_row["id"])
    choice = ok(service, "match.set_canonical_value", job_id=job_id, cluster_id=group["cluster_id"], column="Name", row_id=other["id"])
    group = next(g for g in ok(service, "match.clusters", job_id=job_id)["items"] if g["cluster_id"] == group["cluster_id"])
    assert group["canonical_values"]["Name"] == "Grace Hopper"
    ok(service, "match.undo_canonical_value", job_id=job_id, override_id=choice["override_id"])
    ok(service, "match.undo_review", job_id=job_id, review_action_id=merged["review_action_id"])
    assert all(short_row["id"] not in [m["id"] for m in g["members"]] for g in ok(service, "match.clusters", job_id=job_id)["items"])

    ok(service, "match.flag_mapping", job_id=job_id, column="Name", note="Nicknames create weak candidates", decision_id=item["decision_id"])
    assert ok(service, "dataset.mapping_flags", dataset_id=dataset_id)[0]["column_name"] == "Name"
    assert "not part of the mapping" in call(service, "match.flag_mapping", job_id=job_id, column="Nope", note="")["error"]["message"]
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person", mapping={"Name": "other", "Phone": "phone", "Address": "address", "Zip": "postal_code"})
    assert ok(service, "dataset.mapping_flags", dataset_id=dataset_id) == []

    turnaround = ok(service, "match.results", job_id=job_id)["review_turnaround"]
    assert turnaround["decisions"] == 0  # the only decision was undone
    assert set(ok(service, "match.results", job_id=job_id)["metrics"]["stage_seconds"]) >= {"normalizing", "evaluating_evidence"}


def test_export_matches_golden_files(service: Service, tmp_path: Path) -> None:
    """Exports are reproducible from the same input; ids are replaced with row numbers for comparison."""
    dataset_id = people_dataset(service, tmp_path)
    job_id = run_full(service, dataset_id=dataset_id)
    for pending in ok(service, "match.review_queue", job_id=job_id)["items"]:
        ok(service, "match.submit_review", job_id=job_id, decision_id=pending["decision_id"], action="keep_separate", expected_version=pending["review_version"])
    files = {f["kind"]: f["path"] for f in ok(service, "export.create", job_id=job_id, include_provenance=False)["files"]}
    rows = {r["id"]: r["row_number"] for r in ok(service, "dataset.rows", dataset_id=dataset_id)}

    def stable(path: str) -> list[dict]:
        out = []
        for record in read_csv(path):
            record.pop("cluster_id", None)
            record.pop("source_row_id", None)
            if "source_row_ids" in record:
                record["source_row_ids"] = ";".join(str(rows[i]) for i in record["source_row_ids"].split(";"))
            out.append(record)
        return sorted(out, key=json.dumps)

    actual = {kind: stable(path) for kind, path in files.items() if kind != "audit"}
    golden_path = GOLDEN / "people_export.json"
    if not golden_path.exists():  # first run writes the golden file; review it before committing
        golden_path.parent.mkdir(exist_ok=True)
        golden_path.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")
    assert actual == json.loads(golden_path.read_text(encoding="utf-8"))


def test_project_created_at_schema_004_upgrades_with_backup_and_keeps_data(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    repo_migrations = Path(__file__).parents[3] / "migrations"
    old_migrations = tmp_path / "migrations-004"
    old_migrations.mkdir()
    for path in sorted(repo_migrations.glob("00[1-4]_*.sql")):
        shutil.copy(path, old_migrations / path.name)

    project = tmp_path / "legacy"
    store = ProjectStore(project / "dataforge.sqlite3")
    store.migrate(old_migrations)
    project_id = store.create_project("Legacy", project)
    store._connection.execute(
        "INSERT INTO datasets(id, project_id, name, source_artifact_hash, source_filename, row_count, column_count, created_at) VALUES ('d1', ?, 'Old import', 'abc', 'old.csv', 1, 1, '2026-01-01T00:00:00+00:00')",
        (project_id,),
    )
    store._connection.execute("INSERT INTO source_rows(id, dataset_id, source_row_number, raw_values_json, created_at) VALUES ('r1', 'd1', 2, '{\"Name\":\"Ada\"}', '2026-01-01T00:00:00+00:00')")
    store._connection.commit()
    assert store.schema_version() == 4
    store.close()

    service = Service()
    opened = ok(service, "project.open", path=str(project))
    assert opened["schema_version"] == len(list(repo_migrations.glob("*.sql")))
    backups = list((project / "backups").glob("dataforge-before-005_*.sqlite3"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 4
    assert ok(service, "dataset.list")[0]["kind"] == "import"
    assert ok(service, "dataset.rows", dataset_id="d1")[0]["raw"] == {"Name": "Ada"}
