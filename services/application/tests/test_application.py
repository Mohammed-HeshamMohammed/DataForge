from pathlib import Path

import pytest

from dataforge_application.contracts import health_check
from dataforge_application.datasets import (
    confirm_mapping,
    import_csv,
    import_json,
    import_xlsx,
    profile_dataset,
    propose_field_mappings,
    register_staged_rows,
)
from dataforge_application.storage import ProjectStore


def test_health_contract_is_versioned() -> None:
    result = health_check().to_dict()

    assert result["schema_version"] == 1
    assert result["service"] == "application"
    assert result["status"] == "ok"
    assert result["checked_at"].endswith("+00:00")


def test_project_and_job_state_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Fixture", tmp_path / "project")
    job_id = store.create_job(project_id, "health")
    store.append_event(job_id, "job.created", {"state": "draft"})
    store.close()

    reopened = ProjectStore(database_path)
    assert reopened.job(job_id)["project_id"] == project_id
    assert reopened.job(job_id)["state"] == "draft"
    event_count = reopened._connection.execute(
        "SELECT COUNT(*) FROM job_events WHERE job_id = ?", (job_id,)
    ).fetchone()[0]
    assert event_count == 1
    reopened.close()


def test_job_lifecycle_persists_transitions_and_rejects_invalid_moves(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Lifecycle", tmp_path / "project")
    job_id = store.create_job(project_id, "fixture")

    store.transition_job(job_id, "validating")
    store.transition_job(job_id, "queued")
    store.transition_job(job_id, "running")
    with pytest.raises(ValueError, match="Invalid job transition"):
        store.transition_job(job_id, "draft")
    store.close()

    reopened = ProjectStore(database_path)
    assert reopened.job(job_id)["state"] == "running"
    events = reopened._connection.execute(
        "SELECT event_type, payload_json FROM job_events WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    assert len(events) == 3
    assert '"to_state": "running"' in events[-1]["payload_json"]
    reopened.close()


def test_csv_import_preserves_hash_rows_and_exact_values(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    source_path = tmp_path / "source.csv"
    source_path.write_bytes("Name,Postal Code\nZoë,00123\nAda,90210\n".encode("utf-8"))

    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Import fixture", tmp_path / "project")
    imported = import_csv(store, project_id, source_path)
    store.close()

    reopened = ProjectStore(database_path)
    dataset = reopened._connection.execute(
        "SELECT * FROM datasets WHERE id = ?", (imported.dataset_id,)
    ).fetchone()
    rows = reopened._connection.execute(
        "SELECT source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number",
        (imported.dataset_id,),
    ).fetchall()

    assert dataset["source_artifact_hash"] == imported.source_artifact_hash
    assert dataset["row_count"] == 2
    assert [row["source_row_number"] for row in rows] == [2, 3]
    assert '"Postal Code":"00123"' in rows[0]["raw_values_json"]
    assert '"Name":"Zoë"' in rows[0]["raw_values_json"]
    profiles = profile_dataset(reopened, imported.dataset_id)
    assert profiles[1].name == "Postal Code"
    assert profiles[1].null_rate == 0.0
    assert profiles[1].distinct_count == 2
    assert profiles[1].sample_values == ("00123", "90210")
    reopened.close()


def test_csv_blank_and_duplicate_headers_keep_every_value_and_row_number(tmp_path: Path) -> None:
    source_path = tmp_path / "messy.csv"
    source_path.write_text("Address,,Phone,Phone\n1 A St,x,111,222\n\n2 B St,y,333,444\n", encoding="utf-8")
    store = ProjectStore(tmp_path / "project" / "dataforge.sqlite3")
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Messy", tmp_path / "project")
    imported = import_csv(store, project_id, source_path)
    rows = store._connection.execute(
        "SELECT source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ? ORDER BY source_row_number", (imported.dataset_id,)
    ).fetchall()
    assert [r["source_row_number"] for r in rows] == [2, 4]
    assert rows[0]["raw_values_json"] == '{"Address":"1 A St","column_2":"x","Phone":"111","Phone (2)":"222"}'
    store.close()


def test_json_import_and_mapping_proposals_are_conservative(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    source_path = tmp_path / "source.json"
    source_path.write_text(
        '[{"full name":"Ada Lovelace","email address":"ada@example.test","postal code":"00123"}]',
        encoding="utf-8",
    )

    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("JSON fixture", tmp_path / "project")
    imported = import_json(store, project_id, source_path)

    row = store._connection.execute(
        "SELECT raw_values_json FROM source_rows WHERE dataset_id = ?", (imported.dataset_id,)
    ).fetchone()
    proposals = propose_field_mappings(["full name", "email address", "postal code", "mystery"])

    assert imported.row_count == 1
    assert '"postal code":"00123"' in row["raw_values_json"]
    assert proposals[0].role == "name"
    assert proposals[0].confidence == "proposed"
    assert proposals[1].role == "email"
    assert proposals[3].role is None
    assert proposals[3].confidence == "unmapped"
    store.close()


def test_xlsx_import_and_mapping_versions_are_immutable(tmp_path: Path) -> None:
    from openpyxl import Workbook

    database_path = tmp_path / "project" / "dataforge.sqlite3"
    source_path = tmp_path / "source.xlsx"
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["Name", "Postal Code"])
    worksheet.append(["Ada Lovelace", "00123"])
    workbook.save(source_path)
    workbook.close()

    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("XLSX fixture", tmp_path / "project")
    imported = import_xlsx(store, project_id, source_path)
    first = confirm_mapping(store, imported.dataset_id, {"Name": "name"})
    second = confirm_mapping(store, imported.dataset_id, {"Name": "display_name", "Postal Code": "postal_code"})

    assert imported.row_count == 1
    assert first.version == 1
    assert second.version == 2
    assert store._connection.execute(
        "SELECT COUNT(*) FROM mapping_versions WHERE dataset_id = ?", (imported.dataset_id,)
    ).fetchone()[0] == 2
    store.close()


def test_staged_scrape_rows_keep_provenance_and_are_immutable(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Staged scrape", tmp_path / "project")
    imported = register_staged_rows(
        store,
        project_id,
        [{"title": "Alpha", "source_url": "https://example.test/a", "strategy_used": "http"}],
        "scrape-results.json",
        "Fixture scrape",
    )

    dataset = store._connection.execute(
        "SELECT source_filename, row_count FROM datasets WHERE id = ?", (imported.dataset_id,)
    ).fetchone()
    row = store._connection.execute(
        "SELECT source_row_number, raw_values_json FROM source_rows WHERE dataset_id = ?", (imported.dataset_id,)
    ).fetchone()

    assert dataset["source_filename"] == "scrape-results.json"
    assert dataset["row_count"] == 1
    assert row["source_row_number"] == 1
    assert '"strategy_used":"http"' in row["raw_values_json"]
    store.close()


def test_scrape_run_metadata_and_errors_survive_reopen(tmp_path: Path) -> None:
    database_path = tmp_path / "project" / "dataforge.sqlite3"
    store = ProjectStore(database_path)
    store.migrate(Path(__file__).parents[3] / "migrations")
    project_id = store.create_project("Scrape run", tmp_path / "project")
    job_id = store.create_job(project_id, "scrape")
    scrape_run_id = store.create_scrape_run(
        job_id, "fixture.html_list", "1.0.0", "http", "https://example.test/list"
    )
    store.complete_scrape_run(scrape_run_id, 2, 3, 1, 2)
    store.close()

    reopened = ProjectStore(database_path)
    completed = reopened.scrape_run(scrape_run_id)
    assert completed["status"] == "completed"
    assert completed["pages_fetched"] == 2
    assert completed["records_duplicate"] == 2

    failed_run = reopened.create_scrape_run(
        job_id, "fixture.html_list", "1.0.0", "http", "https://example.test/blocked"
    )
    reopened.fail_scrape_run(failed_run, "policy_violation", "Collection stopped on access response")
    reopened.close()

    recovered = ProjectStore(database_path)
    assert recovered.scrape_run(failed_run)["status"] == "failed"
    error = recovered._connection.execute(
        "SELECT error_type, message FROM scrape_errors WHERE scrape_run_id = ?", (failed_run,)
    ).fetchone()
    assert error["error_type"] == "policy_violation"
    assert "access response" in error["message"]
    recovered.close()
