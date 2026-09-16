"""Contract tests: live service responses and worker results must match packages/contracts schemas."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from dataforge_application.api import Service
from dataforge_matching import engine

from test_scope_review_tools import people_dataset, run_full
from test_workflows import call, ok

CONTRACTS = Path(__file__).parents[3] / "packages" / "contracts"
PRESETS = Path(__file__).parents[3] / "packages" / "presets"


def _registry() -> Registry:
    resources = []
    for path in CONTRACTS.glob("*.schema.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        resource = Resource.from_contents(schema)
        resources += [(schema["$id"], resource), (path.name, resource)]
    return Registry().with_resources(resources)


def validate(name: str, value: object) -> None:
    schema = json.loads((CONTRACTS / name).read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema, registry=_registry()).iter_errors(value), key=lambda e: e.path)
    assert not errors, [f"{list(e.path)}: {e.message}" for e in errors[:5]]


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Contracts")
    return svc


def test_schemas_are_valid_json_schema() -> None:
    for path in CONTRACTS.glob("*.schema.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_every_response_envelope_matches_the_contract(service: Service, tmp_path: Path) -> None:
    for request in (
        {"schema_version": 1, "command": "health.check"},
        {"schema_version": 1, "command": "dataset.list"},
        {"schema_version": 1, "command": "nope.nope"},
        {"schema_version": 7, "command": "health.check"},
        {"schema_version": 1, "command": "dataset.rows", "payload": {}},
    ):
        validate("envelope.schema.json", service.handle(request))
    validate("health.schema.json", ok(service, "health.check"))


def test_jobs_events_review_queue_and_worker_results_match_contracts(service: Service, tmp_path: Path) -> None:
    dataset_id = people_dataset(service, tmp_path)
    job_id = run_full(service, dataset_id=dataset_id)
    job = ok(service, "job.get", job_id=job_id)
    validate("job.schema.json", job)
    for listed in ok(service, "job.list"):
        validate("job.schema.json", listed)
    queue = ok(service, "match.review_queue", job_id=job_id)
    assert queue["items"]
    validate("review-queue.schema.json", queue)

    rows = [{"id": r["id"], "row_number": r["row_number"], "raw": r["raw"]} for r in ok(service, "dataset.rows", dataset_id=dataset_id)]
    result = engine.run({"schema_version": 1, "mapping": {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"}, "rows": rows, "settings": {}})
    validate("match-worker-result.schema.json", result)


def test_bundled_presets_match_the_structural_contract() -> None:
    for path in PRESETS.glob("*.json"):
        validate("preset.schema.json", json.loads(path.read_text(encoding="utf-8-sig")))


def test_contract_rejects_leaked_secrets_in_job_params() -> None:
    with pytest.raises(AssertionError):
        validate("job.schema.json", {"id": "j", "project_id": "p", "kind": "scrape", "state": "queued", "created_at": "2026-01-01T00:00:00Z", "updated_at": "x", "params": {"credential_secret": "x"}, "result": None, "error": None})
    assert call  # imported helper is shared with other suites
