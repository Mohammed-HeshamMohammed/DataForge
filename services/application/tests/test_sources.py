"""Service integration for the expanded source types: purposes and signals on runs, settings, sitemap and
crawl jobs on both engines, watches with diffs, archives, bulk corpora, suggestions, and maintenance."""

from __future__ import annotations

import gzip
import json
import sys
from pathlib import Path

import httpx
import pytest

from dataforge_application import sources
from dataforge_application.api import Service

from test_workflows import call, ok, wait

sys.path.insert(0, str(Path(__file__).parents[3] / "workers" / "scraping" / "tests"))
from fixture_site import PRODUCTS, product_page, serve  # noqa: E402


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Sources")
    return svc


@pytest.fixture()
def site():
    base, state, server = serve()
    yield base, state
    server.shutdown()


def scrape(service: Service, **params) -> dict:
    params = {"policy_acknowledgement": True, "purpose": "internal_analysis", "run_mode": "test", **params}
    return wait(service, ok(service, "scrape.create_job", **params)["job_id"], timeout=120)


def test_purpose_required_and_signals_recorded(service: Service, site) -> None:
    base, _ = site
    missing = call(service, "scrape.create_job", preset_id="generic.structured_data", preset_version="1.0.0", start_url=base + "/product/SKU-1", policy_acknowledgement=True)
    assert "purpose" in missing["error"]["message"]
    job = scrape(service, preset_id="generic.structured_data", preset_version="1.0.0", start_url=base + "/product/SKU-1")
    assert job["state"] == "completed", job
    result = job["result"]
    assert result["purpose"] == "internal_analysis" and result["engine"] == "httpx" and result["sample_records"][0]["sku"] == "SKU-1"
    assert ok(service, "scrape.run_signals", job_id=job["id"])[0]["robots_status"] == "local"


def test_settings_contact_identity_gates_sec_preset(service: Service) -> None:
    settings = ok(service, "settings.get")
    assert settings["warc_capture"] == {"enabled": False, "retention_days": 30} and settings["ai_suggestions"]["remote_consent"] is False
    blocked = call(service, "scrape.create_job", preset_id="sec.submissions", preset_version="1.0.0", policy_acknowledgement=True, purpose="research", variables={"cik": "0000320193"})
    assert "contact identity" in blocked["error"]["message"]
    assert "unknown" in call(service, "settings.update", changes={"surprise": 1})["error"]["message"].lower()
    ok(service, "settings.update", changes={"contact_identity": {"organization": "Acme Research", "email": "ops@acme.test"}})
    bad_variable = call(service, "scrape.create_job", preset_id="sec.submissions", preset_version="1.0.0", policy_acknowledgement=True, purpose="research", variables={"cik": "12"})
    assert "invalid format" in bad_variable["error"]["message"]


def test_sitemap_job_full_run_on_scrapy_engine_with_warc_capture(service: Service, site) -> None:
    base, _ = site
    ok(service, "settings.update", changes={"warc_capture": {"enabled": True, "retention_days": 7}})
    httpx_job = scrape(service, preset_id="generic.sitemap_structured", preset_version="1.0.0", start_url=base + "/", run_mode="full", max_pages=30, engine="httpx")
    assert httpx_job["state"] == "completed", httpx_job
    assert httpx_job["result"]["warc_capture"] and Path(httpx_job["result"]["warc_capture"]).is_file()
    scrapy_job = scrape(service, preset_id="generic.sitemap_structured", preset_version="1.0.0", start_url=base + "/", run_mode="full", max_pages=30, engine="scrapy")
    assert scrapy_job["state"] == "completed", scrapy_job
    assert scrapy_job["result"]["engine"] == "scrapy"
    skus = lambda job: sorted(r["sku"] for r in ok(service, "dataset.rows", dataset_id=job["result"]["dataset_id"], limit=100) for r in [r["raw"]] if r.get("sku"))  # noqa: E731
    assert skus(httpx_job) == skus(scrapy_job) and len(skus(httpx_job)) == 12


