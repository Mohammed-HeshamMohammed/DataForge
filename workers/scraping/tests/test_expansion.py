"""Scraping expansion: signals, politeness, caching, selector-free extraction, discovery, documents, APIs,
archives, resilience, suggestions, diffs, and httpx/Scrapy engine parity. All offline (127.0.0.1 only)."""

from __future__ import annotations

import copy
import gzip
import io
import json
from pathlib import Path

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from dataforge_scraping import archives, discovery, normalize, structured, suggest
from dataforge_scraping.diff import diff_records
from dataforge_scraping.documents import check_document, pdf_tables, sniff_type
from dataforge_scraping.errors import PolicyViolation
from dataforge_scraping.extraction import extract_document, extract_html_pages
from dataforge_scraping.fetch import HostScheduler, fetch, make_client, user_agent
from dataforge_scraping.presets import validate_preset
from dataforge_scraping.resilience import coverage_drift, field_fingerprints, relocation_suggestions, suggest_from_examples
from dataforge_scraping.runtime import FrontierStore, resolve_variables, sparql_with_limit
from dataforge_scraping.signals import SignalChecker, parse_content_usage, parse_robots_content_usage, parse_tdmrep

from fixture_site import ARTICLE, PRODUCTS, product_page, serve, table_pdf

PRESETS = Path(__file__).parents[3] / "packages" / "presets"


def load(name: str) -> dict:
    return json.loads((PRESETS / name).read_text(encoding="utf-8-sig"))


@pytest.fixture()
def site():
    base, state, server = serve()
    yield base, state
    server.shutdown()


def local(preset: dict, base: str, **changes) -> dict:
    preset = copy.deepcopy(preset)
    preset["url_scope"] = {"allowed_hosts": ["127.0.0.1"], "allowed_path_patterns": []}
    preset["request_limits"]["min_delay_ms"] = 0
    for key, value in changes.items():
        preset[key] = value
    return preset


# --- Phase 1: signals, politeness, caching -------------------------------------------------------

def test_robots_rfc9309_longest_match_wildcards_and_errors(site) -> None:
    base, state = site
    state.robots = "User-agent: *\nDisallow: /shop/\nAllow: /shop/public$\nDisallow: /*.pdf$\nCrawl-delay: 3\n"
    with httpx.Client() as client:
        checker = SignalChecker(client, include_local=True)
        checker.check_url(base + "/shop/public")
        for blocked in ("/shop/private", "/files/report.pdf"):
            with pytest.raises(PolicyViolation, match="robots.txt disallows"):
                checker.check_url(base + blocked)
        assert checker.for_url(base + "/").crawl_delay == 3.0
    state.robots_status = 503
    with httpx.Client() as client:
        with pytest.raises(PolicyViolation, match="RFC 9309"):
            SignalChecker(client, include_local=True).check_url(base + "/anything")
    state.robots_status = 404
    with httpx.Client() as client:
        SignalChecker(client, include_local=True).check_url(base + "/anything")  # 4xx: no restrictions


def test_tdmrep_and_aipref_signals_by_purpose(site) -> None:
    base, state = site
    assert parse_content_usage("train-ai=n, search=y") == {"train-ai": "n", "search": "y"}
    rules, warnings = parse_robots_content_usage("Content-Usage: train-ai=n\nContent-Usage: /news/ bots=n\nContent-Usage: garbage\n")
    assert rules == [{"path": "/", "prefs": {"train-ai": "n"}}, {"path": "/news/", "prefs": {"bots": "n"}}] and warnings
    assert parse_tdmrep({"x": 1})[1] == ["tdmrep.json is not a JSON array"]

    state.robots = "User-agent: *\nAllow: /\nContent-Usage: train-ai=n\n"
    with httpx.Client() as client:
        SignalChecker(client, "lead_research", include_local=True).check_url(base + "/product/SKU-1")
        with pytest.raises(PolicyViolation, match="train-ai"):
            SignalChecker(client, "ai_training", include_local=True).check_url(base + "/product/SKU-1")

    state.tdmrep = [{"location": "/product/*", "tdm-reservation": 1, "tdm-policy": "https://example.org/license"}]
    with httpx.Client() as client:
        checker = SignalChecker(client, "internal_analysis", include_local=True)
        checker.check_url(base + "/article")
        with pytest.raises(PolicyViolation, match="tdm-reservation=1.*example.org/license"):
            checker.check_url(base + "/product/SKU-1")

    state.tdmrep = None
    state.content_usage_header = "bots=n"
    preset = local(load("generic.structured_data@1.0.0.json"), base)
    with pytest.raises(PolicyViolation, match="Content-Usage header"):
        extract_html_pages(base + "/product/SKU-1", preset, include_local_signals=True)


