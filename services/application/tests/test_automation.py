from __future__ import annotations

import csv
from pathlib import Path

import pytest

from dataforge_application.api import Service
from test_workflows import call, ok, wait


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Automation")
    return svc


def test_workflow_crud_and_validation(service: Service, tmp_path: Path) -> None:
    source = tmp_path / "people.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows([["email", "name"], ["ada@example.test", "Ada"]])
    dataset_id = wait(service, ok(service, "dataset.import", path=str(source))["job_id"])["result"]["dataset_id"]

    saved = ok(service, "automation.save", name="Enter leads", start_url="https://portal.example/leads", dataset_id=dataset_id, steps=[
        {"type": "fill", "selector": "input[name=email]", "value": "{{email}}"},
        {"type": "click", "selector": "button.next"},
        {"type": "wait", "milliseconds": 500},
        {"type": "read", "selector": ".confirmation"},
    ])
    assert saved["allowed_host"] == "portal.example"
    assert saved["steps"][0]["value"] == "{{email}}"
    assert ok(service, "automation.list")[0]["id"] == saved["id"]

    updated = ok(service, "automation.save", **{**saved, "name": "Enter contacts"})
    assert updated["id"] == saved["id"] and updated["name"] == "Enter contacts"
    assert ok(service, "automation.delete", workflow_id=saved["id"]) == {"deleted": saved["id"]}
    assert ok(service, "automation.list") == []

    insecure = call(service, "automation.save", name="Bad", start_url="http://example.test", steps=[{"type": "wait"}])
    assert insecure["ok"] is False and "HTTPS" in insecure["error"]["message"]
    secret = call(service, "automation.save", name="Bad", start_url="https://user:secret@example.test", steps=[{"type": "wait"}])
    assert secret["ok"] is False and "credentials" in secret["error"]["message"]
