from __future__ import annotations

import csv
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from dataforge_application.api import Service
from dataforge_application.storage import ProjectStore


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Fixture")
    return svc


def call(svc: Service, command: str, **payload):
    return svc.handle({"schema_version": 1, "command": command, "payload": payload})


def ok(svc: Service, command: str, **payload):
    response = call(svc, command, **payload)
    assert response["ok"], response
    return response["result"]


def wait(svc: Service, job_id: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = ok(svc, "job.get", job_id=job_id)
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


PEOPLE = [
    ["Name", "Phone", "Address", "Zip", "Email"],
    ["Ada Lovelace", "(512) 555-0182", "123 Main Street", "78701", "ada@example.test"],
    ["Ada Lovelace", "512-555-0182", "123 Main St", "78701", ""],          # safe match with row 2
    ["Ada Lovelace", "512-555-0182", "125 Main St", "78701", ""],          # house number contradiction
    ["Grace Hopper", "(212) 555-0100", "1 Navy Way", "10001", ""],
    ["G. Hopper", "(212) 555-0100", "", "", ""],                          # strong phone, weak rest: review
    ["Alan Turing", "", "9 Bletchley Rd", "00123", "alan@example.test"],
]


def import_people(svc: Service, tmp_path: Path) -> str:
    source = tmp_path / "people.csv"
    with source.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(PEOPLE)
    job = wait(svc, ok(svc, "dataset.import", path=str(source))["job_id"])
    assert job["state"] == "completed", job
    return job["result"]["dataset_id"]


def test_migrations_apply_once_and_project_reopens(service: Service, tmp_path: Path) -> None:
    store = ProjectStore(tmp_path / "project" / "dataforge.sqlite3")
    assert store.migrate(service.migrations_dir) == []
    store.close()
    reopened = ok(service, "project.open", path=str(tmp_path / "project"))
    assert reopened["name"] == "Fixture"
    assert call(service, "project.create", path=str(tmp_path / "project"))["error"]["code"] == "invalid_request"
    assert call(service, "project.open", path="relative/path")["error"]["message"] == "Project path must be absolute"


def test_full_match_review_and_export_workflow(service: Service, tmp_path: Path) -> None:
    dataset_id = import_people(service, tmp_path)
    profile = ok(service, "dataset.profile", dataset_id=dataset_id)
    assert {c["name"]: c["proposed_role"] for c in profile["columns"]}["Phone"] == "phone"

    mapping = {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code", "Email": "email"}
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, mapping=mapping, entity_type="person")

    blocked = call(service, "match.create_job", dataset_id=dataset_id, run_mode="full")
    assert "preview" in blocked["error"]["message"]

    preview = wait(service, ok(service, "match.create_job", dataset_id=dataset_id, run_mode="preview")["job_id"])
    assert preview["state"] == "completed", preview
    stages = [e["payload"].get("stage") for e in preview["events"] if e["event_type"] == "job.stage_changed"]
    assert stages[:3] == ["loading", "normalizing", "finding_candidates"]

    # Changing strictness makes the preview stale.
    stale = call(service, "match.create_job", dataset_id=dataset_id, run_mode="full", settings={"strictness": "balanced"})
    assert "stale" in stale["error"]["message"]

    full = wait(service, ok(service, "match.create_job", dataset_id=dataset_id, run_mode="full")["job_id"])
    job_id = full["id"]
    summary = ok(service, "match.results", job_id=job_id)
    assert summary["decisions"]["match"] == 1
    assert summary["metrics"]["guard_reasons"]["Address: different house number"] >= 1
    assert summary["merged_groups"] == 1 and summary["canonical_records"] == 5

    queue = ok(service, "match.review_queue", job_id=job_id)
    assert queue["total"] >= 1 and "Phone" in queue["sensitive_columns"]
    item = next(i for i in queue["items"] if {i["left"]["raw"]["Name"], i["right"]["raw"]["Name"]} == {"Grace Hopper", "G. Hopper"})

    assert call(service, "export.create", job_id=job_id)["error"]["message"].endswith("export with unresolved records.")
    unresolved = ok(service, "export.create", job_id=job_id, allow_unresolved=True)
    assert unresolved["is_final"] is False and "unresolved" in unresolved["directory"]

    decision = ok(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="merge", expected_version=item["review_version"])
    conflict = call(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="keep_separate", expected_version=item["review_version"])
    assert conflict["error"]["code"] == "review_conflict"
    assert ok(service, "match.results", job_id=job_id)["merged_groups"] == 2

    ok(service, "match.undo_review", job_id=job_id, review_action_id=decision["review_action_id"])
    assert ok(service, "match.results", job_id=job_id)["merged_groups"] == 1
    refreshed = next(i for i in ok(service, "match.review_queue", job_id=job_id)["items"] if i["decision_id"] == item["decision_id"])
    ok(service, "match.submit_review", job_id=job_id, decision_id=item["decision_id"], action="keep_separate", expected_version=refreshed["review_version"])

    remaining = ok(service, "match.review_queue", job_id=job_id)
    for pending in remaining["items"]:
        ok(service, "match.submit_review", job_id=job_id, decision_id=pending["decision_id"], action="keep_separate", expected_version=pending["review_version"])
    final = ok(service, "export.create", job_id=job_id)
    assert final["is_final"] is True
    files = {f["kind"]: Path(f["path"]) for f in final["files"]}
    with files["canonical"].open(encoding="utf-8-sig") as handle:
        canonical = list(csv.DictReader(handle))
    assert len(canonical) == 5 and "field_provenance" in canonical[0]
    with files["original"].open(encoding="utf-8-sig") as handle:
        original = list(csv.DictReader(handle))
    assert len(original) == 6 and original[-1]["Zip"] == "00123"
    audit = json.loads(files["audit"].read_text(encoding="utf-8"))
    assert audit["is_final"]
    assert sorted((a["action"], a["reversed_at"] is None) for a in audit["review_actions"]) == [("keep_separate", True), ("merge", False)]

    # Source rows are untouched by matching, review, and export.
    rows = ok(service, "dataset.rows", dataset_id=dataset_id)
    assert [r["raw"] for r in rows] == [dict(zip(PEOPLE[0], r)) for r in PEOPLE[1:]]


def test_fixture_job_pause_resume_cancel_and_retry(service: Service) -> None:
    job_id = ok(service, "job.start_fixture", steps=50, step_seconds=0.02)["job_id"]
    ok(service, "job.pause", job_id=job_id)
    deadline = time.monotonic() + 5
    while ok(service, "job.get", job_id=job_id)["state"] != "paused" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ok(service, "job.get", job_id=job_id)["state"] == "paused"
    ok(service, "job.resume", job_id=job_id)
    ok(service, "job.cancel", job_id=job_id)
    job = wait(service, job_id)
    assert job["state"] == "cancelled"
    transitions = [e["payload"]["to_state"] for e in job["events"] if e["event_type"] == "job.state_changed"]
    assert "paused" in transitions and transitions[-1] == "cancelled"
    retried = wait(service, ok(service, "job.retry", job_id=job_id)["job_id"])
    assert retried["state"] == "completed" and retried["params"]["retry_of"] == job_id


def test_interrupted_jobs_are_recovered_as_failed_on_reopen(service: Service, tmp_path: Path) -> None:
    store = service.store
    job_id = store.create_job(service.project["id"], "fixture")
    for state in ("validating", "queued", "running"):
        store.transition_job(job_id, state)
    reopened = ok(service, "project.open", path=str(tmp_path / "project"))
    assert job_id in reopened["recovered_jobs"]
    job = ok(service, "job.get", job_id=job_id)
    assert job["state"] == "failed" and "restart" in job["error"]


class Site(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"<article><h2>Alpha</h2><a href='/a'>a</a></article><article><h2>Beta</h2><a href='/b'>b</a></article>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def test_scrape_test_mode_custom_preset_gate_and_staging(service: Service) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
    Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/list"
    try:
        presets = ok(service, "preset.list")
        base = next(p for p in presets if p["id"] == "generic.html_list")
        assert call(service, "scrape.create_job", preset_id=base["id"], preset_version="1.0.0", start_url=url)["error"]["message"].startswith("Confirm")

        custom = {k: v for k, v in base.items() if k not in ("source", "errors")}
        custom.update(id="custom.me.cards", version="1.0.0", parent_preset_id=base["id"], parent_preset_version="1.0.0")
        custom["request_limits"] = {**custom["request_limits"], "min_delay_ms": 0}
        weakened = {**custom, "policy": {**base["policy"], "robots_policy": "ignore"}}
        assert call(service, "preset.save_custom", preset=weakened)["error"]["message"].startswith("A derived preset cannot change")
        ok(service, "preset.save_custom", preset=custom)
        assert "immutable" in call(service, "preset.save_custom", preset=custom)["error"]["message"]

        args = dict(preset_id="custom.me.cards", preset_version="1.0.0", start_url=url, policy_acknowledgement=True, purpose="internal_analysis")
        assert "test" in call(service, "scrape.create_job", **args, run_mode="full")["error"]["message"]
        test = wait(service, ok(service, "scrape.create_job", **args, run_mode="test")["job_id"])
        assert test["state"] == "completed", test
        assert test["result"]["records_extracted"] == 2 and test["result"]["dataset_id"] is None
        full = wait(service, ok(service, "scrape.create_job", **args, run_mode="full", dataset_name="Cards")["job_id"])
        assert full["result"]["dataset_id"]
        rows = ok(service, "dataset.rows", dataset_id=full["result"]["dataset_id"])
        assert rows[0]["raw"]["preset_id"] == "custom.me.cards" and rows[0]["raw"]["title"] == "Alpha"
    finally:
        server.shutdown()


def full_job(service: Service, dataset_id: str, mapping: dict) -> str:
    ok(service, "dataset.confirm_mapping", dataset_id=dataset_id, mapping=mapping, entity_type="person")
    wait(service, ok(service, "match.create_job", dataset_id=dataset_id, run_mode="preview")["job_id"])
    return wait(service, ok(service, "match.create_job", dataset_id=dataset_id, run_mode="full")["job_id"])["id"]


def test_split_lock_and_undo_cluster_actions_persist_for_future_runs(service: Service, tmp_path: Path) -> None:
    source = tmp_path / "triples.csv"
    rows = [["Name", "Phone", "Address", "Zip"]] + [["Ada Lovelace", "5125550182", "123 Main St", "78701"]] * 3 + [["Bob Stone", "2125550100", "9 Oak Ave", "10001"]] * 2
    with source.open("w", newline="", encoding="utf-8") as handle:
        csv.writer(handle).writerows(rows)
    dataset_id = wait(service, ok(service, "dataset.import", path=str(source))["job_id"])["result"]["dataset_id"]
    job_id = full_job(service, dataset_id, {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code"})

    groups = ok(service, "match.clusters", job_id=job_id)
    assert sorted(g["member_count"] for g in groups["items"]) == [2, 3]
    ada = next(g for g in groups["items"] if g["member_count"] == 3)
    assert call(service, "match.split_cluster", job_id=job_id, cluster_id=ada["cluster_id"], row_ids=[m["id"] for m in ada["members"]])["error"]["code"] == "invalid_request"

    split = ok(service, "match.split_cluster", job_id=job_id, cluster_id=ada["cluster_id"], row_ids=[ada["members"][0]["id"]])
    assert sorted(g["member_count"] for g in ok(service, "match.clusters", job_id=job_id)["items"]) == [2, 2]
    assert call(service, "match.split_cluster", job_id=job_id, cluster_id=ada["cluster_id"], row_ids=[ada["members"][1]["id"]])["error"]["code"] == "review_conflict"

    bob = next(g for g in ok(service, "match.clusters", job_id=job_id)["items"] if g["members"][0]["raw"]["Name"] == "Bob Stone")
    lock = ok(service, "match.lock_cluster", job_id=job_id, cluster_id=bob["cluster_id"])
    locked = next(g for g in ok(service, "match.clusters", job_id=job_id)["items"] if g["cluster_id"] == bob["cluster_id"])
    assert locked["status"] == "locked" and locked["lock_action_id"] == lock["cluster_action_id"]
    assert "Unlock" in call(service, "match.split_cluster", job_id=job_id, cluster_id=bob["cluster_id"], row_ids=[bob["members"][0]["id"]])["error"]["message"]

    # The split constraint and the lock carry into a new run of the same dataset.
    rerun = wait(service, ok(service, "match.create_job", dataset_id=dataset_id, run_mode="full")["job_id"])["id"]
    rerun_groups = ok(service, "match.clusters", job_id=rerun)["items"]
    assert sorted(g["member_count"] for g in rerun_groups) == [2, 2]
    assert any(g["status"] == "locked" for g in rerun_groups)

    ok(service, "match.undo_cluster_action", job_id=job_id, cluster_action_id=split["cluster_action_id"])
    assert sorted(g["member_count"] for g in ok(service, "match.clusters", job_id=job_id)["items"]) == [2, 3]


def test_ranking_requires_enough_human_labels(service: Service, tmp_path: Path) -> None:
    dataset_id = import_people(service, tmp_path)
    job_id = full_job(service, dataset_id, {"Name": "name", "Phone": "phone", "Address": "address", "Zip": "postal_code", "Email": "email"})
    assert "at least" in call(service, "match.train_ranking", job_id=job_id)["error"]["message"]
    assert "No ranking model" in call(service, "match.review_queue", job_id=job_id, order="model")["error"]["message"]
    assert ok(service, "match.review_queue", job_id=job_id)["ranking_model"] is None


def test_new_migration_backs_up_existing_project(service: Service, tmp_path: Path) -> None:
    migrations = tmp_path / "migrations"
    migrations.mkdir()
    for path in service.migrations_dir.glob("*.sql"):
        (migrations / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (migrations / "999_extra.sql").write_text("CREATE TABLE extra_fixture (id INTEGER);", encoding="utf-8")
    service.migrations_dir = migrations
    ok(service, "project.open", path=str(tmp_path / "project"))
    backups = list((tmp_path / "project" / "backups").glob("dataforge-before-999_extra-*.sqlite3"))
    assert len(backups) == 1 and backups[0].stat().st_size > 0


def test_fuzzy_header_suggestions_need_confirmation() -> None:
    from dataforge_application.datasets import propose_field_mappings

    typo = propose_field_mappings(["Phone Numbr"])[0]
    assert typo.confidence == "ambiguous" and typo.role is None and typo.candidates == ("phone",)
    assert propose_field_mappings(["Zebra"])[0].confidence == "unmapped"


def test_signed_package_install_health_and_rollback(service: Service, tmp_path: Path) -> None:
    from dataforge_scraping.packages import generate_key, sign_package

    key = generate_key(tmp_path / "keys" / "k.pem")
    (tmp_path / "appdata" / "DataForge").mkdir(parents=True, exist_ok=True)
    (tmp_path / "appdata" / "DataForge" / "trusted-preset-keys.json").write_text(json.dumps({key["key_id"]: key["public_key_pem"]}), encoding="utf-8")
    base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list")
    fixture = (service.presets_dir / "fixtures/generic/html-list.html").read_text(encoding="utf-8")

    def package(version: str, preset_version: str) -> Path:
        preset = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
        preset.update(id="vendor.cards", version=preset_version, status="active")
        signed = sign_package({"name": "vendor-pack", "version": version, "presets": [preset], "fixtures": {"fixtures/generic/html-list.html": fixture}}, (tmp_path / "keys" / "k.pem").read_bytes())
        path = tmp_path / f"vendor-{version}.dfpreset"
        path.write_text(json.dumps(signed), encoding="utf-8")
        return path

    ok(service, "preset.install_package", path=str(package("1.0.0", "1.0.0")))
    ok(service, "preset.install_package", path=str(package("1.1.0", "1.1.0")))
    ids = {(p["id"], p["version"]) for p in ok(service, "preset.list") if p["source"] == "package"}
    assert ids == {("vendor.cards", "1.1.0")}
    rolled = ok(service, "preset.rollback_package", name="vendor-pack")
    assert rolled == {"name": "vendor-pack", "removed": "1.1.0", "active": "1.0.0"}
    assert {p["version"] for p in ok(service, "preset.list") if p["id"] == "vendor.cards"} == {"1.0.0"}

    tampered = json.loads(package("2.0.0", "2.0.0").read_text(encoding="utf-8"))
    tampered["package"]["presets"][0]["policy"]["robots_policy"] = "ignore"
    (tmp_path / "tampered.dfpreset").write_text(json.dumps(tampered), encoding="utf-8")
    assert "signature" in call(service, "preset.install_package", path=str(tmp_path / "tampered.dfpreset"))["error"]["message"]

    results = ok(service, "preset.health_check")
    assert all(r["status"] == "passed" for r in results)
    assert next(p for p in ok(service, "preset.list") if p["id"] == "wikipedia.search_api")["health_status"]["status"] == "passed"


def test_failing_health_checks_degrade_then_disable_a_preset(service: Service, tmp_path: Path) -> None:
    base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list")
    custom = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    custom.update(id="custom.me.broken", version="1.0.0", parent_preset_id=base["id"], parent_preset_version="1.0.0")
    custom["extraction"] = {**custom["extraction"], "record_root": {"css": "div.does-not-exist"}}
    ok(service, "preset.save_custom", preset=custom)
    ok(service, "preset.health_check", preset_id="custom.me.broken")
    assert next(p for p in ok(service, "preset.list") if p["id"] == "custom.me.broken")["status"] == "degraded"
    ok(service, "preset.health_check", preset_id="custom.me.broken")
    ok(service, "preset.health_check", preset_id="custom.me.broken")
    assert next(p for p in ok(service, "preset.list") if p["id"] == "custom.me.broken")["status"] == "disabled"
    blocked = call(service, "scrape.create_job", preset_id="custom.me.broken", preset_version="1.0.0", start_url="http://127.0.0.1:9/x", policy_acknowledgement=True, purpose="internal_analysis")
    assert "disabled" in blocked["error"]["message"]

    exported = ok(service, "preset.export_custom", preset_id="custom.me.broken", preset_version="1.0.0")
    exported["preset"]["version"] = "1.0.1"
    exported["preset"]["extraction"]["record_root"] = {"css": "article"}
    assert ok(service, "preset.import_custom", document=exported)["version"] == "1.0.1"


def test_credential_secret_is_used_in_memory_but_never_persisted(service: Service, tmp_path: Path) -> None:
    received = {}

    class Api(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            received["key"] = self.headers.get("X-Api-Key")
            body = json.dumps({"items": [{"id": "1", "name": "One"}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Api)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.json_api")
        custom = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
        custom.update(id="custom.me.keyed", version="1.0.0")
        custom["strategy"] = {**custom["strategy"], "api_integration": {"auth": "header", "header_name": "X-Api-Key"}}
        ok(service, "preset.save_custom", preset=custom)
        url = f"http://127.0.0.1:{server.server_port}/items"
        args = dict(preset_id="custom.me.keyed", preset_version="1.0.0", start_url=url, policy_acknowledgement=True, run_mode="test", purpose="internal_analysis")
        assert "credential" in call(service, "scrape.create_job", **args)["error"]["message"]
        job = wait(service, ok(service, "scrape.create_job", **args, credential_ref="vendor-api", credential_secret="s3cr3t-value")["job_id"])
        assert job["state"] == "completed", job
        assert received["key"] == "s3cr3t-value"
        database = (tmp_path / "project" / "dataforge.sqlite3").read_bytes() + (tmp_path / "project" / "dataforge.sqlite3-wal").read_bytes()
        assert b"s3cr3t-value" not in database
        assert job["params"]["credential_ref"] == "vendor-api" and "credential_secret" not in job["params"]
    finally:
        server.shutdown()


def test_rendered_records_are_scope_checked_validated_and_staged(service: Service) -> None:
    base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list")
    draft = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    draft.update(id="custom.local.studio_cards", version="1.0.0", strategy={"preferred": "webview", "allowed": ["webview"]})
    assert ok(service, "scrape.check_url", preset=draft, url="https://example.org/list", scope_url="https://example.org/")["allowed"] is True
    assert ok(service, "scrape.check_url", preset=draft, url="https://evil.example/", scope_url="https://example.org/")["allowed"] is False

    pages = [{"url": "https://example.org/list", "records": [{"title": "  Alpha  ", "link": "/a"}, {"title": "", "link": "/b"}, {"title": "Alpha", "link": "/a"}]}]
    job = wait(service, ok(service, "scrape.stage_rendered", preset=draft, pages=pages, run_mode="test", policy_acknowledgement=True, purpose="internal_analysis")["job_id"])
    assert job["state"] == "completed", job
    assert job["result"]["records_extracted"] == 1 and job["result"]["records_rejected"] == 1 and job["result"]["records_duplicate"] == 1
    assert job["result"]["sample_records"][0]["link"] == "https://example.org/a" and job["result"]["strategy_used"] == "webview"
    assert "Save this custom preset" in call(service, "scrape.stage_rendered", preset=draft, pages=pages, run_mode="full", policy_acknowledgement=True, purpose="internal_analysis")["error"]["message"]
    outside = [{"url": "https://evil.example/x", "records": []}, *pages]
    assert "allow-list" in call(service, "scrape.stage_rendered", preset=draft, pages=pages[:0] + [pages[0], outside[0]], run_mode="test", policy_acknowledgement=True, purpose="internal_analysis")["error"]["message"]


def test_commands_work_from_other_threads_like_the_http_bridge(service: Service) -> None:
    results = []
    worker = Thread(target=lambda: results.append(call(service, "dataset.list")))
    worker.start()
    worker.join()
    assert results[0]["ok"], results


def test_unknown_commands_and_schema_versions_are_rejected(service: Service) -> None:
    assert call(service, "nope.nope")["error"]["code"] == "unknown_command"
    assert service.handle({"schema_version": 9, "command": "health.check"})["error"]["code"] == "unsupported_schema"
