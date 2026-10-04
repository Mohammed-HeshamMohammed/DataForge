from __future__ import annotations

from pathlib import Path

import pytest

from dataforge_application.api import Service
from test_scope_review_tools import import_csv, run_full, write_csv
from test_workflows import ok


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="People")
    return svc


def groups(service: Service, job_id: str) -> list[set[str]]:
    return [{m["raw"]["Name"] for m in g["members"]} for g in ok(service, "match.clusters", job_id=job_id, limit=100)["items"]]


def test_regrouping_after_a_review_keeps_different_first_names_apart(service: Service, tmp_path: Path) -> None:
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", [
        ["Name", "Phone", "Email", "Address", "Zip"],
        ["Susan Rodriguez", "4303658070", "rodriguez.home@example.org", "3607 Lincoln St", "97295"],
        ["S. Rodriguez", "4303658070", "rodriguez.home@example.org", "3607 Lincoln St", "97295"],
        ["Samantha Rodriguez", "4303658070", "rodriguez.home@example.org", "3607 Lincoln St", "97295"],
        ["Grace Hopper", "(212) 555-0100", "", "1 Navy Way", "10001"],
        ["G. Hopper", "(212) 555-0100", "", "", ""],
    ]))
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person",
       mapping={"Name": "name", "Phone": "phone", "Email": "email", "Address": "address", "Zip": "postal_code"})
    job_id = run_full(service, dataset_id=dataset_id)
    assert not any({"Susan Rodriguez", "Samantha Rodriguez"} <= group for group in groups(service, job_id))

    item = next(i for i in ok(service, "match.review_queue", job_id=job_id)["items"] if {i["left"]["raw"]["Name"], i["right"]["raw"]["Name"]} == {"Grace Hopper", "G. Hopper"})
    ok(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="merge", expected_version=item["review_version"])
    after = groups(service, job_id)
    assert {"Grace Hopper", "G. Hopper"} in after
    assert not any({"Susan Rodriguez", "Samantha Rodriguez"} <= group for group in after)