def test_crawl_retry_resumes_from_persisted_frontier(service: Service, site) -> None:
    base, state = site
    preset = next(p for p in ok(service, "preset.list") if p["id"] == "generic.crawl_structured")
    custom = {k: v for k, v in preset.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    custom.update(id="custom.me.crawl", version="1.0.0")
    custom["discovery"] = {"mode": "crawl", "crawl": {"max_depth": 3, "same_host_only": True, "link_pattern": "^/(products|product/)", "extract_pattern": "^/product/"}}
    custom["validation"] = {"unique_by": ["sku"]}
    ok(service, "preset.save_custom", preset=custom)
    test = scrape(service, preset_id="custom.me.crawl", preset_version="1.0.0", start_url=base + "/products?page=1")
    assert test["state"] == "completed" and test["result"]["records_extracted"] > 0
    first = ok(service, "scrape.create_job", preset_id="custom.me.crawl", preset_version="1.0.0", start_url=base + "/products?page=1", policy_acknowledgement=True, purpose="internal_analysis", run_mode="full", max_pages=4)
    first_job = wait(service, first["job_id"])
    assert first_job["result"]["stop_reason"] == "max_pages"
    store = service.store
    done = {r[0] for r in store._connection.execute("SELECT url FROM crawl_frontier WHERE job_id = ? AND status = 'done'", (first["job_id"],))}
    assert len(done) == 4
    ok(service, "job.cancel", job_id=first["job_id"]) if first_job["state"] not in ("completed", "failed", "cancelled") else None
    store._connection.execute("UPDATE jobs SET state = 'failed' WHERE id = ?", (first["job_id"],))
    store._connection.commit()
    state.requests.clear()
    retried = wait(service, ok(service, "job.retry", job_id=first["job_id"])["job_id"])
    assert retried["state"] == "completed", retried
    assert not ({"/" + url.split("/", 3)[3] for url in done} & set(state.requests))


def test_watch_runs_and_reports_changes(service: Service, site) -> None:
    base, _ = site
    watch = ok(service, "watch.create", name="Widget prices", interval_minutes=60,
               params={"preset_id": "generic.sitemap_structured", "preset_version": "1.0.0", "start_url": base + "/", "policy_acknowledgement": True, "purpose": "price_monitoring", "engine": "httpx"})
    assert call(service, "watch.create", name="x", interval_minutes=5, params=watch["params"])["ok"] is False
    first = wait(service, ok(service, "watch.run_now", watch_id=watch["id"])["job_id"], timeout=60)
    assert first["state"] == "completed", first
    original = PRODUCTS[0]["price"]
    PRODUCTS[0]["price"] = "11.11"
    try:
        second = wait(service, ok(service, "watch.run_now", watch_id=watch["id"])["job_id"], timeout=60)
    finally:
        PRODUCTS[0]["price"] = original
    assert second["state"] == "completed", second
    # sitemap_structured has no unique_by, so the stored diff is empty; compare the two versions by sku instead
    runs = ok(service, "watch.get", watch_id=watch["id"])["runs"]
    assert len(runs) == 2 and runs[0]["previous_dataset_id"] == runs[1]["dataset_id"]
    diff = ok(service, "dataset.diff", before_dataset_id=runs[1]["dataset_id"], after_dataset_id=runs[0]["dataset_id"], unique_by=["sku"])
    assert diff["counts"]["changed"] == 1 and diff["changed"][0]["changes"]["price"] == {"before": original, "after": "11.11"}
    assert ok(service, "watch.set_status", watch_id=watch["id"], status="paused")["status"] == "paused"
    assert sources.due_watches(service.store, service.project["id"]) == []


def test_archive_job_reads_wayback_captures(service: Service, monkeypatch: pytest.MonkeyPatch) -> None:
    page = product_page(PRODUCTS[4])

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt" or request.url.path.startswith("/.well-known") or request.url.path == "/ai.txt":
            return httpx.Response(404)
        if request.url.path == "/cdx/search/cdx":
            assert request.url.params["url"] == "shop.test/product/*"
            return httpx.Response(200, json=[["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"],
                                             ["x", "20240101000000", "https://shop.test/product/SKU-5", "text/html", "200", "DIGEST5", "10"]])
        if "/web/20240101000000id_/" in request.url.path:
            return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(404)

    real = sources.make_client
    monkeypatch.setattr(sources, "make_client", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    job = wait(service, ok(service, "archive.create_job", archive="wayback", url_pattern="shop.test/product/*", preset_id="generic.structured_data",
                           preset_version="1.0.0", policy_acknowledgement=True, purpose="research", run_mode="full")["job_id"])
    assert job["state"] == "completed", job
    record = job["result"]["sample_records"][0]
    assert (record["sku"], record["archive"], record["archive_capture_time"], record["archive_digest"]) == ("SKU-5", "wayback", "20240101000000", "DIGEST5")
    assert job["result"]["dataset_id"]


def test_bulk_import_web_data_commons_subset(service: Service, tmp_path: Path) -> None:
    lines = []
    for n in range(30):
        graph = f"<https://biz{n}.{'example.de' if n % 3 else 'example.com'}/>"
        lines += [f"_:b{n} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/LocalBusiness> {graph} .",
                  f'_:b{n} <http://schema.org/name> "Business {n}" {graph} .', f'_:b{n} <http://schema.org/telephone> "+49 30 1234{n:03d}" {graph} .']
    path = tmp_path / "wdc.nq.gz"
    path.write_bytes(gzip.compress("\n".join(lines).encode()))
    job = wait(service, ok(service, "bulk.create_job", path=str(path), schema_types=["LocalBusiness"], domain_suffix=".de", run_mode="full")["job_id"])
    assert job["state"] == "completed", job
    assert job["result"]["records_extracted"] == 20 and all(r["source_url"].endswith(".de/") for r in job["result"]["sample_records"])


def test_suggestions_detection_and_maintenance(service: Service) -> None:
    books = "<html><body><ol>" + "".join(f"<li class='book'><h3><a href='/b/{i}'>Book {i}</a></h3><p class='price'>£{i}1.50</p></li>" for i in range(5)) + "</ol></body></html>"
    detected = ok(service, "scrape.detect_structured", html=product_page(PRODUCTS[0]), url="https://shop.test/p")
    assert detected["suggested_type"] == "Product"
    suggestion = ok(service, "scrape.suggest_selectors", html=books, url="https://books.test/", examples={"title": "Book 3", "price": "£31.50"})
    assert suggestion["record_root"] == {"css": "li.book"} and suggestion["record_count"] == 5
    proposals = ok(service, "scrape.propose_presets", html=books, url="https://books.test/")
    assert proposals["provider"] == "local_heuristic" and proposals["proposals"][0]["evaluation"]["passed"]
    remote = call(service, "scrape.propose_presets", html=books, url="https://books.test/", provider="model")
    assert remote["ok"] is False  # no endpoint configured

    preset = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list" and p["version"] == "1.1.0")
    custom = {k: v for k, v in preset.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    custom.update(id="custom.me.books", version="1.0.0")
    custom["extraction"] = {"record_root": {"css": "li.book"}, "fields": [{"key": "title", "required": True, "selectors": [{"css": "h3 a"}]}, {"key": "price", "selectors": [{"css": "p.price"}]}]}
    custom["health"] = {"fixture_tests": [], "expected": {"minimum_records": 2}}
    ok(service, "preset.save_custom", preset=custom)
    sources.save_fingerprints(service.store, custom, {"books.html": books})
    report = ok(service, "preset.maintenance_report", preset_id="custom.me.books", preset_version="1.0.0", html=books.replace("price", "cost"), url="https://books.test/")
    assert report["drift"] == [{"field": "price", "baseline": 1.0, "current": 0.0}]
    assert report["suggestions"][0]["suggested"] == "p.cost"


def test_new_presets_pass_health_checks_in_service(service: Service) -> None:
    results = {r["id"]: r["status"] for r in ok(service, "preset.health_check")}
    for preset_id in ("generic.structured_data", "generic.document_tables", "generic.feed", "osm.overpass_pois", "wikidata.sparql", "sec.submissions", "gdelt.doc_search"):
        assert results[preset_id] == "passed", preset_id


def test_studio_navigation_and_staging_apply_site_signals(service: Service, site, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataforge_scraping.signals import SignalChecker

    base, state = site
    state.robots = "User-agent: *\nDisallow: /product/SKU-2\n"
    checkers: dict[str, SignalChecker] = {}
    monkeypatch.setattr(sources, "shared_checker", lambda purpose: checkers.setdefault(purpose, SignalChecker(httpx.Client(), purpose, include_local=True)))
    preset = next(p for p in ok(service, "preset.list") if p["id"] == "generic.structured_data")
    allowed = ok(service, "scrape.check_url", preset=preset, url=base + "/product/SKU-1", scope_url=base + "/", purpose="internal_analysis")
    blocked = ok(service, "scrape.check_url", preset=preset, url=base + "/product/SKU-2", scope_url=base + "/", purpose="internal_analysis")
    assert allowed["allowed"] and not blocked["allowed"] and blocked["skippable"] and "robots.txt" in blocked["reason"]
    assert ok(service, "scrape.check_url", preset=preset, url=base + "/product/SKU-2", scope_url=base + "/")["allowed"]  # user browsing is not collection

    html = product_page(PRODUCTS[0])
    missing = call(service, "scrape.stage_rendered", preset=preset, pages=[{"url": base + "/product/SKU-1", "html": html}], run_mode="test", policy_acknowledgement=True)
    assert "purpose" in missing["error"]["message"]
    refused = call(service, "scrape.stage_rendered", preset=preset, pages=[{"url": base + "/product/SKU-2", "html": html}], run_mode="test", policy_acknowledgement=True, purpose="internal_analysis")
    assert "robots.txt" in refused["error"]["message"]
    state.tdmrep = [{"location": "/", "tdm-reservation": 1}]
    checkers.clear()
    tdm = ok(service, "scrape.check_url", preset=preset, url=base + "/product/SKU-1", scope_url=base + "/", purpose="internal_analysis")
    assert not tdm["allowed"] and not tdm["skippable"]
    state.tdmrep = None
    checkers.clear()
    job = wait(service, ok(service, "scrape.stage_rendered", preset=preset, pages=[{"url": base + "/product/SKU-1", "html": html}], run_mode="test", policy_acknowledgement=True, purpose="lead_research")["job_id"])
    assert job["state"] == "completed", job
    assert job["result"]["sample_records"][0]["sku"] == "SKU-1"
    assert ok(service, "scrape.run_signals", job_id=job["id"])[0]["robots_status"] == "ok"


def test_capture_to_fixture_health_suggestions_and_archive_compare(service: Service, site, monkeypatch: pytest.MonkeyPatch) -> None:
    base, _ = site
    ok(service, "settings.update", changes={"warc_capture": {"enabled": True, "retention_days": 7}})
    html_list = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list" and p["version"] == "1.1.0")
    custom = {k: v for k, v in html_list.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    custom.update(id="custom.me.product", version="1.0.0", pagination={"type": "none"}, validation={"unique_by": ["sku"]})
    custom["extraction"] = {"record_root": {"css": "main.product"}, "fields": [{"key": "sku", "required": True, "selectors": [{"css": "span.sku"}]}, {"key": "price", "selectors": [{"css": "span.price"}]}]}
    custom["request_limits"] = {**custom["request_limits"], "min_delay_ms": 0}
    ok(service, "preset.save_custom", preset=custom)
    job = scrape(service, preset_id="custom.me.product", preset_version="1.0.0", start_url=base + "/product/SKU-4")
    assert job["state"] == "completed", job

    fixture = ok(service, "preset.fixture_from_capture", job_id=job["id"])
    assert fixture["fixture"].startswith("project:fixtures/captured/") and fixture["source_url"].endswith("/product/SKU-4")
    healthy = {**custom, "version": "1.0.1", "health": {"fixture_tests": [fixture["fixture"]], "expected": {"minimum_records": 1}}}
    ok(service, "preset.save_custom", preset=healthy)
    [passed] = ok(service, "preset.health_check", preset_id="custom.me.product")[-1:]
    assert passed["status"] == "passed" and passed["version"] == "1.0.1"

    broken = {**healthy, "version": "1.0.2", "extraction": {**healthy["extraction"], "fields": [{"key": "sku", "required": True, "selectors": [{"css": "span.stock-code"}]}, healthy["extraction"]["fields"][1]]}}
    ok(service, "preset.save_custom", preset=broken)
    results = {r["version"]: r for r in ok(service, "preset.health_check", preset_id="custom.me.product")}
    assert results["1.0.2"]["status"] == "failed"
    assert any(s["field"] == "sku" and s["suggested"] == "span.sku" for s in results["1.0.2"]["suggestions"])

    versions = {"20200101000000": product_page(PRODUCTS[0]), "20250101000000": product_page({**PRODUCTS[0], "price": "99.00"})}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("/robots.txt", "/ai.txt") or request.url.path.startswith("/.well-known"):
            return httpx.Response(404)
        if request.url.path == "/cdx/search/cdx":
            header = ["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"]
            return httpx.Response(200, json=[header] + [["k", ts, "https://shop.test/product/SKU-1", "text/html", "200", "D" + ts, "1"] for ts in versions])
        for timestamp, page in versions.items():
            if f"/web/{timestamp}id_/" in request.url.path:
                return httpx.Response(200, text=page, headers={"content-type": "text/html"})
        return httpx.Response(404)

    real = sources.make_client
    monkeypatch.setattr(sources, "make_client", lambda *a, **k: real(*a, **{**k, "transport": httpx.MockTransport(handler)}))
    assert "unique_by" in call(service, "archive.create_job", archive="wayback", url_pattern="shop.test/product/*", preset_id="generic.structured_data", preset_version="1.0.0",
                                policy_acknowledgement=True, purpose="research", compare=True)["error"]["message"]
    compare = wait(service, ok(service, "archive.create_job", archive="wayback", url_pattern="shop.test/product/*", preset_id="custom.me.product", preset_version="1.0.1",
                                policy_acknowledgement=True, purpose="research", run_mode="test", compare=True)["job_id"])
    assert compare["state"] == "completed", compare
    diff = compare["result"]["archive_diff"]
    assert diff["counts"] == {"added": 0, "removed": 0, "changed": 1, "unchanged": 0, "unkeyed": 0}
    assert diff["changed"][0]["changes"]["price"] == {"before": "$19.99", "after": "$99.00"}
    assert [r["archive_capture_time"] for r in compare["result"]["sample_records"]] == ["20250101000000"]


def test_watches_refuse_presets_that_need_a_saved_credential(service: Service) -> None:
    base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.json_api")
    custom = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    custom.update(id="custom.me.keyedwatch", version="1.0.0")
    custom["strategy"] = {**custom["strategy"], "api_integration": {"auth": "bearer"}}
    ok(service, "preset.save_custom", preset=custom)
    refused = call(service, "watch.create", name="Keyed", interval_minutes=60, params={"preset_id": "custom.me.keyedwatch", "preset_version": "1.0.0", "start_url": "https://api.example.test/items",
                                                                                     "policy_acknowledgement": True, "purpose": "research", "credential_ref": "vendor"})
    assert "saved credential" in refused["error"]["message"]
