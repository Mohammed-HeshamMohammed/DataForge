from __future__ import annotations

from pathlib import Path

import pytest

from dataforge_application.api import Service
from test_scope_review_tools import import_csv, run_full, write_csv
from test_workflows import call, ok


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


def test_review_queue_orders_by_likelihood_and_decides_the_clearest_bands_in_bulk(service: Service, tmp_path: Path) -> None:
    first = ["Ada", "Alan", "Barbara", "Claude", "Dorothy", "Edsger", "Frances", "Hedy", "Ivan", "Joan", "Ken", "Lynn", "Margaret", "Niklaus", "Radia", "Tim"]
    last = ["Babbage", "Church", "Dijkstra", "Engelbart", "Floyd", "Gosling", "Hamming", "Iverson", "Kay", "Lamport", "Minsky", "Naur", "Perlis", "Ritchie", "Shannon", "Thompson"]
    rows = [["Name", "Phone", "Address", "City"]]
    for i in range(160):  # a list of distinct people, as real lists have
        rows.append([f"{first[i % 16]} {last[(i * 7 + i // 16) % 16]}", f"30355{i:05d}", f"{100 + i} Main St", ["Austin", "Denver", "Boston", "Miami", "Tulsa"][i % 5]])
    rows += rows[1:31]  # and some rows entered twice, which the rules merge and the estimate learns from
    rows += [["Grace Hopper", "", "", "Arlington"], ["Grace Hopper", "", "", "Arlington"]]  # same rare name and town, nothing else: review
    # An initial with a home phone and address nobody else has: Safer mode asks, and the likelihood is near certain.
    rows += [["Katherine Johnson", "7575550101", "12 Mercury Rd", "Hampton"], ["K. Johnson", "7575550101", "12 Mercury Rd", "Hampton"],
             ["Mary Jackson", "7575550102", "40 Apollo Ave", "Hampton"], ["M. Jackson", "7575550102", "40 Apollo Ave", "Hampton"]]
    dataset_id = import_csv(service, write_csv(tmp_path / "people.csv", rows))
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, entity_type="person", mapping={"Name": "name", "Phone": "phone", "Address": "address", "City": "city"})
    job_id = run_full(service, dataset_id=dataset_id)

    queue = ok(service, "match.review_queue", job_id=job_id, order="likelihood", limit=100)
    likelihoods = [item["likelihood"] for item in queue["items"]]
    assert likelihoods == sorted(likelihoods, reverse=True) and all(0 <= value <= 1 for value in likelihoods)
    likely = queue["bands"]["likely"]
    assert likely == 2 and min(likelihoods[:2]) >= 0.99
    assert {queue["items"][0]["left"]["raw"]["Name"], queue["items"][0]["right"]["raw"]["Name"]} & {"K. Johnson", "M. Jackson"}

    assert "changed" in call(service, "match.bulk_review", job_id=job_id, band="likely", expected_count=likely + 1)["error"]["message"]
    done = ok(service, "match.bulk_review", job_id=job_id, band="likely", expected_count=likely)
    assert done["decided"] == 2 and done["action"] == "merge"
    assert ok(service, "match.review_queue", job_id=job_id)["bands"]["likely"] == 0
    assert {"Katherine Johnson", "K. Johnson"} in groups(service, job_id)
    assert ok(service, "match.undo_bulk_review", job_id=job_id, batch_id=done["batch_id"]) == {"undone": 2}
    assert ok(service, "match.review_queue", job_id=job_id)["bands"]["likely"] == 2
    assert {"Katherine Johnson", "K. Johnson"} not in groups(service, job_id)
