"""project.overview: totals, activity, runs, attention items, and checklist from real jobs, matching its contract."""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

from dataforge_application.api import Service

from test_contracts import validate
from test_workflows import call, ok, wait

sys.path.insert(0, str(Path(__file__).parents[3] / "workers" / "scraping" / "tests"))
from fixture_site import serve  # noqa: E402


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Overview")
    return svc


def test_empty_project_overview_guides_the_first_steps(service: Service) -> None:
    overview = ok(service, "project.overview")
    validate("project-overview.schema.json", overview)
    assert overview["collection"]["records"] == 0 and len(overview["activity"]) == 14 and overview["attention"] == []
    assert [item["done"] for item in overview["checklist"]] == [False] * 6
    assert overview["app"]["version"] == ok(service, "app.info")["version"]


def test_overview_totals_runs_attention_and_activity(service: Service, tmp_path: Path) -> None:
    base, _, server = serve()
    try:
        common = {"policy_acknowledgement": True, "purpose": "internal_analysis", "preset_id": "generic.html_list", "preset_version": "1.1.0"}
        full = wait(service, ok(service, "scrape.create_job", start_url=base + "/catalog", run_mode="full", detail_level="full", **common)["job_id"], 120)
        assert full["state"] == "completed", full
        wait(service, ok(service, "scrape.create_job", start_url=base + "/catalog", run_mode="test", detail_level="standard", **common)["job_id"], 120)
        failed = wait(service, ok(service, "scrape.create_job", start_url=base + "/challenge", run_mode="full", **common)["job_id"], 120)
        assert failed["state"] == "failed"
    finally:
        server.shutdown()
    people = tmp_path / "people.csv"
    with people.open("w", newline="") as handle:
        csv.writer(handle).writerows([["Name", "Phone"], ["Ada", "512-555-0100"], ["Bo", "512-555-0101"]])
    wait(service, ok(service, "dataset.import", path=str(people))["job_id"])

    overview = ok(service, "project.overview", days=7, tz_offset_minutes=-180)
    validate("project-overview.schema.json", overview)
    collection = overview["collection"]
    assert (collection["runs"], collection["full_runs"], collection["test_runs"], collection["failed_runs"]) == (3, 2, 1, 1)
    assert collection["records"] == 5 and collection["detail_pages"] == 2 and collection["records_last_7_days"] == 5 and collection["hosts"] == 1
    assert len(overview["activity"]) == 7 and overview["activity"][-1]["records"] == 5 and overview["activity"][-1]["runs"] == 3
    assert overview["activity"][-1]["failed"] == 1 and overview["activity"][-1]["detail_pages"] == 2
    latest = overview["recent_runs"][0]
    assert latest["status"] == "failed" and "access challenge" in latest["error"]
    completed = next(r for r in overview["recent_runs"] if r["run_mode"] == "full" and r["status"] == "completed")
    assert completed["detail_level"] == "full" and completed["detail_pages"] == 2 and completed["detail_fields"] > 30 and completed["dataset_id"]
    assert overview["by_source"] == [{"source": "website", "records": 5, "runs": 1}]
    assert overview["datasets"]["count"] == 2 and overview["datasets"]["scraped"] == 1 and overview["datasets"]["unmapped"] == 2
    kinds = [item["kind"] for item in overview["attention"]]
    assert kinds[0] == "failed_jobs" and "mapping" in kinds
    checklist = {item["id"]: item["done"] for item in overview["checklist"]}
    assert checklist["collect"] and checklist["details"] and not checklist["mapping"]
    assert overview["storage"]["total_bytes"] >= overview["storage"]["database_bytes"] > 0
    assert overview["presets"]["total"] >= 20 and overview["jobs"]["counts"]["failed"] == 1


def test_overview_needs_a_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    assert call(Service(), "project.overview")["error"]["code"] == "no_project"