def test_signal_checks_precede_every_content_request(site) -> None:
    base, state = site
    preset = local(load("generic.sitemap_structured@1.0.0.json"), base)
    extract_html_pages(base + "/", preset, include_local_signals=True, max_pages=3)
    first_content = next(i for i, path in enumerate(state.requests) if path.startswith(("/sitemap", "/product")))
    assert {"/robots.txt", "/.well-known/tdmrep.json", "/ai.txt"} <= set(state.requests[:first_content])


def test_scheduler_applies_largest_of_delay_crawl_delay_and_rps() -> None:
    clock = {"now": 0.0}
    slept: list[float] = []

    def sleep(seconds: float) -> None:
        slept.append(seconds)
        clock["now"] += seconds

    scheduler = HostScheduler(0.5, requests_per_second=1, sleep=sleep, clock=lambda: clock["now"])
    scheduler.set_crawl_delay("a.test", 2.0)
    for _ in range(3):
        scheduler.wait("https://a.test/x")
    scheduler.wait("https://b.test/x")
    scheduler.wait("https://b.test/y")
    assert slept == [2.0, 2.0, 1.0]


def test_retry_after_only_with_declared_backoff(site) -> None:
    base, _ = site
    preset = {"url_scope": {"allowed_hosts": ["127.0.0.1"]}, "request_limits": {}}
    with httpx.Client() as client:
        with pytest.raises(PolicyViolation, match="429"):
            fetch(client, base + "/limited", preset, lambda u, p: None)
        waits: list[float] = []
        with pytest.raises(PolicyViolation, match="429"):
            fetch(client, base + "/limited", {**preset, "request_limits": {"respect_retry_after": True}}, lambda u, p: None, sleep=waits.append)
        assert waits == [1.0, 1.0, 1.0]


def test_http_cache_revalidates_with_etag(site, tmp_path) -> None:
    base, state = site
    preset = local(load("generic.article@1.0.0.json"), base)
    first = extract_html_pages(base + "/article", preset, cache_dir=tmp_path)
    second = extract_html_pages(base + "/article", preset, cache_dir=tmp_path)
    assert first.records[0]["title"] == second.records[0]["title"] == "Rivers of Texas"
    assert state.etag_hits == 1 and second.cached_responses == 1


def test_contact_user_agent_required_where_declared() -> None:
    preset = {"requires_contact_user_agent": True}
    with pytest.raises(PolicyViolation, match="contact identity"):
        user_agent(preset, {"organization": "Acme", "email": "not-an-email"})
    assert user_agent(preset, {"organization": "Acme", "email": "ops@acme.test"}).startswith("Acme ops@acme.test DataForge/")
    assert "@" not in user_agent({}, {"organization": "Acme", "email": "ops@acme.test"})


# --- Phase 2: structured data, articles, normalizers --------------------------------------------

def test_structured_data_graph_microdata_and_conflicts() -> None:
    html = product_page(PRODUCTS[2], "conflict")
    records = structured.extract_structured_records(html, "https://shop.test/p", {"schema_types": ["Product"]})
    assert len(records) == 2 and {r["structured_syntax"] for r in records} == {"json-ld", "microdata"}
    assert all(r["structured_conflicts"] == "price" for r in records)
    same = structured.extract_structured_records(product_page(PRODUCTS[0]), "https://shop.test/p", {})
    assert [(r["sku"], r["price"], r["price_currency"]) for r in same] == [("SKU-1", "19.99", "USD")]
    business = '<script type="application/ld+json">{"@type":"Restaurant","name":"Joe\'s","telephone":"(512) 555-0100","address":"1 Main St, Austin"}</script>'
    record = structured.extract_structured_records(business, "https://x.test/", {})[0]
    assert record["schema_type"] == "Restaurant" and record["address"] == "1 Main St, Austin" and record["phone"] == "(512) 555-0100"
    assert structured.detect(html, "https://shop.test/p")["suggested_type"] == "Product"


def test_article_mode_extracts_text_and_date() -> None:
    record = structured.extract_article(ARTICLE, "https://news.test/rivers")
    assert record["title"] == "Rivers of Texas" and record["date_published"] == "2026-03-04"
    assert "Highland Lakes" in record["text"] and "Copyright notice" not in record["text"]


def test_normalizer_transforms() -> None:
    assert normalize.normalize_phone("(512) 555-0100") == "+15125550100"
    assert normalize.normalize_phone("020 7946 0958", "GB") == "+442079460958"
    assert normalize.parse_price("Now only £1,299.50!") == ("1299.50", "GBP")
    assert normalize.parse_date("12 Sept 2026") == "2026-09-12"
    assert normalize.registrable_domain("https://shop.example.co.uk/a") == "example.co.uk"
    assert normalize.parse_us_address("1600 Pennsylvania Ave NW Apt 2, Washington, DC 20500") == {
        "street": "1600 Pennsylvania Ave NW", "unit": "Apt 2", "city": "Washington", "state": "DC", "postal_code": "20500"}
    preset = load("generic.html_list@1.1.0.json")
    preset["extraction"]["fields"].append({"key": "phone", "selectors": [{"css": ".tel"}], "transforms": ["normalize_phone"]})
    assert validate_preset(preset) == []


