from __future__ import annotations

from pathlib import Path

import pytest

from dataforge_application.api import Service
from test_scope_review_tools import import_csv, run_full, write_csv
from test_workflows import call, ok, wait

ROWS = [
    ["Name", "Phone", "Email", "Zip", "Company"],
    ["SMITH, JOHN", "512.555.0182", "John@GMIAL.com", "2152", "Acme, Inc."],
    ["John Smith", "(512) 555-0182", "john@gmail.com", "02152", "ACME INC"],
    ["Ana Lopez", "000-000-0000", "noemail@gmail.com", "78701", "ACME INC"],
    ["Test User", "", "test@test.com", "", "Acme Incorporated"],
    ["", "", "", "", ""],
]


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Cleanup")
    return svc


def test_scan_uses_the_confirmed_column_meanings(service: Service, tmp_path: Path) -> None:
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", ROWS))
    proposed = ok(service, "dataset.cleanup_scan", dataset_id=dataset_id)
    assert proposed["roles_confirmed"] is False and proposed["roles"]["Phone"] == "phone"
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person",
       mapping={"Name": "name", "Phone": "phone", "Email": "email", "Zip": "postal_code", "Company": "other"})
    report = ok(service, "dataset.cleanup_scan", dataset_id=dataset_id)
    assert report["roles_confirmed"] is True and report["rows"] == 5
    assert {c["column"] for c in report["columns"]} >= {"Name", "Phone", "Email", "Zip"}
    assert report["junk_rows"] == {**report["junk_rows"], "empty": 1, "test": 1}
    assert any(c["column"] == "Company" and c["rows"] == 4 for c in report["clusters"])


def test_apply_creates_a_cleaned_copy_that_matches_without_touching_the_original(service: Service, tmp_path: Path) -> None:
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", ROWS))
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person",
       mapping={"Name": "name", "Phone": "phone", "Email": "email", "Zip": "postal_code", "Company": "other"})
    plan = {
        "standardize": ["Name", "Phone", "Email", "Zip"], "clear_invalid": ["Phone", "Email"],
        "merge_values": [{"column": "Company", "values": ["Acme, Inc.", "ACME INC", "Acme Incorporated"], "to": "Acme Inc."}],
        "drop_rows": ["empty", "test"],
    }
    job = wait(service, ok(service, "dataset.cleanup_apply", dataset_id=dataset_id, plan=plan)["job_id"])
    assert job["state"] == "completed", job
    result = job["result"]
    assert result["rows"] == 3 and result["removed_rows"] == 2 and result["mapping_version"] == 1

    cleaned = next(d for d in ok(service, "dataset.list") if d["id"] == result["dataset_id"])
    assert cleaned["kind"] == "cleaned" and cleaned["parent_dataset_id"] == dataset_id and cleaned["name"] == "people (cleaned)"
    rows = ok(service, "dataset.rows", dataset_id=result["dataset_id"])
    assert [r["row_number"] for r in rows] == [2, 3, 4]  # original row numbers are kept
    assert rows[0]["raw"] == {"Name": "John Smith", "Phone": "(512) 555-0182", "Email": "john@gmail.com", "Zip": "02152", "Company": "Acme Inc."}
    assert rows[2]["raw"]["Phone"] == "" and rows[2]["raw"]["Email"] == ""
    original = ok(service, "dataset.rows", dataset_id=dataset_id)
    assert original[0]["raw"]["Name"] == "SMITH, JOHN" and len(original) == 5

    # The two John Smith rows were written differently; cleaned, they match automatically.
    job_id = run_full(service, dataset_id=result["dataset_id"])
    assert ok(service, "match.results", job_id=job_id)["decisions"].get("match") == 1


def test_apply_rejects_unknown_columns_and_drop_reasons(service: Service, tmp_path: Path) -> None:
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", ROWS))
    assert "Unknown columns" in call(service, "dataset.cleanup_apply", dataset_id=dataset_id, plan={"standardize": ["Nope"]})["error"]["message"]
    assert "dropped only for" in call(service, "dataset.cleanup_apply", dataset_id=dataset_id, plan={"drop_rows": ["everything"]})["error"]["message"]
