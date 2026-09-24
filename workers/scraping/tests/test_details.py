"""Record details: value, element, page, and detail-page data on every scraped record, on both engines.
All offline (127.0.0.1 only)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from dataforge_scraping import details, structured
from dataforge_scraping.diff import diff_records
from dataforge_scraping.extraction import extract_document, extract_html_pages
from dataforge_scraping.presets import validate_preset

from fixture_site import PRODUCTS, item_page, product_page, serve

PRESETS = Path(__file__).parents[3] / "packages" / "presets"
PROVENANCE_ONLY = {"source_url", "source_retrieved_at", "preset_id", "preset_version", "strategy_used"}


def load(name: str) -> dict:
    return json.loads((PRESETS / name).read_text(encoding="utf-8-sig"))


@pytest.fixture()
def site():
    base, state, server = serve()
    yield base, state
    server.shutdown()


def catalog_preset(level: str | None = "full", **details_config) -> dict:
    preset = copy.deepcopy(load("generic.html_list@1.1.0.json"))
    preset["url_scope"] = {"allowed_hosts": ["127.0.0.1"], "allowed_path_patterns": []}
    preset["request_limits"]["min_delay_ms"] = 0
    preset["extraction"]["record_root"] = {"css": "article.card"}
    preset["pagination"] = {"type": "none"}
    preset["validation"]["unique_by"] = []
    if level is not None:
        preset["details"] = {"level": level, **details_config}
    return preset


# --- value details ---------------------------------------------------------------------------------

@pytest.mark.parametrize(("key", "value", "expected"), [
    ("price", "Now only £1,299.50!", {"price.amount": 1299.5, "price.currency": "GBP"}),
    ("cost", "USD 20", {"cost.amount": 20, "cost.currency": "USD"}),
    ("phone", "(512) 555-0100", {"phone.e164": "+15125550100", "phone.country": "US", "phone.type": "fixed_line_or_mobile"}),
    ("posted", "12 Sept 2026", {"posted.iso": "2026-09-12"}),
    ("rating", "4.5/5", {"rating.value": 4.5, "rating.scale": 5}),
    ("stars", "Three", {"stars.value": 3, "stars.scale": 5}),
    ("summary", "Rated 4 out of 5", {"summary.value": 4, "summary.scale": 5}),
    ("reviews", "3,450 reviews", {"reviews.number": 3450, "reviews.unit": "reviews"}),
    ("views", "1.2k", {"views.number": 1200}),
    ("availability", "Only 3 left in stock", {"availability.in_stock": "true", "availability.quantity": 3}),
    ("stock", "Sold out", {"stock.in_stock": "false"}),
    ("address", "1600 Pennsylvania Ave NW, Washington, DC 20500",
     {"address.street": "1600 Pennsylvania Ave NW", "address.city": "Washington", "address.state": "DC", "address.postal_code": "20500"}),
    ("contact", "Jo@Example.COM", {"contact.normalized": "jo@example.com", "contact.domain": "example.com"}),
    ("website", "https://shop.example.co.uk/p/x.pdf", {"website.domain": "example.co.uk", "website.path": "/p/x.pdf", "website.file_type": "pdf"}),
    ("link", "/item/1", {"link.absolute": "https://shop.test/item/1", "link.domain": "shop.test"}),
])
def test_value_details_by_kind(key: str, value: str, expected: dict) -> None:
    assert details.value_details({key: value}, "https://shop.test/list") == expected


def test_value_details_leave_plain_values_alone() -> None:
    record = {"sku": "SKU-1", "title": "Widget 12", "date": "2026-09-12", "published": "2026-09-12T10:00:00Z", "count": 5, "note": "Call 12 times",
              "source_url": "https://shop.test/", "page.title": "£5 deal", "codes": "a | b"}
    assert details.value_details(record, "https://shop.test/") == {}
    # A derived key never replaces a value the preset already extracted.
    assert details.value_details({"price": "$5", "price.amount": "kept"}) == {"price.currency": "USD"}


def test_phone_region_follows_the_preset() -> None:
    preset = {"normalization": {"default_region": "GB"}}
    assert details.value_details({"phone": "020 7946 0958"}, region=details.region_of(preset))["phone.e164"] == "+442079460958"


# --- element and page details ----------------------------------------------------------------------

def test_element_details_read_everything_inside_a_card() -> None:
    card = ("<li class='card golden' data-product-id='42' data-reactid='7'><h3><a href='/p/42' title='Widget 42, full name'>Widget 42</a></h3>"
            "<img data-src='/i/42.jpg' alt='Widget photo'><img src='data:image/gif;base64,R0lG'>"
            "<span class='price price--old'>$120.00</span> <span class='price'>$99.50</span><span class='rating' aria-label='4.5 out of 5 stars'></span>"
            "<p class='stock'>Only 2 left</p><time datetime='2026-09-01'>Sept 1</time><span itemprop='sku' content='AB-42'></span>"
            "<a href='tel:+1 512 555 0100'>Call</a> <a href='mailto:Sales@Shop.test'>Mail</a><a href='javascript:void(0)'>x</a>"
            "<script>var secret = 1;</script><input type='hidden' name='token' value='abc'></li>")
    found = details.element_details(card, "https://shop.test/c")
    assert found["item.heading"] == "Widget 42" and found["item.title"] == "Widget 42, full name"
    assert found["item.link"] == "https://shop.test/p/42" and found["item.link_count"] == 1
    assert found["item.image"] == "https://shop.test/i/42.jpg" and found["item.image_alt"] == "Widget photo"
    assert (found["item.price"], found["item.price_original"]) == ("$99.50", "$120.00")
    assert found["item.rating"] == "4.5 out of 5 stars" and found["item.availability"] == "Only 2 left"
    assert found["item.datetime"] == "2026-09-01" and found["item.prop.sku"] == "AB-42"
    assert found["item.phone"] == "+15125550100" and found["item.email"] == "sales@shop.test"
    assert found["item.data.product_id"] == "42" and not any("react" in k or k.endswith(".src") for k in found)
    assert "secret" not in found["item.text"] and "abc" not in json.dumps(found)


def test_page_details_metadata_breadcrumbs_and_link_previews() -> None:
    html = ("<html lang='de'><head><title>Shop</title><meta name='description' content='All widgets'><link rel='canonical' href='/c?x=1'>"
            "<meta property='og:site_name' content='Widget Shop'><meta property='og:image' content='/og.jpg'><meta property='product:price:amount' content='9'>"
            "<meta name='twitter:label1' content='Reading time'><meta name='twitter:data1' content='4 min'><meta name='csrf-token' content='x1'>"
            "<meta property='article:published_time' content='2026-01-02'></head><body><h1> Widgets </h1>"
            "<nav aria-label='Breadcrumb'><ol><li><a href='/'>Home</a></li><li>Widgets</li></ol></nav></body></html>")
    found = details.page_details(html, "https://shop.test/c")
    assert found["page.title"] == "Shop" and found["page.description"] == "All widgets" and found["page.language"] == "de"
    assert found["page.canonical"] == "https://shop.test/c?x=1" and found["page.image"] == "https://shop.test/og.jpg"
    assert found["page.og.product_price_amount"] == "9" and found["page.twitter.reading_time"] == "4 min"
    assert found["page.breadcrumbs"] == "Home > Widgets" and found["page.h1"] == "Widgets" and found["page.published"] == "2026-01-02"
    assert not any("csrf" in key for key in found)


def test_detail_page_details_primary_entity_specs_contacts_and_social() -> None:
    found = details.detail_page_details(item_page(PRODUCTS[0]), "https://shop.test/item/SKU-1")
    assert (found["detail.schema_type"], found["detail.sku"], found["detail.brand"], found["detail.gtin"]) == ("Product", "SKU-1", "Acme", "0000000000001")
    assert (found["detail.price"], found["detail.price_currency"], found["detail.availability"]) == ("19.99", "USD", "InStock")
    assert found["detail.description"] == "A sturdy widget." and (found["detail.rating"], found["detail.review_count"]) == (4.4, 89)
    assert found["detail.spec.weight"] == "1.5 kg" and found["detail.spec.warranty"] == "2 years" and found["detail.spec.material"] == "steel"
    assert found["detail.spec.price_incl_tax.amount"] == 21.99 and found["detail.spec.availability.quantity"] == 7
    assert found["detail.emails"] == "sales@shop.test" and found["detail.phones"] == "+15125550100"
    assert found["detail.social.twitter"] == "https://twitter.com/widgetshop" and "detail.social.linkedin" in found
    assert found["detail.image"] == "https://shop.test/img/big-SKU-1.jpg" and found["detail.word_count"] > 60
    assert found["detail.page.title"] == "Widget 1 | Widget Shop"


# --- levels through the shared extraction path -----------------------------------------------------

def test_levels_add_details_step_by_step() -> None:
    html = ("<html><head><title>Catalog</title></head><body><article class='card'><h2><a href='/item/SKU-1'>Widget 1</a></h2>"
            "<p class='price'>$19.99</p><p class='star-rating Four'></p></article></body></html>")
    by_level = {}
    for level in details.LEVELS:
        preset = catalog_preset(level)
        preset["extraction"]["fields"].append({"key": "price", "selectors": [{"css": "p.price"}]})
        [record], _, _ = extract_document(html, "https://shop.test/c", preset)
        by_level[level] = record
    assert set(by_level["none"]) == {"title", "link", "price"} | PROVENANCE_ONLY
    assert by_level["basic"]["price.amount"] == 19.99 and not any(k.startswith(("item.", "page.")) for k in by_level["basic"])
    assert by_level["standard"]["item.rating.value"] == 4 and by_level["standard"]["page.title"] == "Catalog"
    assert set(by_level["full"]) == set(by_level["standard"])  # no network without a collection run
    assert all(details.detail_group(k, by_level["standard"]) is None for k in by_level["none"])


def test_default_level_is_standard_and_structured_records_carry_page_metadata() -> None:
    preset = load("generic.structured_data@1.0.0.json")
    assert details.level_of(preset) == "standard"
    records, _, _ = extract_document(product_page(PRODUCTS[0]), "https://shop.test/p", preset)
    assert records[0]["sku"] == "SKU-1" and records[0]["page.title"] == "Widget 1" and records[0]["price.amount"] == 19.99


def test_preset_details_section_is_validated() -> None:
    preset = catalog_preset("full", follow={"field": "link", "max_pages": 50, "text": "precise"})
    assert validate_preset(preset) == []
    for bad, message in (({"level": "everything"}, "details.level"), ({"follow": {"max_pages": -1}}, "details.follow.max_pages"),
                         ({"follow": {"field": ""}}, "details.follow.field"), ({"follow": "x"}, "details.follow must be"),
                         ({"follow": {"text": "slow"}}, "details.follow.text")):
        broken = catalog_preset(None)
        broken["details"] = bad
        assert any(message in error for error in validate_preset(broken)), bad
    webview_only = catalog_preset("full", follow={"fields": [{"key": "bullets", "selectors": [{"css": "#feature-bullets li"}]}]})
    webview_only["strategy"] = {"preferred": "webview", "allowed": ["webview"]}
    assert validate_preset(webview_only) == []  # Scrape Studio opens item pages in the WebView
    for fields, message in (([{"key": "Bad Key", "selectors": [{"css": "b"}]}], "keys must be unique"),
                            ([{"key": "a", "selectors": []}], "at least one CSS or XPath"),
                            ([{"key": "a", "selectors": [{"xpath": "//["}]}], "invalid XPath"),
                            ([{"key": "a", "type": "money", "selectors": [{"css": "b"}]}], "type must be"),
                            ([{"key": "a", "selectors": [{"css": "b"}], "transforms": ["nope"]}], "unknown transforms"),
                            ([{"key": "a", "selectors": [{"css": "b"}]}, {"key": "a", "selectors": [{"css": "c"}]}], "keys must be unique")):
        broken = catalog_preset("full", follow={"fields": fields})
        assert any(message in error for error in validate_preset(broken)), fields


# --- following detail pages ------------------------------------------------------------------------

def test_full_level_follows_detail_pages_under_policy(site) -> None:
    base, state = site
    events: list[dict] = []
    result = extract_html_pages(base + "/catalog", catalog_preset("full"), include_local_signals=True, on_page=events.append)
    records = {r["title"]: r for r in result.records}
    first = records["Widget 1"]
    assert first["detail.sku"] == "SKU-1" and first["detail.price"] == "19.99" and first["detail.spec.color"] == "Blue"
    assert first["item.price_original"] == "$25.00" and first["page.breadcrumbs"] == "Home > Widgets"
    assert records["Widget 1 again"]["detail.sku"] == "SKU-1"  # the same detail page is read once
    assert records["Missing 6"]["detail.status"] == 404 and "detail.sku" not in records["Missing 6"]
    assert not any(k.startswith("detail.") for k in records["Secret 4"]) and not any(k.startswith("detail.") for k in records["Elsewhere 5"])
    counts = {k: v for k, v in result.details.items() if not k.endswith("_ms")}
    assert counts == {"candidates": 6, "fetched": 2, "reused": 1, "failed": 1, "skipped_scope": 1, "skipped_robots": 1, "stop_reason": "completed"}
    assert result.details["fetch_ms"] >= 0 and result.details["parse_ms"] > 0
    assert any("outside the preset scope" in w for w in result.warnings) and any("robots.txt" in w for w in result.warnings)
    assert "/private/secret" not in state.requests and state.requests.count("/item/SKU-1") == 1
    outcomes = [(e["detail_page"], e["url"].removeprefix(base), e["status"]) for e in events if "detail_page" in e]
    assert outcomes == [(1, "/item/SKU-1", "done"), (2, "/item/SKU-2", "done"), (3, "/item/SKU-1", "reused"), (4, "/private/secret", "skipped"),
                        (5, "https://elsewhere.example/item", "skipped"), (6, "/item/NOPE", "failed")]
    done = next(e for e in events if e.get("status") == "done")
    assert done["fields"] > 30 and done["fetch_ms"] >= 0 and done["parse_ms"] > 0
    summary = details.summarize(list(result.records), "full", result.details)
    assert summary["records_enriched"] == 6 and summary["groups"]["detail"] > 30 and summary["coverage"]["item.link"] == 1.0


def test_standard_level_makes_no_extra_requests(site) -> None:
    base, state = site
    result = extract_html_pages(base + "/catalog", catalog_preset("standard"), include_local_signals=True)
    assert result.details is None and not any(path.startswith("/item/") for path in state.requests)
    assert result.records[0]["item.link"].endswith("/item/SKU-1")


def test_follow_stops_on_challenge_but_keeps_records(site) -> None:
    base, _ = site
    result = extract_html_pages(base + "/catalog-challenge", catalog_preset("full"), include_local_signals=True)
    assert len(result.records) == 3 and result.stop_reason == "completed"
    assert result.records[0]["detail.sku"] == "SKU-1" and not any(k.startswith("detail.") for k in result.records[2])
    assert "access challenge" in result.details["stop_reason"] and any(w.startswith("Stopped following detail pages") for w in result.warnings)


def test_follow_cap_field_choice_and_cancel(site) -> None:
    base, state = site
    capped = extract_html_pages(base + "/catalog", catalog_preset("full", follow={"max_pages": 1}), include_local_signals=True)
    assert capped.details["fetched"] == 1 and capped.details["stop_reason"] == "max_detail_pages"
    state.requests.clear()
    none_followed = extract_html_pages(base + "/catalog", catalog_preset("full", follow={"field": "no_such_field"}), include_local_signals=True)
    assert none_followed.details["candidates"] == 0 and not any(p.startswith("/item/") for p in state.requests)
    calls = {"n": 0}

    def stop_after_listing() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    cancelled = extract_html_pages(base + "/catalog", catalog_preset("full"), include_local_signals=True, should_stop=stop_after_listing)
    assert cancelled.stop_reason == "cancelled"


def test_detail_links_pagination_is_not_followed_twice(site) -> None:
    base, state = site
    preset = catalog_preset("full")
    preset["extraction"] = {"mode": "structured_data", "schema_types": ["Product"], "fields": []}
    preset["pagination"] = {"type": "detail_links", "links": {"css": "li.item a::attr(href)"}}
    result = extract_html_pages(base + "/products?page=1", preset, include_local_signals=True, max_records=2)
    assert [r["sku"] for r in result.records] == ["SKU-1", "SKU-2"] and result.details is None
    assert all(r["page.title"] == r["name"] for r in result.records)


def test_scrapy_engine_adds_the_same_details(site) -> None:
    from dataforge_scraping.engines.launcher import collect_with_scrapy

    base, _ = site
    preset = catalog_preset("full")
    httpx_result = extract_html_pages(base + "/catalog", preset, include_local_signals=True)
    scrapy_result = collect_with_scrapy(base + "/catalog", preset, 20, 5, include_local_signals=True)
    volatile = ("source_retrieved_at", "detail.retrieved_at")

    def comparable(result) -> list[dict]:
        return sorted(({k: v for k, v in r.items() if k not in volatile} for r in result.records), key=lambda r: r["title"])

    assert comparable(httpx_result) == comparable(scrapy_result)
    without_timings = lambda stats: {k: v for k, v in stats.items() if not k.endswith("_ms")}  # noqa: E731
    assert without_timings(scrapy_result.details) == without_timings(httpx_result.details) and scrapy_result.engine == "scrapy"


# --- structured data mappings ----------------------------------------------------------------------

def test_mappings_cover_jobs_events_recipes_vehicles_and_faqs() -> None:
    page = json.dumps([
        {"@type": "JobPosting", "title": "Welder", "datePosted": "2026-09-01", "employmentType": ["FULL_TIME", "CONTRACTOR"],
         "hiringOrganization": {"@type": "Organization", "name": "Acme", "sameAs": "https://acme.test"},
         "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": "Austin", "addressRegion": "TX"}},
         "baseSalary": {"@type": "MonetaryAmount", "currency": "USD", "value": {"@type": "QuantitativeValue", "minValue": 50000, "maxValue": 70000, "unitText": "YEAR"}},
         "description": "<p>Weld <b>things</b>.</p>"},
        {"@type": "MusicEvent", "name": "Live", "startDate": "2026-10-01T20:00", "eventStatus": "https://schema.org/EventScheduled",
         "location": {"@type": "Place", "name": "Hall", "address": {"@type": "PostalAddress", "addressLocality": "Austin"}},
         "performer": [{"@type": "Person", "name": "A"}, {"@type": "Person", "name": "B"}], "offers": {"@type": "Offer", "price": "25", "priceCurrency": "USD"}},
        {"@type": "Recipe", "name": "Soup", "recipeIngredient": ["water", "salt"], "recipeInstructions": [{"@type": "HowToStep", "text": "Boil"}, {"@type": "HowToStep", "text": "Salt"}],
         "nutrition": {"@type": "NutritionInformation", "calories": "90 kcal"}},
        {"@type": "Car", "name": "Roadster", "brand": {"@type": "Brand", "name": "Zoom"}, "vehicleIdentificationNumber": "VIN1",
         "mileageFromOdometer": {"@type": "QuantitativeValue", "value": 1200, "unitCode": "KMT"}, "offers": {"@type": "Offer", "price": 9000, "priceCurrency": "EUR"}},
        {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": "Why?", "acceptedAnswer": {"@type": "Answer", "text": "Because."}}]},
        {"@type": "Restaurant", "name": "Joe's", "openingHours": ["Mo-Fr 09:00-17:00", "Sa 10:00-14:00"], "geo": {"@type": "GeoCoordinates", "latitude": 30.2, "longitude": -97.7},
         "address": {"@type": "PostalAddress", "streetAddress": "1 Main St", "addressLocality": "Austin", "addressCountry": {"@type": "Country", "name": "US"}}},
    ])
    records = {r["schema_type"]: r for r in structured.extract_structured_records(f'<script type="application/ld+json">{page}</script>', "https://x.test/", {})}
    job = records["JobPosting"]
    assert (job["title"], job["hiring_organization"], job["city"], job["employment_type"]) == ("Welder", "Acme", "Austin", "FULL_TIME, CONTRACTOR")
    assert (job["salary_min"], job["salary_max"], job["salary_unit"], job["description"]) == (50000, 70000, "YEAR", "Weld things.")
    event = records["MusicEvent"]
    assert (event["venue"], event["city"], event["performer"], event["event_status"], event["price"]) == ("Hall", "Austin", "A, B", "EventScheduled", "25")
    assert records["Recipe"]["ingredients"] == "water; salt" and records["Recipe"]["instructions"] == "Boil | Salt" and records["Recipe"]["calories"] == "90 kcal"
    car = records["Car"]
    assert (car["brand"], car["vin"], car["mileage"], car["price"], car["price_currency"]) == ("Zoom", "VIN1", 1200, 9000, "EUR")
    assert records["Question"]["answer"] == "Because."
    restaurant = records["Restaurant"]
    assert restaurant["opening_hours"] == "Mo-Fr 09:00-17:00, Sa 10:00-14:00" and (restaurant["latitude"], restaurant["country"]) == (30.2, "US")


def test_structured_data_after_closing_html_tag_is_read() -> None:
    page = product_page(PRODUCTS[0]) + product_page(PRODUCTS[1]).replace("<html>", "<div>").replace("</html>", "</div>")
    assert [r["sku"] for r in structured.extract_structured_records(page, "https://shop.test/", {"schema_types": ["Product"]})] == ["SKU-1", "SKU-2"]


def test_watch_diffs_ignore_page_metadata_and_fetch_times() -> None:
    before = [{"sku": "A", "price": "1", "page.og.updated_time": "t1", "detail.page.title": "Old", "detail.retrieved_at": "t1", "detail.spec.color": "Red"}]
    after = [{"sku": "A", "price": "1", "page.og.updated_time": "t2", "detail.page.title": "New", "detail.retrieved_at": "t2", "detail.spec.color": "Blue"}]
    diff = diff_records(before, after, ["sku"])
    assert diff["changed"] == [{"key": {"sku": "A"}, "changes": {"detail.spec.color": {"before": "Red", "after": "Blue"}}}]


def test_summarize_groups_and_coverage() -> None:
    records = [{"price": "$5", "price.amount": 5, "item.link": "x", "page.title": "T", "detail.sku": "S"}, {"price": "$6", "price.amount": 6}]
    summary = details.summarize(records, "full", None)
    assert summary["groups"] == {"value": 1, "item": 1, "page": 1, "detail": 1} and summary["records_enriched"] == 2
    assert summary["coverage"]["price.amount"] == 1.0 and summary["coverage"]["detail.sku"] == 0.5


# --- custom item-page fields -------------------------------------------------------------------------

ITEM_FIELDS = [
    {"key": "weight", "selectors": [{"css": "table.specs tr:nth-of-type(1) td"}], "transforms": ["trim"]},
    {"key": "headline", "selectors": [{"css": "h2.missing"}, {"xpath": "//main/h1"}]},
    {"key": "photo", "type": "url", "selectors": [{"css": "main img::attr(src)"}], "transforms": ["to_absolute_url"]},
    {"key": "price", "type": "decimal", "selectors": [{"css": "table.specs tr:nth-of-type(3) td"}], "transforms": ["parse_price"]},
    {"key": "absent", "selectors": [{"css": ".nowhere"}]},
]


def test_custom_item_fields_come_first_and_win_over_automatic_values() -> None:
    preset = catalog_preset("full", follow={"fields": ITEM_FIELDS})
    found = details.detail_page_details(item_page(PRODUCTS[0]), "https://shop.test/item/SKU-1", preset=preset)
    assert list(found)[:4] == ["detail.weight", "detail.headline", "detail.photo", "detail.price"]
    assert (found["detail.weight"], found["detail.headline"], found["detail.photo"]) == ("1.5 kg", "Widget 1", "https://shop.test/img/big-SKU-1.jpg")
    assert found["detail.price"] == 21.99  # the preset's own price field replaces the structured-data offer price
    assert "detail.absent" not in found and found["detail.sku"] == "SKU-1" and found["detail.weight.number"] == 1.5


def test_full_level_applies_custom_item_fields_on_both_engines(site) -> None:
    from dataforge_scraping.engines.launcher import collect_with_scrapy

    base, _ = site
    preset = catalog_preset("full", follow={"field": "link", "fields": ITEM_FIELDS})
    httpx_result = extract_html_pages(base + "/catalog", preset, include_local_signals=True)
    first = next(r for r in httpx_result.records if r["title"] == "Widget 1")
    assert first["detail.weight"] == "1.5 kg" and first["detail.headline"] == "Widget 1" and first["detail.price"] == 21.99
    scrapy_result = collect_with_scrapy(base + "/catalog", preset, 20, 5, include_local_signals=True)
    same = next(r for r in scrapy_result.records if r["title"] == "Widget 1")
    assert {k: v for k, v in same.items() if k.startswith("detail.") and k != "detail.retrieved_at"} == {
        k: v for k, v in first.items() if k.startswith("detail.") and k != "detail.retrieved_at"}


def test_rendered_item_pages_use_the_webview_values_and_sanitized_copy() -> None:
    preset = catalog_preset("full", follow={"fields": [{"key": "bullets", "selectors": [{"css": "#bullets"}]},
                                                       {"key": "price", "type": "decimal", "selectors": [{"css": ".p"}], "transforms": ["parse_price"]}]})
    detail = {"url": "https://shop.test/item/SKU-1", "fields": {"bullets": "Steel; Blue", "price": "$21.99", "ignored": "x"},
              "html": item_page(PRODUCTS[0]), "retrieved_at": "2026-09-24T10:00:00Z"}
    found = details.rendered_detail(detail, preset, {"title": "Widget 1"})
    assert (found["detail.bullets"], found["detail.price"], found["detail.sku"]) == ("Steel; Blue", 21.99, "SKU-1")
    assert found["detail.url"] == "https://shop.test/item/SKU-1" and found["detail.retrieved_at"] == "2026-09-24T10:00:00Z"
    assert "detail.ignored" not in found
    without_copy = details.rendered_detail({**detail, "html": ""}, preset, {})
    assert without_copy["detail.bullets"] == "Steel; Blue" and "detail.sku" not in without_copy


def test_bot_checks_used_by_large_retailers_stop_collection() -> None:
    from dataforge_scraping.fetch import CHALLENGE_MARKERS

    page = "<form method='get' action='/errors/validateCaptcha'><input name='amzn'></form>"
    assert any(marker in page.lower() for marker in CHALLENGE_MARKERS)
    assert all(marker not in item_page(PRODUCTS[0]).lower() for marker in CHALLENGE_MARKERS)


# --- fields detected from the grid itself --------------------------------------------------------------

def _retail_cards(count: int = 16) -> list[str]:
    import re

    from retail_grid import grid

    return re.findall(r"<div data-component-type='s-search-result'.*?</div></div>", grid(count))


def test_grid_fields_find_what_every_card_shares_with_readable_names() -> None:
    cards = details.grid_details(_retail_cards(), "https://shop.test/s?k=widget")
    first, fourth = cards[0], cards[3]
    assert first["item.badge"] == "Best Seller" and "item.badge" not in fourth  # a flag on some cards, not boilerplate
    assert (first["item.ratings"], first["item.sales"], first["item.delivery"]) == ("1,000 ratings", "50+ bought in past month", "FREE delivery Thu, Oct 1")
    assert first["item.coupon"] == "Save 5% with coupon" and first["item.availability"] == "Only 1 left in stock - order soon."
    assert first["item.price"] == "$19.99" and first["item.rating"] == "3.0 out of 5 stars" and first["item.data.asin"] == "B000000000"
    keys = set().union(*cards)
    assert not {"item.size", "item.color", "item.count"} & keys  # utility classes and repeated numbers never become fields
    assert "item.data.component_type" not in keys and not any("add to cart" in str(v).lower() for card in cards for k, v in card.items() if k != "item.text")
    assert not any(v == "19." for card in cards for v in card.values())  # split screen-reader price pieces are skipped


def test_grid_fields_flow_through_extraction_with_value_details() -> None:
    from retail_grid import grid

    preset = catalog_preset("standard")
    preset["extraction"]["record_root"] = {"css": "div[data-component-type=s-search-result]"}
    preset["extraction"]["fields"] = [{"key": "title", "selectors": [{"css": "h2 a span"}]}]
    for parser in ("bs4", "parsel", "selectolax"):
        preset["extraction"]["parser"] = parser
        records, _, _ = extract_document(grid(6), "https://shop.test/s", preset)
        assert len(records) == 6, parser
        first = records[0]
        assert first["item.delivery"].startswith("FREE delivery") and first["item.price.amount"] == 19.99 and first["item.rating.value"] == 3.0, parser
        assert first["item.availability.in_stock"] == "true" and first["item.ratings.number"] == 1000, parser
    summary = details.describe_grid(_retail_cards(6), "https://shop.test/s")
    by_key = {f["key"]: f for f in summary}
    assert by_key["item.badge"]["coverage"] < 1 and by_key["item.delivery"]["examples"][0].startswith("FREE delivery")


def test_single_record_roots_keep_every_value() -> None:
    [record] = details.grid_details(["<div class='product'><h1>Solo</h1><span class='maker'>Acme</span><span class='weight'>2 kg</span></div>"], "https://x.test/")
    assert record["item.heading"] == "Solo" and record["item.weight"] == "2 kg" and any(v == "Acme" for v in record.values())


def test_grid_details_are_fast_enough_for_large_grids() -> None:
    import time

    cards = _retail_cards(16) * 10
    details.grid_details(cards[:5], "https://shop.test/")
    started = time.perf_counter()
    details.grid_details(cards, "https://shop.test/")
    per_card = (time.perf_counter() - started) * 1000 / len(cards)
    assert per_card < 10, per_card  # about 1 ms on a developer machine; the bound only catches pathological regressions


def test_item_pages_read_json_ld_first_and_skip_slow_syntaxes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple] = []
    original = structured.extract_entities

    def spy(html, url, syntaxes=structured.SYNTAXES):
        calls.append(tuple(syntaxes))
        return original(html, url, syntaxes)

    monkeypatch.setattr(structured, "extract_entities", spy)
    found = details.detail_page_details(item_page(PRODUCTS[0]), "https://shop.test/item/SKU-1")
    assert calls == [("json-ld",)] and found["detail.sku"] == "SKU-1"
    calls.clear()
    microdata = "<div itemscope itemtype='https://schema.org/Product'><span itemprop='name'>Solo</span><meta itemprop='sku' content='S-1'></div>"
    assert details.detail_page_details(microdata, "https://x.test/p")["detail.sku"] == "S-1" and calls == [("json-ld",), ("microdata", "microformat")]


def test_item_pages_show_visible_facts_and_bullets() -> None:
    page = ("<html><body><main><h1>Solo Widget</h1><span class='price'><del>$30.00</del> <b>$25.00</b></span><div id='availability'>In stock</div>"
            "<span class='rating' aria-label='4.5 out of 5 stars'></span><ul><li>Made from brushed stainless steel for years of use</li>"
            "<li>Fits every standard kitchen drawer and cupboard</li><li>Dishwasher safe and easy to clean after cooking</li></ul></main></body></html>")
    found = details.detail_page_details(page, "https://x.test/p")
    assert (found["detail.heading"], found["detail.price_shown"], found["detail.price_original"]) == ("Solo Widget", "$25.00", "$30.00")
    assert found["detail.availability_shown"] == "In stock" and found["detail.rating_shown"] == "4.5 out of 5 stars"
    assert found["detail.bullet_count"] == 3 and found["detail.bullets"].startswith("Made from brushed")
    assert found["detail.price_shown.amount"] == 25.0 and found["detail.rating_shown.value"] == 4.5


def test_item_fields_picked_in_the_webview_match_raw_html() -> None:
    preset = catalog_preset("full", follow={"fields": [
        {"key": "weight", "selectors": [{"css": "body > main > table.specs > tbody > tr:nth-of-type(1) > td"}]},
        {"key": "color", "selectors": [{"css": "div.gone"}, {"xpath": "./main[1]/table[1]/tbody[1]/tr[2]/td[1]"}]},
    ]})
    found = details.detail_fields(item_page(PRODUCTS[0]), "https://shop.test/item/SKU-1", preset)
    assert found == {"detail.weight": "1.5 kg", "detail.color": "Blue"}