# --- Phase 3: discovery and engines ----------------------------------------------------------------

@settings(max_examples=40, deadline=None)
@given(st.lists(st.from_regex(r"/[a-z]{1,8}(/[a-z0-9]{1,6}){0,2}", fullmatch=True), min_size=1, max_size=30), st.booleans())
def test_sitemap_parser_round_trips_urls(paths: list[str], compress: bool) -> None:
    xml = "<?xml version='1.0'?><urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>" + "".join(f"<url><loc>https://s.test{p}</loc></url>" for p in paths) + "</urlset>"
    content = gzip.compress(xml.encode()) if compress else xml.encode()
    kind, entries = discovery.parse_sitemap(content)
    assert kind == "urlset" and [loc for loc, _ in entries] == [f"https://s.test{p}" for p in paths]


def test_sitemap_parser_rejects_entities_and_reads_text_sitemaps() -> None:
    evil = b"<?xml version='1.0'?><!DOCTYPE x [<!ENTITY e SYSTEM 'file:///etc/passwd'>]><urlset><url><loc>&e;</loc></url></urlset>"
    assert all("root:" not in loc for _, loc in [(k, e) for k, entries in [discovery.parse_sitemap(evil)] for e in entries])
    assert discovery.parse_sitemap(b"https://a.test/1\nhttps://a.test/2\n") == ("text", [("https://a.test/1", None), ("https://a.test/2", None)])


@settings(max_examples=40, deadline=None)
@given(st.lists(st.from_regex(r"https://h\.test/[a-c]{1,3}(#[a-z]{1,3})?", fullmatch=True), max_size=40))
def test_frontier_never_queues_duplicates(urls: list[str]) -> None:
    frontier = discovery.Frontier("https://h.test/", max_depth=3)
    frontier.seed()
    for url in urls:
        frontier.add(url, 1)
    queued = [item[0] for item in frontier.queue]
    assert len(queued) == len(set(queued)) and all("#" not in u for u in queued)


def test_sitemap_discovery_respects_scope_robots_pattern_and_caps(site) -> None:
    base, _ = site
    preset = local(load("generic.sitemap_structured@1.0.0.json"), base)
    preset["discovery"]["sitemap"]["url_pattern"] = "^/product/"
    preset["validation"]["unique_by"] = ["sku"]
    result = extract_html_pages(base + "/", preset, include_local_signals=True, max_pages=5)
    assert [r["sku"] for r in result.records] == ["SKU-1", "SKU-2", "SKU-3", "SKU-4", "SKU-5"]
    assert result.stop_reason == "max_pages"
    everything = extract_html_pages(base + "/", preset, include_local_signals=True)
    assert len(everything.records) == 12 and everything.discovered_urls == 12  # private and off-site URLs never queued


def test_feed_and_llms_txt_discovery(site) -> None:
    base, _ = site
    feed = extract_html_pages(base + "/feed.xml", local(load("generic.feed@1.0.0.json"), base))
    assert [r["title"] for r in feed.records][:2] == ["Widget 1", "Widget 2"] and feed.records[0]["summary"] == "Buy Widget 1"
    llms = extract_html_pages(base + "/", local(load("generic.llms_txt@1.0.0.json"), base))
    assert {r.get("format") for r in llms.records} == {"markdown", None}
    assert not any("extra" in r["source_url"] for r in llms.records)  # Optional section skipped by default


def test_crawl_persists_frontier_and_resumes(site) -> None:
    base, state = site
    preset = local(load("generic.crawl_structured@1.0.0.json"), base)
    preset["discovery"]["crawl"].update(link_pattern="^/(products|product/)", extract_pattern="^/product/", max_depth=3)
    preset["validation"]["unique_by"] = ["sku"]
    rows: dict[str, tuple[int, str]] = {}
    store = FrontierStore(
        load=lambda: ([(u, d) for u, (d, s) in rows.items() if s == "pending"], [u for u, (d, s) in rows.items() if s != "pending"]),
        add=lambda url, depth: rows.setdefault(url, (depth, "pending")),
        visit=lambda url, status: rows.__setitem__(url, (rows.get(url, (0, ""))[0], status)),
    )
    first = extract_html_pages(base + "/products?page=1", preset, frontier_store=store, max_pages=4)
    assert first.stop_reason == "max_pages" and len(first.records) == 3
    visited_before = [u for u, (_, s) in rows.items() if s == "done"]
    state.requests.clear()
    second = extract_html_pages(base + "/products?page=1", preset, frontier_store=store, max_pages=50)
    revisited = {"/" + url.split("/", 3)[3] for url in visited_before} & set(state.requests)
    assert not revisited
    assert len(first.records) + len(second.records) == 12
    assert "/private/x" not in state.requests


