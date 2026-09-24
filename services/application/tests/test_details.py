"""Record details through the service: the detail_level job option, result summaries and contracts, staged
datasets, Scrape Studio staging, and bulk corpora."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from dataforge_application.api import Service

from test_contracts import validate
from test_workflows import call, ok, wait

sys.path.insert(0, str(Path(__file__).parents[3] / "workers" / "scraping" / "tests"))
from fixture_site import PRODUCTS, catalog_page, item_page, serve  # noqa: E402


@pytest.fixture()
def service(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Service:
    monkeypatch.setenv("DATAFORGE_APP_DATA", str(tmp_path / "appdata"))
    svc = Service()
    ok(svc, "project.create", path=str(tmp_path / "project"), name="Details")
    return svc


@pytest.fixture()
def site():
    base, state, server = serve()
    yield base, state
    server.shutdown()


def scrape(service: Service, **params) -> dict:
    params = {"policy_acknowledgement": True, "purpose": "internal_analysis", "run_mode": "test", "preset_id": "generic.html_list", "preset_version": "1.1.0", **params}
    return wait(service, ok(service, "scrape.create_job", **params)["job_id"], timeout=120)


def test_jobs_read_item_pages_by_default(service: Service, site) -> None:
    base, state = site
    default = scrape(service, start_url=base + "/catalog")
    assert default["params"]["detail_level"] == "full" and default["result"]["details"]["detail_pages"]["fetched"] == 2
    assert default["result"]["sample_records"][0]["detail.sku"] == "SKU-1"
    state.requests.clear()
    job = scrape(service, start_url=base + "/catalog", detail_level="standard")
    assert job["state"] == "completed", job
    assert job["params"]["detail_level"] == "standard"
    result = job["result"]
    validate("scrape-result.schema.json", result)
    assert result["details"]["level"] == "standard" and result["details"]["detail_pages"] is None
    assert result["details"]["groups"]["item"] > 5 and result["details"]["groups"]["page"] > 5
    first = result["sample_records"][0]
    assert first["item.price"] == "$19.99" and first["item.price_original"] == "$25.00" and first["page.breadcrumbs"] == "Home > Widgets"
    assert set(result["field_coverage"]) == {"title", "link"}  # details are summarized, not mixed into field coverage
    assert not any(path.startswith("/item/") for path in state.requests)


def test_full_level_reads_detail_pages_and_stages_them(service: Service, site) -> None:
    base, _ = site
    job = scrape(service, start_url=base + "/catalog", detail_level="full", run_mode="full")
    assert job["state"] == "completed", job
    result = job["result"]
    validate("scrape-result.schema.json", result)
    pages = result["details"]["detail_pages"]
    assert pages["fetched"] == 2 and pages["candidates"] >= 4 and result["details"]["groups"]["detail"] > 30
    assert any(e["payload"].get("stage") == "detail_page_extracted" for e in job["events"])
    records = {r["raw"]["title"]: r["raw"] for r in ok(service, "dataset.rows", dataset_id=result["dataset_id"], limit=50)}
    assert records["Widget 1"]["detail.sku"] == "SKU-1" and records["Widget 1"]["detail.spec.weight.number"] == 1.5
    assert records["Widget 2"]["detail.price"] == "29.99" and records["Missing 6"]["detail.status"] == 404


def test_detail_level_is_validated(service: Service, site) -> None:
    base, _ = site
    response = call(service, "scrape.create_job", preset_id="generic.html_list", preset_version="1.1.0", start_url=base + "/catalog",
                    policy_acknowledgement=True, purpose="internal_analysis", detail_level="everything")
    assert response["ok"] is False and "detail_level" in response["error"]["message"]


def test_none_level_keeps_the_lean_record_shape(service: Service, site) -> None:
    base, _ = site
    job = scrape(service, start_url=base + "/catalog", detail_level="none")
    first = job["result"]["sample_records"][0]
    assert set(first) == {"title", "link", "source_url", "source_retrieved_at", "preset_id", "preset_version", "strategy_used"}
    assert job["result"]["details"]["fields_added"] == 0


def _studio_draft(service: Service) -> dict:
    base = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list" and p["version"] == "1.1.0")
    draft = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    draft.update(id="custom.local.detail_cards", version="1.0.0", strategy={"preferred": "webview", "allowed": ["webview"]})
    return draft


def test_studio_staging_reads_sanitized_element_and_head_copies(service: Service, site) -> None:
    base, _ = site
    html = catalog_page([("/item/SKU-1", "Widget 1")])
    element = html[html.index("<article"): html.index("</article>") + len("</article>")]
    head = html[: html.index("<body>")] + "<body><h1>All widgets</h1></body></html>"
    pages = [{"url": base + "/catalog", "records": [{"title": "Widget 1", "link": "/item/SKU-1", "__element": element}], "head_html": head}]
    job = wait(service, ok(service, "scrape.stage_rendered", preset=_studio_draft(service), pages=pages, run_mode="test", policy_acknowledgement=True,
                          purpose="internal_analysis")["job_id"])
    assert job["state"] == "completed", job
    record = job["result"]["sample_records"][0]
    assert record["item.rating.value"] == 4 and record["item.price.amount"] == 19.99 and record["page.title"] == "Catalog"
    assert "__element" not in record and record["strategy_used"] == "webview"
    assert job["result"]["details"]["level"] == "full"
    full = wait(service, ok(service, "scrape.stage_rendered", preset=_studio_draft(service), pages=pages, run_mode="test", policy_acknowledgement=True,
                           purpose="internal_analysis", detail_level="full")["job_id"])
    assert any("Open each item's page" in w for w in full["result"]["warnings"]) and full["result"]["details"]["detail_pages"] is None


def test_bulk_corpus_records_get_value_details_for_their_country(service: Service, tmp_path: Path) -> None:
    graph = "<https://cafe.de/>"
    lines = [f"_:b <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/LocalBusiness> {graph} .",
             f'_:b <http://schema.org/name> "Café" {graph} .', f'_:b <http://schema.org/telephone> "030 1234567" {graph} .']
    path = tmp_path / "wdc.nq.gz"
    path.write_bytes(gzip.compress("\n".join(lines).encode()))
    job = wait(service, ok(service, "bulk.create_job", path=str(path), schema_types=["LocalBusiness"])["job_id"])
    record = job["result"]["sample_records"][0]
    assert record["phone.e164"] == "+49301234567" and record["phone.country"] == "DE"
    lean = wait(service, ok(service, "bulk.create_job", path=str(path), schema_types=["LocalBusiness"], detail_level="none")["job_id"])
    assert "phone.e164" not in lean["result"]["sample_records"][0]


ITEM_FIELDS = [{"key": "weight", "selectors": [{"css": "table.specs tr:nth-of-type(1) td"}]}, {"key": "absent", "selectors": [{"css": ".nowhere"}]}]


def test_item_page_preview_shows_own_fields_and_automatic_details(service: Service, site) -> None:
    base, _ = site
    preset = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list" and p["version"] == "1.1.0")
    draft = {**preset, "details": {"level": "full", "follow": {"fields": ITEM_FIELDS}}}
    preview = ok(service, "scrape.test_detail", preset=draft, url=base + "/item/SKU-2", purpose="internal_analysis")
    assert preview["fields"] == {"weight": "1.5 kg", "absent": None} and preview["missing"] == ["absent"]
    assert preview["details"]["detail.sku"] == "SKU-2" and preview["details"]["detail.spec.color"] == "Blue"
    from_html = ok(service, "scrape.test_detail", preset=draft, url="https://shop.test/item/SKU-1", html=item_page(PRODUCTS[0]))
    assert from_html["fields"]["weight"] == "1.5 kg"
    assert "HTTP 404" in call(service, "scrape.test_detail", preset=draft, url=base + "/item/NOPE", purpose="internal_analysis")["error"]["message"]
    broken = {**draft, "details": {"level": "full", "follow": {"fields": [{"key": "x", "selectors": []}]}}}
    assert "selector" in call(service, "scrape.test_detail", preset=broken, url=base + "/item/SKU-2")["error"]["message"]


def test_studio_item_pages_are_checked_and_merged(service: Service, site) -> None:
    base, _ = site
    draft = _studio_draft(service)
    draft["details"] = {"level": "full", "follow": {"field": "link", "fields": [{"key": "weight", "selectors": [{"css": "td"}]}]}}
    records = [
        {"title": "Widget 1", "link": base + "/item/SKU-1",
         "__detail": {"url": base + "/item/SKU-1", "fields": {"weight": " 1.5 kg "}, "html": item_page(PRODUCTS[0]), "retrieved_at": "2026-09-24T10:00:00Z"}},
        {"title": "Widget 2", "link": "https://elsewhere.example/x", "__detail": {"url": "https://elsewhere.example/x", "fields": {"weight": "9 kg"}}},
    ]
    job = wait(service, ok(service, "scrape.stage_rendered", preset=draft, pages=[{"url": base + "/catalog", "records": records}], run_mode="test",
                          policy_acknowledgement=True, purpose="internal_analysis", detail_level="full")["job_id"])
    assert job["state"] == "completed", job
    first, second = job["result"]["sample_records"]
    assert first["detail.weight"] == "1.5 kg" and first["detail.sku"] == "SKU-1" and first["detail.weight.number"] == 1.5
    assert "__detail" not in first and not any(k.startswith("detail.") for k in second)
    assert job["result"]["details"]["detail_pages"]["fetched"] == 1 and any("outside the preset scope" in w for w in job["result"]["warnings"])


def test_detect_fields_previews_every_card_field(service: Service) -> None:
    import re

    from retail_grid import grid

    html = grid(8)
    cards = re.findall(r"<div data-component-type='s-search-result'.*?</div></div>", html)
    from_copies = ok(service, "scrape.detect_fields", elements=cards, url="https://shop.test/s?k=widget")
    from_page = ok(service, "scrape.detect_fields", html=html, record_root="div[data-component-type=s-search-result]", url="https://shop.test/s")
    assert from_copies["cards"] == from_page["cards"] == 8
    keys = [f["key"] for f in from_copies["fields"]]
    assert {"item.price", "item.rating", "item.ratings", "item.delivery", "item.sales", "item.badge"} <= set(keys) and len(keys) >= 15
    assert "record_root" in call(service, "scrape.detect_fields", html=html)["error"]["message"]
    assert "invalid" in call(service, "scrape.detect_fields", html=html, record_root="div[[")["error"]["message"].lower()


def test_studio_reads_item_pages_in_the_background(service: Service, site, monkeypatch: pytest.MonkeyPatch) -> None:
    from dataforge_application import scraping

    base, state = site
    paces: list[int] = []
    real = scraping.details.follow_with_policy_client

    def spy(records, preset, **kwargs):
        paces.append(preset["request_limits"]["min_delay_ms"])
        return real(records, preset, **kwargs)

    monkeypatch.setattr(scraping.details, "follow_with_policy_client", spy)
    draft = _studio_draft(service)
    # What Studio sends in background mode: rendering in the WebView, HTTP only for item pages, the user's delay.
    draft.update(parent_preset_id="generic.html_list", parent_preset_version="1.1.0", strategy={"preferred": "webview", "allowed": ["webview", "http"]},
                 request_limits={**draft["request_limits"], "min_delay_ms": 0}, details={"level": "full", "follow": {"field": "link"}})
    records = [{"title": f"Widget {n}", "link": f"{base}/item/SKU-{n}"} for n in (1, 2)] + [{"title": "Elsewhere", "link": "https://elsewhere.example/item"}]
    job = wait(service, ok(service, "scrape.stage_rendered", preset=draft, pages=[{"url": base + "/catalog", "records": records}], run_mode="test",
                          policy_acknowledgement=True, purpose="internal_analysis")["job_id"])
    assert job["state"] == "completed", job
    assert paces == [scraping.STUDIO_MIN_ITEM_DELAY_MS]  # never faster than the floor, whatever the draft asks
    first, second, _ = job["result"]["sample_records"]
    assert first["detail.sku"] == "SKU-1" and second["detail.sku"] == "SKU-2"
    assert {"/item/SKU-1", "/item/SKU-2"} <= set(state.requests)
    events = [e["payload"] for e in job["events"] if e["payload"].get("stage") == "detail_page_extracted"]
    assert [(e["url"].removeprefix(base), e["status"]) for e in events] == [("/item/SKU-1", "done"), ("/item/SKU-2", "done"), ("https://elsewhere.example/item", "skipped")]
    assert all(isinstance(e["fetch_ms"], int) and isinstance(e["parse_ms"], int) and e["fields"] > 5 for e in events[:2])


def test_unsaved_studio_draft_cannot_broaden_its_base(service: Service, site) -> None:
    base, _ = site
    draft = _studio_draft(service)
    draft.update(parent_preset_id="generic.html_list", parent_preset_version="1.1.0", strategy={"preferred": "webview", "allowed": ["webview", "http", "api"]})
    pages = [{"url": base + "/catalog", "records": [{"title": "Widget 1", "link": base + "/item/SKU-1"}]}]
    error = call(service, "scrape.stage_rendered", preset=draft, pages=pages, run_mode="test", policy_acknowledgement=True, purpose="internal_analysis")["error"]
    assert "broaden" in error["message"]
