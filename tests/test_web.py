"""The FastAPI surface."""

from __future__ import annotations

import io

import pytest

fastapi_testclient = pytest.importorskip("fastapi.testclient")

from dataforge.web.app import create_app  # noqa: E402


@pytest.fixture
def client():
    return fastapi_testclient.TestClient(create_app())


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_renders(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "DataForge" in response.text


def test_dedupe_endpoint_removes_duplicates(client, leads_frame):
    response = client.post(
        "/api/dedupe",
        json={"records": leads_frame.to_dict(orient="records"), "threshold": 0.8},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["duplicates_removed"] == 3
    assert len(body["records"]) == 3


def test_dedupe_upload_accepts_csv(client, leads_frame):
    csv_bytes = leads_frame.to_csv(index=False).encode()
    response = client.post(
        "/api/dedupe/upload",
        files={"files": ("leads.csv", io.BytesIO(csv_bytes), "text/csv")},
        data={"threshold": "0.8"},
    )
    assert response.status_code == 200
    assert response.json()["summary"]["duplicates_removed"] == 3


def test_upload_rejects_an_unreadable_file(client):
    response = client.post(
        "/api/dedupe/upload",
        files={"files": ("bad.xlsx", io.BytesIO(b"not a spreadsheet"), "application/vnd.ms-excel")},
    )
    assert response.status_code == 400


def test_spiders_and_pipelines_are_listed(client):
    assert "tabular" in client.get("/api/spiders").json()
    assert "dedupe-files" in client.get("/api/pipelines").json()


def test_unknown_pipeline_returns_404(client):
    assert client.post("/api/pipelines/nope/run", json={"params": {}}).status_code == 404