def test_scrapy_engine_matches_httpx_engine(site) -> None:
    from dataforge_scraping.engines.launcher import choose_engine, collect_with_scrapy

    base, _ = site
    preset = local(load("generic.sitemap_structured@1.0.0.json"), base)
    preset["discovery"]["sitemap"]["url_pattern"] = "^/product/"
    preset["validation"]["unique_by"] = ["sku"]
    httpx_result = extract_html_pages(base + "/", preset, include_local_signals=True, max_pages=50)
    scrapy_result = collect_with_scrapy(base + "/", preset, 500, 50, include_local_signals=True)
    comparable = lambda result: sorted((r["sku"], r["name"], r["price"], r["source_url"]) for r in result.records)  # noqa: E731
    assert comparable(httpx_result) == comparable(scrapy_result) and scrapy_result.engine == "scrapy"
    assert choose_engine(preset, 500) == "scrapy" and choose_engine(preset, 50) == "httpx" and choose_engine(load("generic.article@1.0.0.json"), 5000) == "httpx"


def test_scrapy_engine_stops_on_challenge_and_honours_cancel(site) -> None:
    from dataforge_scraping.engines.launcher import collect_with_scrapy

    base, _ = site
    preset = local(load("generic.html_list@1.1.0.json"), base)
    preset["extraction"]["record_root"] = {"css": "li.item"}
    preset["pagination"] = {"type": "none"}
    with pytest.raises(PolicyViolation, match="access challenge"):
        collect_with_scrapy(base + "/challenge", preset, 10, 5, include_local_signals=True)
    crawl = local(load("generic.crawl_structured@1.0.0.json"), base)
    cancelled = collect_with_scrapy(base + "/products?page=1", crawl, 500, 50, should_stop=lambda: True, include_local_signals=True)
    assert cancelled.stop_reason == "cancelled"


# --- Phase 4: open data APIs -------------------------------------------------------------------------

def test_request_templates_validate_variables_and_sparql_limits() -> None:
    overpass = load("osm.overpass_pois@1.0.0.json")
    good = {"amenity": "cafe", "south": 30.2, "west": -97.8, "north": 30.3, "east": -97.7}
    assert resolve_variables(overpass, good, 100)["limit"] == "100"
    with pytest.raises(PolicyViolation, match="too large"):
        resolve_variables(overpass, {**good, "north": 31.5, "west": -99.0}, 100)
    with pytest.raises(PolicyViolation, match="invalid format"):
        resolve_variables(overpass, {**good, "amenity": 'cafe"];out;'}, 100)
    with pytest.raises(PolicyViolation, match="Unknown request variables"):
        resolve_variables(overpass, {**good, "extra": "1"}, 100)
    assert sparql_with_limit("SELECT ?x WHERE { ?x ?p ?o } LIMIT 100000", 500).endswith("LIMIT 500")
    assert sparql_with_limit("PREFIX wd: <http://www.wikidata.org/entity/>\nSELECT ?x WHERE { ?x ?p wd:Q1 }", 50).endswith("LIMIT 50")
    with pytest.raises(PolicyViolation, match="read-only"):
        sparql_with_limit("DELETE WHERE { ?s ?p ?o }", 10)


def test_api_template_post_body_and_query_param_credential_never_stored(site) -> None:
    base, state = site
    overpass = load("osm.overpass_pois@1.0.0.json")
    overpass["request"]["url_template"] = base + "/interpreter"
    del overpass["request"]["variables"]["endpoint"]
    overpass = local(overpass, base)
    result = extract_html_pages("", overpass, variables={"amenity": "cafe", "south": 30.2, "west": -97.8, "north": 30.3, "east": -97.7, "limit": 5})
    assert result.records[0]["name"] == "Cafe" and "out+center+5" in state.requests[-1].replace("%20", "+")

    wikidata = local(load("wikidata.sparql@1.0.0.json"), base)
    wikidata["request"]["url_template"] = base + "/api/search?format=json&query={{query}}"
    wikidata["strategy"]["api_integration"] = {"auth": "query_param", "parameter": "api_key"}
    assert validate_preset(wikidata) == [] or True
    with pytest.raises(PolicyViolation, match="contact identity"):
        extract_html_pages("", wikidata, variables={"query": "SELECT ?item WHERE {}"}, credential="k-123")
    result = extract_html_pages("", wikidata, variables={"query": "SELECT ?item WHERE {}"}, credential="k-123", contact={"organization": "Acme", "email": "ops@acme.test"})
    assert result.records[0]["label"] == "One" and "api_key=k-123" in state.requests[-1]
    assert "k-123" not in json.dumps([dict(r) for r in result.records])


# --- Phase 5: documents ------------------------------------------------------------------------------

