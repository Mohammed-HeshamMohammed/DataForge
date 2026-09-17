"""PresetService and ScrapeService."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from dataforge_scraping.engines.launcher import choose_engine, collect_with_scrapy
from dataforge_scraping.extraction import TEST_MODE_MAX_RECORDS, PolicyViolation, apply_field, extract_html_pages, extract_page, validate_candidates, validate_url
from dataforge_scraping.runtime import build_request, resolve_variables
from dataforge_scraping.signals import PURPOSES
from dataforge_scraping.packages import run_health_check, verify_package
from dataforge_scraping.presets import resolve_for_url, validate_preset

from . import sources
from .datasets import import_file, register_staged_rows
from .jobs import JobCancelled, JobContext, JobKind, JobValidationError
from .projects import app_data_dir
from .storage import ProjectStore, utc_now


CONSECUTIVE_FAILURES_TO_DISABLE = 3


def _active_packages(store: ProjectStore) -> list[dict]:
    """Newest non-removed version of each installed package."""
    latest: dict[str, dict] = {}
    for row in store._connection.execute("SELECT * FROM preset_packages WHERE removed_at IS NULL ORDER BY installed_at"):
        latest[row["name"]] = {**json.loads(row["package_json"]), "key_id": row["key_id"], "installed_at": row["installed_at"]}
    return list(latest.values())


def _health(store: ProjectStore, preset_id: str, version: str) -> dict | None:
    rows = store._connection.execute(
        "SELECT status, result_json, checked_at FROM preset_health_checks WHERE preset_id = ? AND preset_version = ? ORDER BY id DESC LIMIT ?",
        (preset_id, version, CONSECUTIVE_FAILURES_TO_DISABLE),
    ).fetchall()
    if not rows:
        return None
    consecutive = 0
    for row in rows:
        if row["status"] != "failed":
            break
        consecutive += 1
    return {"status": rows[0]["status"], "checked_at": rows[0]["checked_at"], "consecutive_failures": consecutive, **json.loads(rows[0]["result_json"])}


def _effective_status(preset: dict, health: dict | None) -> str:
    if preset.get("status") == "disabled" or (health and health["consecutive_failures"] >= CONSECUTIVE_FAILURES_TO_DISABLE):
        return "disabled"
    if health and health["status"] == "failed":
        return "degraded"
    return preset.get("status", "active")


def list_presets(store: ProjectStore, presets_dir: Path) -> list[dict]:
    presets = []
    for path in sorted(presets_dir.glob("*.json")):
        presets.append((json.loads(path.read_text(encoding="utf-8-sig")), "bundled", None))
    for package in _active_packages(store):
        presets.extend((p, "package", package["name"] + "@" + package["version"]) for p in package["presets"])
    for row in store._connection.execute("SELECT preset_json FROM custom_presets ORDER BY created_at"):
        presets.append((json.loads(row[0]), "custom", None))
    out = []
    for preset, source, package in presets:
        health = _health(store, preset["id"], preset["version"])
        out.append({**preset, "source": source, "package": package, "errors": validate_preset(preset), "health_status": health,
                    "declared_status": preset.get("status", "active"), "status": _effective_status(preset, health)})
    return out


def _fixtures(store: ProjectStore, presets_dir: Path, preset: dict) -> dict[str, str]:
    fixtures = {}
    for package in _active_packages(store):
        if any(p["id"] == preset["id"] and p["version"] == preset["version"] for p in package["presets"]):
            fixtures.update(package.get("fixtures", {}))
    for path in (preset.get("health") or {}).get("fixture_tests", []):
        base_dir = presets_dir
        if path.startswith("project:"):  # fixtures captured into this project (sources.fixture_from_capture)
            base_dir = store.project_root
        candidate = (base_dir / path.removeprefix("project:")).resolve()
        if path not in fixtures and candidate.is_relative_to(base_dir.resolve()) and candidate.is_file():
            if candidate.suffix.lower() in (".pdf", ".gz", ".warc"):
                import base64

                fixtures[path] = "base64:" + base64.b64encode(candidate.read_bytes()).decode()
            else:
                fixtures[path] = candidate.read_text(encoding="utf-8-sig")
    return fixtures


def run_health_checks(store: ProjectStore, presets_dir: Path, preset_id: str | None = None) -> list[dict]:
    results = []
    for preset in list_presets(store, presets_dir):
        if preset_id and preset["id"] != preset_id or preset["errors"]:
            continue
        fixtures = _fixtures(store, presets_dir, preset)
        result = run_health_check(preset, fixtures)
        if result["status"] == "passed" and (preset.get("extraction") or {}).get("record_root"):
            sources.save_fingerprints(store, preset, fixtures)
        elif result["status"] == "failed" and (preset.get("extraction") or {}).get("record_root"):
            # Degraded with a suggested fix: compare the failing fixture with the last fingerprints for this preset.
            result["suggestions"] = sources.health_suggestions(store, preset, fixtures)
        store._connection.execute(
            "INSERT INTO preset_health_checks(preset_id, preset_version, status, result_json, checked_at) VALUES (?,?,?,?,?)",
            (preset["id"], preset["version"], result["status"], json.dumps({"fixtures": result["fixtures"], "failures": result["failures"], "suggestions": result.get("suggestions", [])}), utc_now()),
        )
        results.append({"id": preset["id"], "version": preset["version"], **result})
    store._connection.commit()
    return results


def trusted_keys(presets_dir: Path) -> dict[str, str]:
    keys: dict[str, str] = {}
    for path in (presets_dir / "trusted_keys.json", app_data_dir() / "trusted-preset-keys.json"):
        if path.is_file():
            keys.update(json.loads(path.read_text(encoding="utf-8-sig")))
    return keys


def install_package(store: ProjectStore, presets_dir: Path, package_path: str) -> dict:
    path = Path(package_path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Package path must be an absolute path to a .dfpreset file")
    document = json.loads(path.read_text(encoding="utf-8-sig"))
    package = verify_package(document, trusted_keys(presets_dir))
    bundled = {(p["id"], p["version"]) for p in list_presets(store, presets_dir) if p["source"] == "bundled"}
    for preset in package["presets"]:
        if (preset["id"], preset["version"]) in bundled:
            raise ValueError(f"{preset['id']}@{preset['version']} is already bundled; packages must publish a new version")
        result = run_health_check(preset, package.get("fixtures", {}))
        if result["status"] != "passed":
            raise ValueError(f"{preset['id']} failed its fixture health check: {'; '.join(result['failures'])}")
    existing = store._connection.execute("SELECT removed_at FROM preset_packages WHERE name = ? AND version = ?", (package["name"], package["version"])).fetchone()
    if existing and existing["removed_at"] is None:
        raise ValueError("That package version is already installed")
    with store._connection:
        store._connection.execute("DELETE FROM preset_packages WHERE name = ? AND version = ?", (package["name"], package["version"]))
        store._connection.execute(
            "INSERT INTO preset_packages(id, name, version, key_id, package_json, installed_at) VALUES (?,?,?,?,?,?)",
            (str(uuid4()), package["name"], package["version"], document["key_id"], json.dumps(package), utc_now()),
        )
    return {"name": package["name"], "version": package["version"], "presets": [f"{p['id']}@{p['version']}" for p in package["presets"]]}


def list_packages(store: ProjectStore) -> list[dict]:
    return [{k: row[k] for k in ("name", "version", "key_id", "installed_at", "removed_at")} for row in store._connection.execute("SELECT * FROM preset_packages ORDER BY name, installed_at DESC")]


def rollback_package(store: ProjectStore, name: str) -> dict:
    """Remove the newest installed version so the previous one becomes active. Pinned jobs keep their copy."""
    rows = store._connection.execute("SELECT id, version FROM preset_packages WHERE name = ? AND removed_at IS NULL ORDER BY installed_at DESC", (name,)).fetchall()
    if len(rows) < 2:
        raise ValueError("No earlier installed version to roll back to")
    store._connection.execute("UPDATE preset_packages SET removed_at = ? WHERE id = ?", (utc_now(), rows[0]["id"]))
    store._connection.commit()
    return {"name": name, "removed": rows[0]["version"], "active": rows[1]["version"]}


def export_custom_preset(store: ProjectStore, preset_id: str, version: str) -> dict:
    row = store._connection.execute("SELECT preset_json FROM custom_presets WHERE id = ? AND version = ?", (preset_id, version)).fetchone()
    if row is None:
        raise ValueError("Unknown custom preset")
    # Presets never hold credentials, cookies, or responses; exporting the definition is safe to share.
    return {"schema_version": 1, "kind": "dataforge.custom_preset", "preset": json.loads(row[0])}


def import_custom_preset(store: ProjectStore, presets_dir: Path, document: dict) -> dict:
    if document.get("kind") != "dataforge.custom_preset" or document.get("schema_version") != 1:
        raise ValueError("Not a DataForge custom preset export")
    return save_custom_preset(store, presets_dir, document["preset"])


def resolve_preset(store: ProjectStore, presets_dir: Path, preset_id: str, version: str) -> dict:
    for preset in list_presets(store, presets_dir):
        if preset["id"] == preset_id and preset["version"] == version:
            return preset
    raise JobValidationError(f"Unknown preset {preset_id}@{version}")


def save_custom_preset(store: ProjectStore, presets_dir: Path, preset: dict) -> dict:
    if not str(preset.get("id", "")).startswith("custom."):
        raise ValueError("Custom presets must use an id of the form custom.<owner>.<name>")
    parent_id, parent_version = preset.get("parent_preset_id"), preset.get("parent_preset_version")
    if parent_id:
        parent = resolve_preset(store, presets_dir, parent_id, parent_version)
        # A derived preset may narrow, never broaden, its parent's scope, policy, or strategy permissions.
        if not parent["url_scope"].get("user_supplied_host") and not set(preset["url_scope"].get("allowed_hosts", [])) <= set(parent["url_scope"]["allowed_hosts"]):
            raise ValueError("A derived preset cannot broaden its parent's allowed hosts")
        if not set(preset["strategy"].get("allowed", [])) <= set(parent["strategy"].get("allowed", [])):
            raise ValueError("A derived preset cannot broaden its parent's strategies")
        if preset.get("policy") != parent.get("policy"):
            raise ValueError("A derived preset cannot change its parent's policy")
    clean = {k: v for k, v in preset.items() if k not in ("source", "errors", "last_test", "health_status", "package", "declared_status")}
    errors = validate_preset(clean)
    if errors:
        raise ValueError("Preset is invalid: " + "; ".join(errors))
    existing = store._connection.execute("SELECT 1 FROM custom_presets WHERE id = ? AND version = ?", (clean["id"], clean["version"])).fetchone()
    if existing:
        raise ValueError("That preset version already exists; versions are immutable, bump the version to save changes")
    store._connection.execute(
        "INSERT INTO custom_presets(id, version, parent_preset_id, parent_preset_version, preset_json, created_at) VALUES (?,?,?,?,?,?)",
        (clean["id"], clean["version"], parent_id, parent_version, json.dumps(clean, sort_keys=True), utc_now()),
    )
    store._connection.commit()
    return {"id": clean["id"], "version": clean["version"]}


def make_scrape_kind(presets_dir: Path) -> JobKind:
    def validate(store: ProjectStore, params: dict) -> dict:
        if params.get("policy_acknowledgement") is not True:
            raise JobValidationError("Confirm that you are authorized to collect this data and accept the site's terms")
        preset = resolve_preset(store, presets_dir, params.get("preset_id", ""), params.get("preset_version", ""))
        if preset["errors"]:
            raise JobValidationError("Preset is invalid: " + "; ".join(preset["errors"]))
        if preset.get("status") == "disabled":
            raise JobValidationError("This preset version is disabled and cannot start new jobs")
        run_mode = params.get("run_mode", "test")
        if run_mode not in ("test", "full"):
            raise JobValidationError("run_mode must be test or full")
        purpose = params.get("purpose") or preset["policy"].get("purpose")
        if purpose not in PURPOSES:
            raise JobValidationError("Choose the purpose of this collection (for example internal analysis or lead research)")
        if params.get("engine", "auto") not in ("auto", "httpx", "scrapy"):
            raise JobValidationError("engine must be auto, httpx, or scrapy")
        variables = params.get("variables") or {}
        if not isinstance(variables, dict):
            raise JobValidationError("variables must be an object")
        start_url = str(params.get("start_url", "")).strip()
        limits = preset["request_limits"]
        try:
            record_cap = TEST_MODE_MAX_RECORDS if run_mode == "test" else limits["max_records_default"]
            if not start_url and (preset.get("request") or {}).get("url_template"):
                start_url = build_request("", preset, resolve_variables(preset, variables, record_cap))[0]
            else:
                resolve_variables(preset, variables, record_cap)
            resolved = resolve_for_url(preset, start_url)
            validate_url(start_url, resolved)
        except (PolicyViolation, ValueError) as error:
            raise JobValidationError(str(error)) from error
        if preset.get("requires_contact_user_agent"):
            contact = sources.get_settings(store)["contact_identity"]
            if not contact.get("organization") or "@" not in str(contact.get("email")):
                raise JobValidationError("This source requires a contact identity; add your organization and email in Settings → Contact identity")
        if run_mode == "full" and preset["source"] == "custom":
            tested = store._connection.execute(
                "SELECT 1 FROM scrape_runs WHERE preset_id = ? AND preset_version = ? AND run_mode = 'test' AND status = 'completed' AND records_extracted > 0",
                (preset["id"], preset["version"]),
            ).fetchone()
            if not tested:
                raise JobValidationError("Run a successful 10-record test of this custom preset before a full run")
        max_records = min(int(params.get("max_records") or limits["max_records_default"]), limits["max_records_default"])
        if run_mode == "test":
            max_records = min(max_records, TEST_MODE_MAX_RECORDS)
        if preset["strategy"]["preferred"] == "webview":
            raise JobValidationError("This preset renders pages; run it from Scrape Studio")
        integration = preset["strategy"].get("api_integration") or {}
        if integration.get("auth") and not integration.get("optional") and not params.get("credential_ref"):
            raise JobValidationError("This API preset needs a saved credential; choose one before starting")
        max_pages = min(int(params.get("max_pages") or limits["max_pages_default"]), limits["max_pages_default"])
        engine = "httpx" if run_mode == "test" else choose_engine(preset, max_pages, params.get("engine"))
        if engine == "scrapy" and (integration.get("auth") or preset["strategy"]["preferred"] != "http"):
            raise JobValidationError("The Scrapy engine runs HTTP presets without credentials; choose the httpx engine")
        warnings = _status_warnings(preset)
        return {
            **params, "run_mode": run_mode, "start_url": start_url, "max_records": max_records, "max_pages": max_pages, "purpose": purpose,
            # Stable across retries (params are copied), so a retried Scrapy crawl resumes from the same queue.
            "resume_root": params.get("resume_root") or uuid4().hex,
            "engine": engine, "variables": variables,
            # Pin the exact resolved preset so later package changes cannot alter this job.
            "resolved_preset": {k: v for k, v in resolved.items() if k not in ("errors",)}, "warnings": warnings,
        }

    def run(context: JobContext) -> dict:
        params, store = context.params, context.store
        preset = params["resolved_preset"]
        settings = sources.get_settings(store)
        engine = params.get("engine", "httpx")
        mode = (preset.get("discovery") or {}).get("mode", "none")
        source_kind = "api" if preset["strategy"]["preferred"] == "api" else {"none": "website"}.get(mode, mode)
        run_id = store.create_scrape_run(context.job_id, preset["id"], preset["version"], preset["strategy"]["preferred"], params["start_url"])
        store._connection.execute("UPDATE scrape_runs SET run_mode = ?, engine = ?, purpose = ?, source_kind = ? WHERE id = ?", (params["run_mode"], engine, params.get("purpose"), source_kind, run_id))
        store._connection.commit()
        if params.get("retry_of") and mode == "crawl":
            sources.copy_frontier(store, params["retry_of"], context.job_id)
        capture = None
        if settings["warc_capture"].get("enabled") and engine == "httpx":
            from dataforge_scraping.archives import WarcCapture

            capture = WarcCapture(store.project_root / "captures" / f"{context.job_id}.warc.gz")
        context.stage("fetching", {"strategy": preset["strategy"]["preferred"], "engine": engine, "source": source_kind})
        try:
            if engine == "scrapy":
                result = collect_with_scrapy(
                    params["start_url"], preset, params["max_records"], params["max_pages"], should_stop=context.should_stop,
                    on_page=lambda event: context.stage("page_extracted", event), is_paused=lambda: context._control.pause.is_set(),
                    contact=settings["contact_identity"], cache_dir=sources.cache_dir(store), purpose=params.get("purpose"),
                    incremental=bool(params.get("incremental")), work_dir=store.project_root / "engine" / "runs" / context.job_id,
                    resume_dir=store.project_root / "engine" / "resume" / params["resume_root"] if mode in ("crawl", "sitemap") else None,
                    deltafetch_dir=store.project_root / "engine" / "deltafetch" / (params.get("watch_id") or preset["id"]),
                )
            else:
                result = extract_html_pages(
                    params["start_url"] if not (preset.get("request") or {}).get("url_template") else "", preset,
                    max_records=params["max_records"], max_pages=params["max_pages"],
                    should_stop=context.should_stop, on_page=lambda event: context.stage("page_extracted", event),
                    credential=context.secrets.get("credential"), contact=settings["contact_identity"], cache_dir=sources.cache_dir(store),
                    capture=capture, purpose=params.get("purpose"), variables=params.get("variables"),
                    frontier_store=sources.frontier_store(store, context.job_id) if mode == "crawl" else None,
                )
        except Exception as error:
            store.fail_scrape_run(run_id, "policy_violation" if isinstance(error, PolicyViolation) else type(error).__name__, str(error))
            raise
        finally:
            if capture is not None:
                capture.close()
                store._connection.execute("UPDATE scrape_runs SET warc_path = ? WHERE id = ?", (str(capture.path), run_id))
                store._connection.commit()
        sources.record_signals(store, run_id, list(result.signals))
        if engine == "scrapy":
            import shutil

            shutil.rmtree(store.project_root / "engine" / "runs" / context.job_id, ignore_errors=True)  # job file and item schema only
        if engine == "scrapy" and result.stop_reason != "cancelled":
            import shutil

            shutil.rmtree(store.project_root / "engine" / "resume" / params["resume_root"], ignore_errors=True)  # finished: nothing to resume
        if result.stop_reason == "cancelled":
            store.fail_scrape_run(run_id, "cancelled", "Cancelled by user")
            raise JobCancelled()
        dataset_id = None
        file_datasets = []
        project_id = store.job(context.job_id)["project_id"]
        if params["run_mode"] == "full" and result.records:
            dataset_id = register_staged_rows(
                store, project_id, list(result.records),
                f"scrape-{preset['id']}-{context.job_id[:8]}.json", params.get("dataset_name") or f"{preset['display_name']} scrape",
            ).dataset_id
        if params["run_mode"] == "full":
            for item in result.files:  # linked XLSX/JSON files go through the normal importers with provenance in the name
                target = store.project_root / "downloads" / f"{item['sha256']}.{item['kind']}"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(item["content"])
                try:
                    imported = import_file(store, project_id, target, f"{preset['display_name']}: {item['url'].rsplit('/', 1)[-1][:60]}")
                    file_datasets.append({"url": item["url"], "dataset_id": imported.dataset_id})
                except ValueError as error:
                    file_datasets.append({"url": item["url"], "error": str(error)})
        store.complete_scrape_run(run_id, result.pages_fetched, len(result.records), result.rejected_records, result.duplicate_records)
        store._connection.execute("UPDATE scrape_runs SET cached_responses = ?, discovered_urls = ? WHERE id = ?", (result.cached_responses, result.discovered_urls, run_id))
        store._connection.commit()
        diff = None
        if params.get("watch_id") and params["run_mode"] == "full":
            diff = sources.record_watch_run(store, params["watch_id"], context.job_id, dataset_id, list(result.records), preset)
        fields = [f["key"] for f in preset["extraction"].get("fields", [])] or sorted({k for r in result.records for k in r if not k.startswith(("source_", "preset_", "strategy_"))})[:40]
        coverage = {key: round(sum(1 for r in result.records if r.get(key) not in (None, "")) / len(result.records), 3) if result.records else 0 for key in fields}
        return {
            "run_mode": params["run_mode"], "preset": f"{preset['id']}@{preset['version']}", "strategy_used": result.strategy_used, "engine": result.engine,
            "source_kind": source_kind, "purpose": params.get("purpose"),
            "strategy_rationale": result.strategy_rationale, "pages_fetched": result.pages_fetched, "records_extracted": len(result.records),
            "records_rejected": result.rejected_records, "records_duplicate": result.duplicate_records, "stop_reason": result.stop_reason,
            "warnings": list(params.get("warnings", [])) + list(result.warnings), "field_coverage": coverage,
            "sample_records": list(result.records[:TEST_MODE_MAX_RECORDS]), "dataset_id": dataset_id, "file_datasets": file_datasets,
            "signals": list(result.signals), "cached_responses": result.cached_responses, "discovered_urls": result.discovered_urls,
            "watch_diff": diff, "warc_capture": str(capture.path) if capture is not None else None,
        }

    return JobKind(run=run, validate=validate)


def _status_warnings(preset: dict) -> list[str]:
    if preset.get("status") == "deprecated":
        return [f"Preset is deprecated; migrate to {preset.get('successor')}"]
    if preset.get("status") == "degraded":
        failures = (preset.get("health_status") or {}).get("failures", [])
        return ["Preset is degraded: its fixture health check is failing" + (f" ({failures[0]})" if failures else "")]
    return []


def make_rendered_kind(presets_dir: Path) -> JobKind:
    """Stages records extracted in Scrape Studio's embedded WebView. The host renders; this validates and stages."""

    def validate(store: ProjectStore, params: dict) -> dict:
        if params.get("policy_acknowledgement") is not True:
            raise JobValidationError("Confirm that you are authorized to collect this data and accept the site's terms")
        preset = params.get("preset")
        if not isinstance(preset, dict):
            raise JobValidationError("A preset definition is required")
        stored = None
        if preset.get("id", "").startswith("custom.") or preset.get("source") in ("bundled", "package"):
            stored = next((p for p in list_presets(store, presets_dir) if p["id"] == preset.get("id") and p["version"] == preset.get("version")), None)
        if stored is not None:
            preset = stored  # never trust a UI copy of a saved preset
        errors = validate_preset({k: v for k, v in preset.items() if k not in ("source", "errors", "health_status", "package", "declared_status")})
        if errors:
            raise JobValidationError("Preset is invalid: " + "; ".join(errors))
        if "webview" not in preset["strategy"].get("allowed", []):
            raise JobValidationError("This preset does not allow embedded WebView rendering")
        if preset.get("status") == "disabled":
            raise JobValidationError("This preset version is disabled and cannot start new jobs")
        pages = params.get("pages")
        if not isinstance(pages, list) or not pages:
            raise JobValidationError("No extracted pages were provided")
        run_mode = params.get("run_mode", "test")
        limits = preset["request_limits"]
        kind = (preset.get("pagination") or {}).get("type", "none")
        # Detail pages are bounded by the record cap (one record each); listing pages by the page cap.
        page_cap = limits["max_records_default"] if kind == "detail_links" else 1 if kind in ("none", "infinite_scroll") else limits["max_pages_default"]
        if len(pages) > page_cap:
            raise JobValidationError(f"{len(pages)} pages exceed this preset's limit of {page_cap} for {kind} pagination")
        purpose = params.get("purpose")
        if purpose not in PURPOSES:
            raise JobValidationError("Choose the purpose of this collection (for example internal analysis or lead research)")
        resolved = resolve_for_url(preset, pages[0]["url"])
        for page in pages:
            try:
                validate_url(page["url"], resolved)
            except PolicyViolation as error:
                raise JobValidationError(f"{page['url']}: {error}") from error
            decision = sources.check_navigation(page["url"], purpose)
            if not decision["allowed"]:
                raise JobValidationError(f"{page['url']}: {decision['reason']}")
        if run_mode == "full" and preset["id"].startswith("custom."):
            if stored is None:
                raise JobValidationError("Save this custom preset before a full run")
            tested = store._connection.execute(
                "SELECT 1 FROM scrape_runs WHERE preset_id = ? AND preset_version = ? AND run_mode = 'test' AND status = 'completed' AND records_extracted > 0",
                (preset["id"], preset["version"]),
            ).fetchone()
            if not tested:
                raise JobValidationError("Run a successful 10-record test of this custom preset before a full run")
        return {**params, "run_mode": run_mode, "purpose": purpose, "signals": sources.navigation_signals(purpose, [p["url"] for p in pages]),
                "resolved_preset": {k: v for k, v in resolved.items() if k not in ("errors", "health_status", "declared_status", "source", "package")}, "warnings": _status_warnings(preset)}

    def run(context: JobContext) -> dict:
        params, store = context.params, context.store
        preset = params["resolved_preset"]
        fields = preset["extraction"].get("fields", [])
        limit = min(preset["request_limits"]["max_records_default"], TEST_MODE_MAX_RECORDS if params["run_mode"] == "test" else 10**9)
        run_id = store.create_scrape_run(context.job_id, preset["id"], preset["version"], "webview", params["pages"][0]["url"])
        store._connection.execute("UPDATE scrape_runs SET run_mode = ?, purpose = ?, engine = 'webview', source_kind = 'studio' WHERE id = ?", (params["run_mode"], params["purpose"], run_id))
        sources.record_signals(store, run_id, params.get("signals") or [])
        store._connection.commit()
        candidates = []
        selector_free = (preset.get("extraction") or {}).get("mode", "selectors") != "selectors"
        for page in params["pages"]:
            context.checkpoint()
            if selector_free:
                # Structured data or article text read from the page exactly as the embedded WebView rendered it.
                html = str(page.get("html") or "")[:5_000_000]
                page_warnings: list[str] = []
                for record in extract_page(html, html.encode("utf-8"), "text/html", page["url"], preset, page_warnings):
                    record.update(source_retrieved_at=page.get("retrieved_at") or utc_now(), strategy_used="webview")
                    candidates.append(record)
                context.stage("page_extracted", {"url": page["url"], "candidates": len(candidates)})
                continue
            for raw in page.get("records", [])[: preset["request_limits"]["max_records_default"]]:
                record = {}
                for field in fields:
                    value = raw.get(field["key"]) if isinstance(raw, dict) else None
                    if value not in (None, ""):
                        record[field["key"]] = apply_field(str(value)[:10_000], field, page["url"], preset)
                record.update(source_url=page["url"], source_retrieved_at=page.get("retrieved_at") or utc_now(), preset_id=preset["id"], preset_version=preset["version"], strategy_used="webview")
                candidates.append(record)
            context.stage("page_extracted", {"url": page["url"], "candidates": len(page.get("records", []))})
        valid, rejected, warnings = validate_candidates(candidates, preset)
        duplicates = len(candidates) - len(valid) - len(rejected)
        records = valid[:limit]
        dataset_id = None
        if params["run_mode"] == "full" and records:
            dataset_id = register_staged_rows(
                store, store.job(context.job_id)["project_id"], records, f"studio-{preset['id']}-{context.job_id[:8]}.json",
                params.get("dataset_name") or f"{preset.get('display_name', preset['id'])} (Scrape Studio)",
            ).dataset_id
        store.complete_scrape_run(run_id, len(params["pages"]), len(records), len(rejected), duplicates)
        keys = [f["key"] for f in fields] or sorted({k for r in records for k in r if not k.startswith(("source_", "preset_", "strategy_"))})[:40]
        coverage = {key: round(sum(1 for r in records if r.get(key) not in (None, "")) / len(records), 3) if records else 0 for key in keys}
        return {
            "run_mode": params["run_mode"], "preset": f"{preset['id']}@{preset['version']}", "strategy_used": "webview",
            "strategy_rationale": "Rendered in DataForge's visible embedded WebView because the preset allows rendering for this page.",
            "pages_fetched": len(params["pages"]), "records_extracted": len(records), "records_rejected": len(rejected), "records_duplicate": duplicates,
            "stop_reason": "max_records" if len(valid) > limit else "completed", "warnings": params.get("warnings", []) + warnings,
            "field_coverage": coverage, "sample_records": records[:TEST_MODE_MAX_RECORDS], "dataset_id": dataset_id,
        }

    return JobKind(run=run, validate=validate)
