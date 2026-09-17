"""Manual live verification of every collection path against real, permitted public sources.

Not part of CI (automated tests never touch live sites). Runs through the same command API as the desktop
app, in a throwaway project, with small caps and polite delays. Prints counts, stop reasons, and signal
decisions only; it never prints contact details from collected records.

    python scripts/live-check.py [--only name,name] [--project C:\\temp\\live-project]
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "services/application/src"), str(ROOT / "workers/matching/src"), str(ROOT / "workers/scraping/src")]

from dataforge_application.api import Service  # noqa: E402

RESULTS: list[dict] = []


def call(service: Service, command: str, **payload):
    return service.handle({"schema_version": 1, "command": command, "payload": payload})


def ok(service: Service, command: str, **payload):
    response = call(service, command, **payload)
    if not response["ok"]:
        raise RuntimeError(f"{command}: {response['error']['message']}")
    return response["result"]


def wait(service: Service, job_id: str, timeout: float = 900) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = ok(service, "job.get", job_id=job_id)
        if job["state"] in ("completed", "failed", "cancelled"):
            return job
        time.sleep(0.5)
    raise TimeoutError(job_id)


def job_summary(job: dict, show: tuple[str, ...] = ()) -> dict:
    result = job.get("result") or {}
    samples = result.get("sample_records") or []
    summary = {
        "state": job["state"], "error": job.get("error"), "records": result.get("records_extracted"), "pages": result.get("pages_fetched", result.get("captures_read")),
        "stop": result.get("stop_reason"), "engine": result.get("engine"), "cached": result.get("cached_responses"),
        "fields": sorted({k for r in samples for k in r if not k.startswith(("source_", "preset_", "strategy_"))})[:14],
        "warnings": (result.get("warnings") or [])[:3],
        "signals": [{k: s.get(k) for k in ("host", "robots_status", "crawl_delay", "tdm_reservation", "ai_txt")} for s in result.get("signals") or []],
    }
    if show:
        summary["examples"] = [{k: r.get(k) for k in show} for r in samples[:3]]
    return summary


def scrape(service: Service, **params) -> dict:
    params = {"policy_acknowledgement": True, "purpose": "internal_analysis", "run_mode": "test", **params}
    return wait(service, ok(service, "scrape.create_job", **params)["job_id"])


def custom(service: Service, base_id: str, base_version: str, new_id: str, **changes) -> None:
    base = next(p for p in ok(service, "preset.list") if p["id"] == base_id and p["version"] == base_version)
    preset = {k: v for k, v in base.items() if k not in ("source", "package", "errors", "health_status", "declared_status")}
    preset.update(id=new_id, version="1.0.0", **changes)
    ok(service, "preset.save_custom", preset=preset)


BOOKS_EXTRACTION = {"record_root": {"css": "article.product_pod"}, "fields": [
    {"key": "title", "required": True, "selectors": [{"css": "h3 a::attr(title)"}], "transforms": ["trim"]},
    {"key": "price", "type": "decimal", "selectors": [{"css": "p.price_color"}], "transforms": ["parse_price"]},
    {"key": "link", "type": "url", "selectors": [{"css": "h3 a::attr(href)"}], "transforms": ["to_absolute_url"]}]}


def check(name: str):
    def decorator(function):
        CHECKS[name] = function
        return function
    return decorator


CHECKS: dict = {}


@check("signals")
def signals(service):
    return {url + " [" + purpose + "]": {k: v for k, v in ok(service, "scrape.check_signals", url=url, purpose=purpose).items() if k in ("allowed", "reason")}
            for url, purpose in [("https://books.toscrape.com/", "internal_analysis"), ("https://www.theguardian.com/international", "ai_training"),
                                 ("https://en.wikipedia.org/wiki/Web_scraping", "research"), ("https://overpass-api.de/api/interpreter", "research")]}


def ensure_books(service) -> dict | None:
    """The books preset (and its required successful test run) is shared by several checks."""
    if any(p["id"] == "custom.live.books" for p in ok(service, "preset.list")):
        return None
    custom(service, "generic.html_list", "1.1.0", "custom.live.books", extraction=BOOKS_EXTRACTION,
           pagination={"type": "next_link", "next": {"css": "li.next a::attr(href)"}}, validation={"minimum_record_coverage": 0.7, "unique_by": ["link"]},
           request_limits={"max_concurrency": 1, "min_delay_ms": 1000, "max_pages_default": 3, "max_records_default": 100, "max_duration_seconds": 300})
    return scrape(service, preset_id="custom.live.books", preset_version="1.0.0", start_url="https://books.toscrape.com/catalogue/page-1.html")


@check("selectors_next_link")
def selectors_next_link(service):
    test = ensure_books(service) or scrape(service, preset_id="custom.live.books", preset_version="1.0.0", start_url="https://books.toscrape.com/catalogue/page-1.html")
    full = scrape(service, preset_id="custom.live.books", preset_version="1.0.0", start_url="https://books.toscrape.com/catalogue/page-1.html", run_mode="full", engine="httpx")
    again = scrape(service, preset_id="custom.live.books", preset_version="1.0.0", start_url="https://books.toscrape.com/catalogue/page-1.html")
    return {"test": job_summary(test, ("title", "price")), "full": job_summary(full), "repeat_test_cache": job_summary(again)["cached"]}


@check("suggestions")
def suggestions(service):
    books_preset = next(p for p in ok(service, "preset.list") if p["id"] == "generic.html_list" and p["version"] == "1.1.0")
    books_preset = {**books_preset, "url_scope": {"allowed_hosts": ["books.toscrape.com"], "allowed_path_patterns": []}}
    url = "https://books.toscrape.com/catalogue/page-1.html"
    suggestion = ok(service, "scrape.suggest_selectors", url=url, preset=books_preset, examples={"title": "A Light in the Attic", "price": "£51.77"})
    proposals = ok(service, "scrape.propose_presets", url=url, preset=books_preset)
    return {"example_based": {"record_root": suggestion["record_root"], "records": suggestion["record_count"], "fields": [(f["key"], f["selectors"][0]["css"]) for f in suggestion["fields"]], "notes": suggestion["notes"]},
            "proposals": [{"source": p["source"], "records": p["evaluation"]["records"], "coverage": p["evaluation"]["field_coverage"]} for p in proposals["proposals"]]}


@check("crawl_engines")
def crawl_engines(service):
    detail = {"record_root": {"css": "article.product_page"}, "fields": [
        {"key": "title", "required": True, "selectors": [{"css": "h1"}]}, {"key": "price", "type": "decimal", "selectors": [{"css": "p.price_color"}], "transforms": ["parse_price"]},
        {"key": "upc", "required": True, "selectors": [{"css": "table.table-striped tr:nth-of-type(1) td"}]}]}
    custom(service, "generic.crawl_structured", "1.0.0", "custom.live.bookcrawl", extraction=detail, validation={"minimum_record_coverage": 0.7, "unique_by": ["upc"]},
           discovery={"mode": "crawl", "crawl": {"max_depth": 1, "same_host_only": True, "link_pattern": r"^/catalogue/[^/]+_\d+/index\.html$", "extract_pattern": r"^/catalogue/[^/]+_\d+/index\.html$"}},
           request_limits={"max_concurrency": 1, "min_delay_ms": 1000, "max_pages_default": 12, "max_records_default": 50, "max_duration_seconds": 600})
    start = "https://books.toscrape.com/catalogue/category/books/travel_2/index.html"
    test = scrape(service, preset_id="custom.live.bookcrawl", preset_version="1.0.0", start_url=start)
    runs = {}
    for engine in ("httpx", "scrapy"):
        job = scrape(service, preset_id="custom.live.bookcrawl", preset_version="1.0.0", start_url=start, run_mode="full", engine=engine)
        rows = ok(service, "dataset.rows", dataset_id=job["result"]["dataset_id"], limit=100) if job["state"] == "completed" and job["result"].get("dataset_id") else []
        runs[engine] = (job_summary(job), sorted(r["raw"]["upc"] for r in rows))
    return {"test": job_summary(test, ("title", "price")), "httpx": runs["httpx"][0], "scrapy": runs["scrapy"][0], "identical_records": runs["httpx"][1] == runs["scrapy"][1] and bool(runs["httpx"][1])}


@check("feed_and_article")
def feed_and_article(service):
    feed = scrape(service, preset_id="generic.feed", preset_version="1.0.0", start_url="https://blog.python.org/feeds/posts/default")
    link = (feed.get("result") or {}).get("sample_records", [{}])[0].get("link")
    article = scrape(service, preset_id="generic.article", preset_version="1.0.0", start_url=link) if link else {"state": "skipped"}
    return {"feed": job_summary(feed, ("title", "published")), "article": job_summary(article, ("title", "date_published", "word_count")) if link else article}


@check("llms_txt_and_sitemap")
def llms_txt_and_sitemap(service):
    llms = scrape(service, preset_id="generic.llms_txt", preset_version="1.0.0", start_url="https://llmstxt.org/", max_pages=3)
    sitemap = scrape(service, preset_id="generic.sitemap_article", preset_version="1.0.0", start_url="https://llmstxt.org/", max_pages=3)
    return {"llms_txt": job_summary(llms, ("title", "format")), "sitemap_article": job_summary(sitemap, ("title", "word_count"))}


@check("structured_news_sitemap")
def structured_news_sitemap(service):
    job = scrape(service, preset_id="generic.sitemap_structured", preset_version="1.0.0", start_url="https://www.theguardian.com/sitemaps/news.xml", max_pages=3)
    return job_summary(job, ("schema_type", "title", "date_published", "structured_syntax"))


@check("documents")
def documents(service):
    job = scrape(service, preset_id="generic.document_tables", preset_version="1.0.0", start_url="https://raw.githubusercontent.com/jsvine/pdfplumber/stable/examples/pdfs/ca-warn-report.pdf")
    summary = job_summary(job)
    summary["provenance_example"] = [{k: r.get(k) for k in ("source_page", "source_table", "source_row")} for r in (job.get("result") or {}).get("sample_records", [])[:2]]
    return summary


@check("open_data_apis")
def open_data_apis(service):
    out = {}
    out["ckan_data_gov"] = job_summary(scrape(service, preset_id="ckan.package_search", preset_version="1.0.0", purpose="research", variables={"portal": "open.canada.ca", "base_path": "/data", "query": "water quality"}), ("title", "organization"))
    out["socrata_chicago"] = job_summary(scrape(service, preset_id="socrata.dataset_rows", preset_version="1.0.0", purpose="research", variables={"domain": "data.cityofchicago.org", "dataset": "ijzp-q8t2"}))
    out["openalex"] = job_summary(scrape(service, preset_id="openalex.works", preset_version="1.0.0", purpose="research", variables={"query": "entity resolution"}), ("title", "year"))
    out["gdelt"] = job_summary(scrape(service, preset_id="gdelt.doc_search", preset_version="1.0.0", purpose="research", variables={"query": "flood", "limit": 10}), ("title", "domain"))
    out["overpass_kumi"] = job_summary(scrape(service, preset_id="osm.overpass_pois", preset_version="1.0.0", purpose="research",
                                              variables={"amenity": "library", "south": 30.25, "west": -97.76, "north": 30.29, "east": -97.72, "limit": 10}))
    out["overpass_default_server"] = job_summary(scrape(service, preset_id="osm.overpass_pois", preset_version="1.0.0", purpose="research",
                                                        variables={"endpoint": "overpass-api.de", "amenity": "library", "south": 30.25, "west": -97.76, "north": 30.29, "east": -97.72, "limit": 10}))
    for preset_id, variables in (("sec.submissions", {"cik": "0000320193"}), ("wikidata.sparql", {"query": "SELECT ?item WHERE { ?item wdt:P31 wd:Q515 }"})):
        response = call(service, "scrape.create_job", preset_id=preset_id, preset_version="1.0.0", policy_acknowledgement=True, purpose="research", run_mode="test", variables=variables)
        out[preset_id + "_without_contact"] = response.get("error", {}).get("message") or "unexpectedly accepted"
    return out


@check("archives")
def archives(service):
    ensure_books(service)
    wayback = wait(service, ok(service, "archive.create_job", archive="wayback", url_pattern="books.toscrape.com/catalogue/page-2.html", preset_id="custom.live.books", preset_version="1.0.0",
                               policy_acknowledgement=True, purpose="research", run_mode="test", max_captures=2)["job_id"])
    common = wait(service, ok(service, "archive.create_job", archive="common_crawl", url_pattern="books.toscrape.com/catalogue/page-2.html", preset_id="custom.live.books", preset_version="1.0.0",
                              policy_acknowledgement=True, purpose="research", run_mode="test", max_captures=1)["job_id"])
    summary = job_summary(wayback, ("title", "archive_capture_time"))
    return {"wayback": summary, "common_crawl": job_summary(common)}


@check("archive_compare")
def archive_compare(service):
    ensure_books(service)
    job = wait(service, ok(service, "archive.create_job", archive="wayback", url_pattern="books.toscrape.com/catalogue/page-2.html", preset_id="custom.live.books", preset_version="1.0.0",
                           policy_acknowledgement=True, purpose="research", run_mode="test", max_captures=20, compare=True)["job_id"])
    summary = job_summary(job, ("title", "archive_capture_time"))
    summary["archive_diff_counts"] = ((job.get("result") or {}).get("archive_diff") or {}).get("counts")
    return summary


@check("watch_diff")
def watch_diff(service):
    quotes = {"record_root": {"css": "div.quote"}, "fields": [{"key": "text", "required": True, "selectors": [{"css": "span.text"}]}, {"key": "author", "selectors": [{"css": "small.author"}]},
                                                             {"key": "tags", "selectors": [{"css": "div.tags meta::attr(content)"}]}]}
    custom(service, "generic.html_list", "1.1.0", "custom.live.quotes", extraction=quotes, pagination={"type": "next_link", "next": {"css": "li.next a::attr(href)"}},
           validation={"minimum_record_coverage": 0.7, "unique_by": ["text"]},
           request_limits={"max_concurrency": 1, "min_delay_ms": 1000, "max_pages_default": 2, "max_records_default": 50, "max_duration_seconds": 300})
    scrape(service, preset_id="custom.live.quotes", preset_version="1.0.0", start_url="https://quotes.toscrape.com/")
    watch = ok(service, "watch.create", name="Quotes", interval_minutes=1440, params={"preset_id": "custom.live.quotes", "preset_version": "1.0.0",
               "start_url": "https://quotes.toscrape.com/", "policy_acknowledgement": True, "purpose": "internal_analysis", "engine": "httpx"})
    first = wait(service, ok(service, "watch.run_now", watch_id=watch["id"])["job_id"])
    second = wait(service, ok(service, "watch.run_now", watch_id=watch["id"])["job_id"])
    runs = ok(service, "watch.get", watch_id=watch["id"])["runs"]
    return {"first": job_summary(first), "second": job_summary(second), "diff_counts": runs[0]["diff"]["counts"] if runs and runs[0]["diff"] else None}


@check("bulk_wdc_sample")
def bulk_wdc_sample(service):
    from dataforge_scraping.fetch import make_client

    url = "https://data.dws.informatik.uni-mannheim.de/structureddata/2024-12/quads/classspecific/LocalBusiness/LocalBusiness_sample.txt"
    preset = {"url_scope": {"allowed_hosts": ["data.dws.informatik.uni-mannheim.de"]}, "policy": {}, "request_limits": {}}
    from dataforge_scraping.fetch import fetch
    from dataforge_scraping.signals import SignalChecker

    with make_client(None) as client:
        SignalChecker(client, "research").check_url(url)
        body = fetch(client, url, preset, lambda u, p: None, check_challenge=False).content
    path = Path(tempfile.mkdtemp()) / "LocalBusiness_sample.nq"
    path.write_bytes(body)
    job = wait(service, ok(service, "bulk.create_job", path=str(path), schema_types=["LocalBusiness"], run_mode="full", max_records=5000)["job_id"])
    result = job.get("result") or {}
    return {"downloaded_bytes": len(body), "state": job["state"], "error": job.get("error"), "pages_read": result.get("pages_read"), "records": result.get("records_extracted"),
            "fields": sorted({k for r in result.get("sample_records", []) for k in r})}


@check("maintenance")
def maintenance(service):
    from dataforge_application import sources
    from dataforge_scraping.fetch import make_client

    ensure_books(service)
    with make_client(None) as client:
        html = client.get("https://books.toscrape.com/catalogue/page-1.html").text
    preset = next(p for p in ok(service, "preset.list") if p["id"] == "custom.live.books")
    sources.save_fingerprints(service.store, preset, {"live-books.html": html})
    same = ok(service, "preset.maintenance_report", preset_id="custom.live.books", preset_version="1.0.0", html=html, url="https://books.toscrape.com/")
    redesigned = ok(service, "preset.maintenance_report", preset_id="custom.live.books", preset_version="1.0.0", html=html.replace("price_color", "price-now"), url="https://books.toscrape.com/")
    return {"unchanged_drift": same["drift"], "redesign_drift": redesigned["drift"], "redesign_suggestions": [(s["field"], s["suggested"], s["score"]) for s in redesigned["suggestions"]]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", default="")
    parser.add_argument("--project", default="")
    args = parser.parse_args()
    project = Path(args.project or tempfile.mkdtemp(prefix="dataforge-live-"))
    service = Service()
    ok(service, "project.create", path=str(project / "project"), name="Live check")
    ok(service, "settings.update", changes={"default_purpose": "internal_analysis"})
    selected = [name for name in CHECKS if not args.only or name in args.only.split(",")]
    for name in selected:
        started = time.monotonic()
        try:
            outcome = CHECKS[name](service)
            status = "ran"
        except Exception as error:  # noqa: BLE001 - report every check
            outcome, status = {"exception": f"{type(error).__name__}: {error}", "trace": traceback.format_exc()[-800:]}, "error"
        entry = {"check": name, "status": status, "seconds": round(time.monotonic() - started, 1), "outcome": outcome}
        RESULTS.append(entry)
        print(json.dumps(entry, ensure_ascii=False, default=str), flush=True)
    (project / "live-check.json").write_text(json.dumps(RESULTS, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("results:", project / "live-check.json")


if __name__ == "__main__":
    main()