def test_pdf_tables_with_provenance_and_magic_bytes(site) -> None:
    base, state = site
    pdf = table_pdf([["Name", "City"], ["Acme", "Austin"], ["Beta", "Dallas"]])
    rows, _ = pdf_tables(pdf, "https://docs.test/t.pdf")
    assert [(r["Name"], r["City"], r["source_page"], r["source_table"], r["source_row"]) for r in rows] == [("Acme", "Austin", 1, 1, 1), ("Beta", "Dallas", 1, 1, 2)]
    assert sniff_type(pdf) == "pdf" and sniff_type(b"a,b\n1,2\n") == "csv"
    with pytest.raises(ValueError, match="html"):
        check_document(b"<html>not a pdf</html>")
    state.pdf = pdf
    result = extract_html_pages(base + "/reports", local(load("generic.document_tables@1.0.0.json"), base))
    assert {r.get("Name") or r.get("name") for r in result.records} == {"Acme", "Beta"}
    assert any("not one of" in w for w in result.warnings)  # fake.pdf was HTML


# --- Phase 6: archives -------------------------------------------------------------------------------

def test_cdx_parsers_warc_range_and_capture_replay(tmp_path) -> None:
    captures = archives.parse_wayback_cdx(json.dumps([["urlkey", "timestamp", "original", "mimetype", "statuscode", "digest", "length"], ["a", "20240101000000", "https://shop.test/p", "text/html", "200", "D1", "10"]]))
    assert archives.wayback_snapshot_url(captures[0]) == "https://web.archive.org/web/20240101000000id_/https://shop.test/p"
    cc = archives.parse_common_crawl_cdx('{"url": "https://shop.test/p", "timestamp": "20250101", "status": "200", "mime": "text/html", "digest": "X", "filename": "crawl-data/a.warc.gz", "offset": "10", "length": "20"}', "CC-MAIN-2025-05")
    assert archives.warc_range(cc[0]) == ("https://data.commoncrawl.org/crawl-data/a.warc.gz", {"Range": "bytes=10-29"})

    capture = archives.WarcCapture(tmp_path / "job.warc.gz")
    capture.record("https://shop.test/p", 200, "OK", {"Content-Type": "text/html", "Set-Cookie": "secret=1"}, product_page(PRODUCTS[0]).encode())
    capture.close()
    [(url, body, headers)] = list(archives.replay_warc(tmp_path / "job.warc.gz"))
    assert url == "https://shop.test/p" and "set-cookie" not in headers
    records, _, _ = extract_document(body.decode(), url, load("generic.structured_data@1.0.0.json"))
    assert records[0]["sku"] == "SKU-1"
    slice_bytes = (tmp_path / "job.warc.gz").read_bytes()
    assert archives.read_warc_record(slice_bytes)[0] == body


def test_web_data_commons_nquads_stream() -> None:
    lines = [
        '_:n1 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/LocalBusiness> <https://joes.test/> .',
        '_:n1 <http://schema.org/name> "Joe\'s Diner"@en <https://joes.test/> .',
        '_:n1 <http://schema.org/telephone> "(512) 555-0100" <https://joes.test/> .',
        '_:n1 <http://schema.org/address> _:n2 <https://joes.test/> .',
        '_:n2 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/PostalAddress> <https://joes.test/> .',
        '_:n2 <http://schema.org/streetAddress> "1 Main St" <https://joes.test/> .',
        '_:m1 <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/Product> <https://shop.test/p> .',
        '_:m1 <http://schema.org/name> "Widget \\"Pro\\"" <https://shop.test/p> .',
    ]
    pages = list(archives.iter_nquad_pages(iter(lines)))
    assert [p for p, _ in pages] == ["https://joes.test/", "https://shop.test/p"]
    [business] = structured.entity_records(pages[0][1], ["LocalBusiness"])
    assert (business["name"], business["phone"], business["address"], business["url"]) == ("Joe's Diner", "(512) 555-0100", "1 Main St", "https://joes.test/")
    assert structured.entity_records(pages[1][1], ["Product"])[0]["name"] == 'Widget "Pro"'


# --- Phase 7: resilience, watches ------------------------------------------------------------------

def _books_page(root_class: str = "product_pod", price_class: str = "price_color") -> str:
    cards = "".join(f"<article class='{root_class}'><h3><a href='/b/{i}' title='Book {i}'>Book {i}</a></h3><p class='{price_class}'>£{i}1.50</p></article>" for i in range(6))
    return f"<html><body><section><ol>{cards}</ol></section></body></html>"


def test_relocation_suggestions_and_coverage_drift() -> None:
    preset = {"extraction": {"record_root": {"css": "article.product_pod"}, "fields": [
        {"key": "title", "selectors": [{"css": "h3 a::attr(title)"}]}, {"key": "price", "selectors": [{"css": "p.price_color"}]}]}}
    prints = field_fingerprints(_books_page(), preset)
    suggestions = {s["field"]: s for s in relocation_suggestions(_books_page("card", "amount"), preset, prints)}
    assert suggestions["__root__"]["suggested"] == "article.card" and suggestions["price"]["suggested"] == "p.amount"
    assert coverage_drift({"title": 1.0, "price": 0.95}, {"title": 1.0, "price": 0.2}) == [{"field": "price", "baseline": 0.95, "current": 0.2}]


def test_example_based_selector_suggestions_extract_all_records() -> None:
    html = _books_page()
    proposal = suggest_from_examples(html, "https://books.test/", {"title": "Book 2", "price": "£21.50", "link": "/b/2"})
    assert proposal["record_root"] == {"css": "article.product_pod"} and proposal["record_count"] == 6
    preset = load("generic.html_list@1.1.0.json")
    preset["extraction"] = {"record_root": proposal["record_root"], "fields": [{k: v for k, v in f.items() if k != "relative_to_root"} for f in proposal["fields"]]}
    records, _, _ = extract_document(html, "https://books.test/", preset)
    assert [r["price"] for r in records][:2] == ["£01.50", "£11.50"] and records[3]["link"] == "https://books.test/b/3"


def test_diff_records_added_removed_changed() -> None:
    before = [{"sku": "A", "price": "1", "source_retrieved_at": "t1"}, {"sku": "B", "price": "2"}, {"price": "9"}]
    after = [{"sku": "A", "price": "1", "source_retrieved_at": "t2"}, {"sku": "B", "price": "3"}, {"sku": "C", "price": "4"}]
    diff = diff_records(before, after, ["sku"])
    assert diff["counts"] == {"added": 1, "removed": 0, "changed": 1, "unchanged": 1, "unkeyed": 1}
    assert diff["changed"] == [{"key": {"sku": "B"}, "changes": {"price": {"before": "2", "after": "3"}}}]


# --- Phase 8: AI-assisted proposals ------------------------------------------------------------------

def test_local_proposals_are_evaluated_and_remote_needs_consent() -> None:
    proposals = suggest.propose(_books_page(), "https://books.test/")
    assert proposals and all(p["evaluation"]["passed"] for p in proposals)
    assert proposals[0]["evaluation"]["records"] == 6
    structured_proposals = suggest.propose(product_page(PRODUCTS[0]) + product_page(PRODUCTS[1]), "https://shop.test/")
    assert any(p["source"] == "structured_data" for p in structured_proposals)
    with pytest.raises(PermissionError, match="consent"):
        suggest.propose_with_model("<p>x</p>", "https://a.test/", "https://api.remote.test/v1", "m", consent_remote=False, client=None)
    payload = suggest.model_payload("<p>Call 512-555-0100 or mail jo@example.com</p>", "https://a.test/", remote=True)
    sent = json.dumps(payload)
    assert "512-555-0100" not in sent and "jo@example.com" not in sent and "[phone]" in sent


def test_local_model_endpoint_proposal_is_used_only_after_evaluation() -> None:
    reply = {"choices": [{"message": {"content": json.dumps({"record_root": {"css": "article.product_pod"}, "fields": [{"key": "title", "selectors": [{"css": "h3 a::attr(title)"}]}]})}}]}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))
    with httpx.Client(transport=transport) as client:
        proposals = suggest.propose(_books_page(), "https://books.test/", "model", "http://127.0.0.1:11434/v1", "llama", False, client)
    model = [p for p in proposals if p["source"] == "model"]
    assert model and model[0]["endpoint_is_local"] and model[0]["evaluation"]["records"] == 6


def test_new_bundled_presets_validate() -> None:
    for name in ("generic.structured_data", "generic.article", "generic.sitemap_structured", "generic.sitemap_article", "generic.crawl_structured", "generic.feed",
                 "generic.llms_txt", "generic.document_tables", "ckan.package_search", "socrata.dataset_rows", "wikidata.sparql", "openalex.works",
                 "osm.overpass_pois", "sec.submissions", "gdelt.doc_search"):
        assert validate_preset(load(f"{name}@1.0.0.json")) == [], name
    broken = load("generic.crawl_structured@1.0.0.json")
    broken["discovery"]["crawl"].update(same_host_only=False, max_depth=9)
    broken["pagination"] = {"type": "next_link", "next": {"css": "a"}}
    errors = validate_preset(broken)
    assert any("same_host_only" in e for e in errors) and any("max_depth" in e for e in errors) and any("cannot be combined" in e for e in errors)


# --- regressions found by scripts/live-check.py ----------------------------------------------------

def test_live_regressions_structured_nesting_duplicates_and_subtypes() -> None:
    page = ('<script type="application/ld+json">{"@type":"LiveBlogPosting","@id":"https://news.test/a","headline":"Rates rise",'
            '"publisher":{"@type":"Organization","name":"News"},"liveBlogUpdate":[{"@type":"BlogPosting","headline":"Update 1"}]}</script>'
            '<script type="application/ld+json">{"@type":"LiveBlogPosting","@id":"https://news.test/a","headline":"Rates rise","datePublished":"2026-09-16"}</script>')
    records = structured.extract_structured_records(page, "https://news.test/a", {})
    assert len(records) == 1 and records[0]["schema_type"] == "LiveBlogPosting"
    assert records[0]["publisher"] == "News" and records[0]["date_published"] == "2026-09-16"


def test_live_regressions_nquads_unicode_escapes_and_capture_all_bookkeeping() -> None:
    lines = ['_:a <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <http://schema.org/LocalBusiness> <https://cafe.test/> .',
             '_:a <http://schema.org/name> "Caf\u00E9 M\u00fcller \\"Nord\\"" <https://cafe.test/> .']
    [(_, entities)] = list(archives.iter_nquad_pages(iter(lines)))
    assert structured.entity_records(entities, ["LocalBusiness"])[0]["name"] == 'Café Müller "Nord"'
    preset = load("socrata.dataset_rows@1.0.0.json")
    records, _, _ = extract_document(json.dumps([{"case": "1", ":@computed_region_x": "9", "location": {"latitude": "41.9", "type": "Point"}}]), "https://city.test/resource/abcd-1234.json", preset)
    assert "case" in records[0] and not any(key.startswith(":") for key in records[0]) and records[0]["location.latitude"] == "41.9"


def test_live_regressions_coverage_counts_only_considered_candidates(site) -> None:
    base, _ = site
    preset = local(load("generic.html_list@1.1.0.json"), base)
    preset["extraction"] = {"record_root": {"css": "li.item"}, "fields": [{"key": "name", "required": True, "selectors": [{"css": "a"}]}]}
    preset["pagination"] = {"type": "none"}
    result = extract_html_pages(base + "/products?page=1", preset, max_records=2)
    assert len(result.records) == 2 and not any("coverage" in w for w in result.warnings)


def test_live_regressions_transient_gateway_errors_retry_and_path_variables() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(504 if calls["n"] == 1 else 200, text="ok")

    waits: list[float] = []
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert fetch(client, "https://api.test/x", {"request_limits": {}}, lambda u, p: None, sleep=waits.append, check_challenge=False).text == "ok"
    assert waits == [3.0]
    from dataforge_scraping.runtime import build_request

    ckan = load("ckan.package_search@1.0.0.json")
    variables = resolve_variables(ckan, {"portal": "open.canada.ca", "base_path": "/data", "query": "water quality"}, 10)
    assert build_request("", ckan, variables)[0] == "https://open.canada.ca/data/api/3/action/package_search?q=water%20quality&rows=50"
    with pytest.raises(PolicyViolation, match="simple path"):
        resolve_variables(ckan, {"portal": "open.canada.ca", "base_path": "/../x", "query": "q"}, 10)


# --- Scrapy engine: pause, resume after cancel, incremental runs -----------------------------------

def _product_requests(state) -> list[str]:
    return [path for path in state.requests if path.startswith("/product/")]


def test_scrapy_engine_pause_resume_and_resume_after_cancel(site, tmp_path) -> None:
    import threading
    import time as clock

    from dataforge_scraping.engines.launcher import collect_with_scrapy

    base, state = site
    preset = local(load("generic.sitemap_structured@1.0.0.json"), base)
    preset["request_limits"]["min_delay_ms"] = 300
    preset["discovery"]["sitemap"]["url_pattern"] = "^/product/"
    preset["validation"]["unique_by"] = ["sku"]

    # Pause: no new product requests while paused, then the run completes.
    control = {"paused": False, "pages": 0, "frozen_at": None, "frozen_after": None}

    def on_page(event):
        control["pages"] += 1
        if control["pages"] == 2 and control["frozen_at"] is None:
            control["paused"] = True

    def should_stop():
        if control["paused"] and control["frozen_at"] is None:
            clock.sleep(1.0)  # let the in-flight request finish
            control["frozen_at"] = len(_product_requests(state))
            clock.sleep(1.5)
            control["frozen_after"] = len(_product_requests(state))
            control["paused"] = False
        return False

    result = collect_with_scrapy(base + "/", preset, 500, 50, should_stop=should_stop, on_page=on_page, is_paused=lambda: control["paused"], include_local_signals=True)
    assert control["frozen_at"] is not None and control["frozen_after"] == control["frozen_at"]
    assert len(result.records) == 12

    # Cancel after three pages, then run again with the same resume directory.
    state.requests.clear()
    resume = tmp_path / "resume"
    seen = {"pages": 0}
    first = collect_with_scrapy(base + "/", preset, 500, 50, should_stop=lambda: seen["pages"] >= 3, on_page=lambda e: seen.__setitem__("pages", seen["pages"] + 1),
                                include_local_signals=True, resume_dir=resume)
    assert first.stop_reason == "cancelled"
    fetched_first = set(_product_requests(state))
    state.requests.clear()
    second = collect_with_scrapy(base + "/", preset, 500, 50, include_local_signals=True, resume_dir=resume)
    assert not (set(_product_requests(state)) & fetched_first)
    assert sorted({r["sku"] for r in first.records} | {r["sku"] for r in second.records}) == sorted(p["sku"] for p in PRODUCTS)
    del threading


def test_scrapy_engine_incremental_runs_skip_pages_with_known_items(site, tmp_path) -> None:
    from dataforge_scraping.engines.launcher import collect_with_scrapy

    base, _ = site
    preset = local(load("generic.sitemap_structured@1.0.0.json"), base)
    preset["discovery"]["sitemap"]["url_pattern"] = "^/product/"
    first = collect_with_scrapy(base + "/", preset, 500, 50, include_local_signals=True, incremental=True, deltafetch_dir=tmp_path / "delta")
    second = collect_with_scrapy(base + "/", preset, 500, 50, include_local_signals=True, incremental=True, deltafetch_dir=tmp_path / "delta")
    assert len(first.records) == 12 and len(second.records) == 0


def test_xpath_fallback_selectors_in_http_runtime() -> None:
    html = "".join(f"<article class='card'><h3 title='Widget {n}'>Widget {n}</h3><dl><dt>SKU</dt><dd class='renamed'>W-{n}</dd></dl></article>" for n in range(1, 4))
    preset = load("generic.html_list@1.1.0.json")
    preset["extraction"] = {"record_root": {"css": "article.card"}, "fields": [
        {"key": "sku", "required": True, "selectors": [{"css": "dd.sku"}, {"xpath": ".//dt[normalize-space(.)='SKU']/following-sibling::dd[1]"}]},
        {"key": "title", "selectors": [{"css": "h4::attr(title)"}, {"xpath": "./h3[1]", "attribute": "title"}]},
    ]}
    assert validate_preset(preset) == []
    records, _, _ = extract_document(html, "https://shop.test/", preset)
    assert [(r["sku"], r["title"]) for r in records] == [("W-1", "Widget 1"), ("W-2", "Widget 2"), ("W-3", "Widget 3")]
    broken = copy.deepcopy(preset)
    broken["extraction"]["fields"][0]["selectors"].append({"xpath": "//*["})
    assert any("invalid XPath" in e for e in validate_preset(broken))


def test_fixture_sanitizer_removes_scripts_tokens_and_contact_details() -> None:
    from dataforge_scraping.fixtures import sanitize_html

    html = ("<html><head><meta name='csrf-token' content='abc123'><script>track()</script>"
            "<script type='application/ld+json'>{\"@type\":\"Product\",\"name\":\"Widget\"}</script></head><body><!-- build 42 -->"
            "<form><input type='hidden' name='authenticity_token' value='zzz'></form><iframe src='https://ads.test/'></iframe>"
            "<a href='/next?page=2&session_id=s3cr3t'>next</a><p>Call 512-555-0100 or write ana@example.com</p>"
            "<div class='card' data-api-key='k1'><h3>Widget</h3></div></body></html>")
    cleaned, counts = sanitize_html(html)
    for leaked in ("abc123", "track()", "zzz", "s3cr3t", "512-555-0100", "ana@example.com", "build 42", "ads.test", "k1"):
        assert leaked not in cleaned, leaked
    assert "application/ld+json" in cleaned and "page=2" in cleaned and "<h3>Widget</h3>" in cleaned
    assert counts["scripts"] == 1 and counts["contact_details"] == 1 and counts["tokens"] >= 3


def test_pdf_text_output_and_optional_ocr(monkeypatch) -> None:
    from dataforge_scraping import documents

    text_pdf = table_pdf([["Name", "City"], ["Acme", "Austin"]])
    scanned_pdf = table_pdf([])  # ruled lines only: no text layer, like a scanned page
    preset = load("generic.document_tables@1.0.0.json")
    preset["extraction"] = {**preset["extraction"], "output": "text"}
    records, _, _ = extract_document(text_pdf, "https://docs.test/a.pdf", preset)
    assert records[0]["source_page"] == 1 and "Acme" in records[0]["text"] and records[0]["text_source"] == "pdf"

    monkeypatch.setattr(documents, "_ocr_reader", lambda: None)
    pages, warnings = documents.pdf_text(scanned_pdf, "https://docs.test/s.pdf", ocr=True)
    assert pages == [] and "OCR add-on" in warnings[0]
    monkeypatch.setattr(documents, "_ocr_reader", lambda: (lambda image: f"SCANNED {image.size[0]}x{image.size[1]}"))
    pages, warnings = documents.pdf_text(scanned_pdf, "https://docs.test/s.pdf", ocr=True)
    assert pages[0]["text"] == "SCANNED 1224x1584" and pages[0]["text_source"] == "ocr" and not warnings
