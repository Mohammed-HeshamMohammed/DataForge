"""Scrapy engine (child process). Run as:

    python -m dataforge_scraping.engines.scrapy_engine <job.json>
    dataforge-service --engine scrapy --job <job.json>        (packaged build)

One generic PresetSpider executes a pinned preset; users never supply spider code. DataForge policy is
enforced by settings and middlewares: scope, robots.txt/TDMRep/AIPREF signals (pre-checked by the parent
and re-applied per URL), stop codes and challenges, politeness, caps, and OS-trusted TLS verification.

Protocol: JSON lines on stdout ({"type": "item"|"page"|"warning"|"done", ...}). The parent controls the
run through a small control file containing "run", "pause", or "stop".
"""

from __future__ import annotations

import json
import os
import ssl
import sys
from pathlib import Path
from urllib.parse import urlparse

import scrapy
from scrapy import signals as scrapy_signals
from scrapy.core.downloader import contextfactory as _contextfactory
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Request, Response
from twisted.internet.task import LoopingCall

from ..discovery import Frontier, extract_links, parse_feed, parse_llms_txt, parse_sitemap
from ..errors import PolicyViolation
from ..extraction import _detail_urls, _next_html_url, _unique_fields, _validate_record, canonicalize_url, extract_page, validate_url
from ..fetch import ACCESS_STOP_CODES, CHALLENGE_MARKERS
from ..signals import SignalChecker

_EMIT = sys.stdout  # replaced by run() before stdout is redirected


def emit(kind: str, **data) -> None:
    _EMIT.write(json.dumps({"type": kind, **data}, default=str, ensure_ascii=False) + "\n")
    _EMIT.flush()


# --- TLS: verify certificates against the operating system trust store ----------------------------

_TRUST_ROOT = None


def os_trust_root():
    global _TRUST_ROOT
    if _TRUST_ROOT is None:
        from OpenSSL import crypto
        from twisted.internet.ssl import Certificate, trustRootFromCertificates

        context = ssl.create_default_context()
        context.load_default_certs()
        certificates = []
        for der in context.get_ca_certs(binary_form=True):
            try:
                certificates.append(Certificate(crypto.load_certificate(crypto.FILETYPE_ASN1, der)))
            except Exception:  # noqa: BLE001 - skip unusable roots
                continue
        _TRUST_ROOT = trustRootFromCertificates(certificates)
    return _TRUST_ROOT


class OSTrustContextFactory(getattr(_contextfactory, "_ScrapyClientContextFactory", None) or _contextfactory.ScrapyClientContextFactory):
    def creatorForNetloc(self, hostname: bytes, port: int):
        from twisted.internet.ssl import optionsForClientTLS

        return optionsForClientTLS(hostname.decode("ascii"), trustRoot=os_trust_root(), extraCertificateOptions=self._get_cert_options_kwargs())


# --- middlewares ----------------------------------------------------------------------------------

class PolicyMiddleware:
    """Downloader middleware mirroring the httpx runtime: scope, signals, stop codes, challenges."""

    @classmethod
    def from_crawler(cls, crawler):
        middleware = cls()
        middleware.crawler = crawler
        return middleware

    def _close(self, spider, reason: str) -> None:
        if not spider.stop_reason:
            spider.stop_reason = reason
            emit("stop", reason=reason)
            _close_spider(self.crawler, "policy_violation")

    def process_request(self, request: Request, spider=None):
        spider = spider or self.crawler.spider
        if request.meta.get("dataforge_robots"):
            return None
        parsed = urlparse(request.url)
        if parsed.path == "/robots.txt" and parsed.hostname in spider.preset["url_scope"].get("allowed_hosts", []):
            return None
        try:
            validate_url(request.url, spider.preset)
        except PolicyViolation as error:
            if request.meta.get("redirect_times"):
                self._close(spider, str(error))  # a redirect out of scope stops, like the httpx engine
            else:
                emit("warning", message="skipped a link outside the preset scope")
            raise IgnoreRequest(str(error))
        try:
            spider.checker.check_url(request.url)
        except PolicyViolation as error:
            if "robots.txt disallows" in str(error) and request.meta.get("skippable"):
                emit("warning", message=f"skipped {parsed.path}: robots.txt disallows it")
            else:
                self._close(spider, str(error))
            raise IgnoreRequest(str(error))
        return None

    def process_response(self, request: Request, response: Response, spider=None):
        spider = spider or self.crawler.spider
        if response.status in ACCESS_STOP_CODES:
            self._close(spider, f"Collection stopped on access or rate-limit response: {response.status}")
            raise IgnoreRequest("stop code")
        content_type = response.headers.get(b"Content-Type", b"").decode("latin-1")
        text = response.text[:200_000] if hasattr(response, "text") and ("html" in content_type or not content_type) else None
        if text is not None and any(marker in text.lower() for marker in CHALLENGE_MARKERS):
            self._close(spider, "Collection stopped: the page presented an access challenge (CAPTCHA/bot check)")
            raise IgnoreRequest("challenge")
        try:
            spider.checker.check_response(request.url, _HeaderAdapter(response), text)
        except PolicyViolation as error:
            self._close(spider, str(error))
            raise IgnoreRequest(str(error))
        return response


class _HeaderAdapter:
    """Presents Scrapy headers with the httpx `response.headers.get(name)` shape used by SignalChecker."""

    def __init__(self, response: Response) -> None:
        self.headers = {k.decode("latin-1").lower(): v[0].decode("latin-1") for k, v in response.headers.items() if v}


class DeltaFetchMiddleware:
    """scrapy-deltafetch's rules (skip requests whose pages produced items before) with the async spider-output
    interface Scrapy 2.13+ requires. The storage and keys are the upstream middleware's."""

    @classmethod
    def from_crawler(cls, crawler):
        from scrapy_deltafetch.middleware import DeltaFetch

        middleware = cls()
        middleware.inner = DeltaFetch.from_crawler(crawler)
        return middleware

    def _process(self, response, output, spider=None):
        inner = self.inner
        if isinstance(output, Request):
            if inner._get_key(output) in inner.db and inner._is_enabled_for_request(output):
                inner.stats.inc_value("deltafetch/skipped")
                return None
        else:
            inner.db[inner._get_key(response.request)] = str(__import__("time").time())
            inner.stats.inc_value("deltafetch/stored")
        return output

    def process_spider_output(self, response, result, spider=None):
        for output in result:
            if (kept := self._process(response, output, spider)) is not None:
                yield kept

    async def process_spider_output_async(self, response, result, spider=None):
        async for output in result:
            if (kept := self._process(response, output, spider)) is not None:
                yield kept


class EmitPipeline:
    def process_item(self, item, spider=None):
        record = {k: v for k, v in dict(item).items() if k != "_validation"}
        emit("item", record=record)
        return item


# --- the generic spider ---------------------------------------------------------------------------

class PresetSpider(scrapy.Spider):
    name = "dataforge_preset"

    def __init__(self, job: dict, **kwargs) -> None:
        super().__init__(**kwargs)
        self.job = job
        self.preset = job["preset"]
        self.mode = (self.preset.get("discovery") or {}).get("mode", "none")
        self.record_limit = int(job["max_records"])
        self.page_limit = int(job["max_pages"])
        self.checker = SignalChecker.from_export(job["signals"], job["purpose"], self.preset["policy"].get("robots_policy", "respect"))
        self.records = 0
        self.pages = 0
        self.discovered = 0
        self.stop_reason: str | None = None
        self.seen_keys: set[tuple] = set()
        self.unique_by = _unique_fields(self.preset)
        self.fields = (self.preset.get("extraction") or {}).get("fields", []) or []
        self.visited: set[str] = set()
        crawl = (self.preset.get("discovery") or {}).get("crawl") or {}
        self.frontier = Frontier(job["start_url"], int(crawl.get("max_depth", 2)), True, crawl.get("link_pattern"), crawl.get("exclude_pattern"))
        self.warnings: list[str] = []

    @classmethod
    def from_crawler(cls, crawler, *args, **kwargs):
        spider = super().from_crawler(crawler, *args, **kwargs)
        crawler.signals.connect(spider.on_closed, signal=scrapy_signals.spider_closed)
        crawler.signals.connect(spider.on_opened, signal=scrapy_signals.spider_opened)
        return spider

    def on_opened(self, spider=None) -> None:
        self._control = LoopingCall(self._poll_control)
        self._control.start(0.3, now=False)

    def _poll_control(self) -> None:
        try:
            command = Path(self.job["control_file"]).read_text(encoding="utf-8").strip()
        except OSError:
            return
        engine = self.crawler.engine
        if command == "stop" and not self.stop_reason:
            self.stop_reason = "cancelled"
            _close_spider(self.crawler, "cancelled")
        elif command == "pause" and not engine.paused:
            engine.pause()
            emit("paused")
        elif command == "run" and engine.paused:
            engine.unpause()
            emit("resumed")

    def on_closed(self, spider=None, reason: str = "") -> None:
        if getattr(self, "_control", None) is not None and self._control.running:
            self._control.stop()
        stats = self.crawler.stats.get_stats()
        validation_errors = sum(v for k, v in stats.items() if k.startswith("spidermon/validation/fields/errors") and isinstance(v, int))
        stats_errors = sum(v for k, v in stats.items() if k.startswith("spidermon/validation/items/errors") and isinstance(v, int))
        validation_errors = max(validation_errors, stats_errors)
        emit("done", reason=self.stop_reason or _reason(reason), pages=self.pages, records=self.records, discovered=self.discovered,
             cached=int(stats.get("httpcache/hit", 0)), validation_errors=validation_errors, warnings=self.warnings)

    def budget(self) -> str | None:
        if self.records >= self.record_limit:
            return "max_records"
        if self.pages >= self.page_limit:
            return "max_pages"
        return None

    def _stop_if_spent(self) -> bool:
        reason = self.budget()
        if reason and not self.stop_reason:
            self.stop_reason = reason
            _close_spider(self.crawler, "finished")
        return bool(reason)

    async def start(self):
        url = self.job["start_url"]
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        discovery = self.preset.get("discovery") or {}
        if self.mode == "sitemap":
            config = discovery.get("sitemap") or {}
            if config.get("urls"):
                sitemaps = [origin + u if u.startswith("/") else u for u in config["urls"]]
            elif urlparse(url).path.endswith((".xml", ".xml.gz", ".txt")):
                sitemaps = [url]
            else:
                sitemaps = self.job["signals"].get(origin, {}).get("signals", {}).get("sitemaps") or [origin + "/sitemap.xml", origin + "/sitemap_index.xml"]
            self.sitemap_budget = int(config.get("max_urls", self.page_limit))
            self.sitemaps_seen = 0
            for sitemap in sitemaps:
                yield Request(sitemap, callback=self.parse_sitemap, dont_filter=True, meta={"skippable": True})
        elif self.mode == "crawl":
            self.frontier.seed()
            item = self.frontier.next()
            yield Request(item[0], callback=self.parse_crawl, cb_kwargs={"depth": 0}, meta={"skippable": True})
        elif self.mode == "feed":
            yield Request(url, callback=self.parse_feed)
        elif self.mode == "llms_txt":
            yield Request(origin + "/llms.txt", callback=self.parse_llms)
        else:
            yield Request(url, callback=self.parse_listing, cb_kwargs={"page": 1})

    # --- extraction shared with the httpx engine ---
    def accept(self, response: Response, url: str) -> tuple[int, int]:
        content_type = response.headers.get(b"Content-Type", b"").decode("latin-1")
        body = response.text if hasattr(response, "text") and "pdf" not in content_type else ""
        warnings: list[str] = []
        candidates = extract_page(body, response.body, content_type, url, self.preset, warnings)
        for message in warnings:
            emit("warning", message=message)
        added = 0
        for candidate in candidates:
            if self.records >= self.record_limit:
                break
            if _validate_record(candidate, self.fields):
                continue
            key = tuple(candidate.get(f) for f in self.unique_by)
            if self.unique_by and all(v not in (None, "") for v in key):
                if key in self.seen_keys:
                    continue
                self.seen_keys.add(key)
            self.records += 1
            added += 1
            self._pending_items.append(candidate)
        return len(candidates), added

    _pending_items: list

    def _drain(self):
        items, self._pending_items = self._pending_items, []
        yield from items

    def parse_listing(self, response: Response, page: int):
        self._pending_items = []
        from bs4 import BeautifulSoup

        pagination = self.preset.get("pagination") or {}
        self.visited.add(canonicalize_url(response.url, self.preset))
        soup = BeautifulSoup(response.text, "html.parser")
        if pagination.get("type") == "detail_links":
            warnings: list[str] = []
            details = [u for u in _detail_urls(soup, response.url, pagination, self.preset, warnings) if canonicalize_url(u, self.preset) not in self.visited]
            for message in warnings:
                emit("warning", message=message)
            self.pages += 1
            emit("page", page=self.pages, url=canonicalize_url(response.url, self.preset))
            for detail in details[: max(0, self.record_limit - self.records)]:
                self.visited.add(canonicalize_url(detail, self.preset))
                yield Request(detail, callback=self.parse_detail, priority=10)
        else:
            candidates, added = self.accept(response, response.url)
            self.pages += 1
            emit("page", page=self.pages, url=canonicalize_url(response.url, self.preset), candidates=candidates, new_records=added)
            yield from self._drain()
            if added == 0 and page > 1:
                self.stop_reason = self.stop_reason or "no_new_records"
                return
        if self._stop_if_spent():
            return
        next_url = _next_html_url(soup, response.url, pagination, self.pages)
        if next_url and canonicalize_url(next_url, self.preset) not in self.visited:
            yield Request(next_url, callback=self.parse_listing, cb_kwargs={"page": page + 1})

    def parse_detail(self, response: Response):
        self._pending_items = []
        if self.records >= self.record_limit:
            return
        content_type = response.headers.get(b"Content-Type", b"").decode("latin-1")
        warnings: list[str] = []
        candidates = extract_page(response.text, response.body, content_type, response.url, self.preset, warnings)[:1]
        before = self.records
        for candidate in candidates:
            if not _validate_record(candidate, self.fields):
                key = tuple(candidate.get(f) for f in self.unique_by)
                if not (self.unique_by and all(v not in (None, "") for v in key) and key in self.seen_keys):
                    self.seen_keys.add(key)
                    self.records += 1
                    self._pending_items.append(candidate)
        yield from self._drain()
        if self.records > before:
            self._stop_if_spent()

    def parse_sitemap(self, response: Response):
        import re

        self._pending_items = []
        config = (self.preset.get("discovery") or {}).get("sitemap") or {}
        kind, entries = parse_sitemap(response.body)
        self.sitemaps_seen += 1
        pattern = re.compile(config["url_pattern"]) if config.get("url_pattern") else None
        from ..discovery import _after

        for loc, lastmod in entries:
            if kind == "sitemapindex":
                if self.sitemaps_seen < int(config.get("max_sitemaps", 50)):
                    yield Request(loc, callback=self.parse_sitemap, meta={"skippable": True})
                continue
            if self.discovered >= self.sitemap_budget or not _after(lastmod, config.get("lastmod_after")):
                continue
            if pattern and not pattern.search(urlparse(loc).path):
                continue
            try:
                validate_url(loc, self.preset)
            except PolicyViolation:
                continue
            self.discovered += 1
            yield Request(loc, callback=self.parse_content, meta={"skippable": True})

    def parse_content(self, response: Response):
        self._pending_items = []
        if self._stop_if_spent():
            return
        candidates, added = self.accept(response, response.url)
        self.pages += 1
        emit("page", page=self.pages, url=canonicalize_url(response.url, self.preset), candidates=candidates, new_records=added)
        yield from self._drain()
        self._stop_if_spent()

    def parse_crawl(self, response: Response, depth: int):
        import re

        self._pending_items = []
        if self._stop_if_spent():
            return
        crawl = (self.preset.get("discovery") or {}).get("crawl") or {}
        extract_pattern = re.compile(crawl["extract_pattern"]) if crawl.get("extract_pattern") else None
        parsed = urlparse(response.url)
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        self.pages += 1
        candidates = added = 0
        if extract_pattern is None or extract_pattern.search(path):
            candidates, added = self.accept(response, response.url)
        yield from self._drain()
        content_type = response.headers.get(b"Content-Type", b"").decode("latin-1")
        if "html" in content_type and depth < self.frontier.max_depth:
            for link in extract_links(response.text, response.url):
                try:
                    validate_url(link, self.preset)
                except PolicyViolation:
                    continue
                if self.frontier.add(link, depth + 1):
                    self.discovered += 1
                    yield Request(self.frontier.normalize(link), callback=self.parse_crawl, cb_kwargs={"depth": depth + 1}, meta={"skippable": True}, priority=-(depth + 1))
        emit("page", page=self.pages, url=canonicalize_url(response.url, self.preset), depth=depth, candidates=candidates, new_records=added)
        self._stop_if_spent()

    def parse_feed(self, response: Response):
        self._pending_items = []
        from ..runtime import _feed_record

        entries = parse_feed(response.body, response.url)
        self.discovered = len(entries)
        self.pages += 1
        if ((self.preset.get("discovery") or {}).get("feed") or {}).get("records", True):
            for entry in entries[: self.record_limit]:
                record = _feed_record(entry, response.url, self.preset)
                if not _validate_record(record, self.fields):
                    self.records += 1
                    yield record
        else:
            for entry in entries:
                if entry.get("link"):
                    yield Request(entry["link"], callback=self.parse_content, meta={"skippable": True})

    def parse_llms(self, response: Response):
        include_optional = ((self.preset.get("discovery") or {}).get("llms_txt") or {}).get("include_optional")
        origin = f"{urlparse(response.url).scheme}://{urlparse(response.url).netloc}"
        for link in parse_llms_txt(response.text, origin):
            if link["optional"] and not include_optional:
                continue
            self.discovered += 1
            yield Request(link["url"], callback=self.parse_content, meta={"skippable": True})


def _close_spider(crawler, reason: str) -> None:
    engine = crawler.engine
    if hasattr(engine, "close_spider_async"):
        from scrapy.utils.defer import deferred_from_coro

        deferred_from_coro(engine.close_spider_async(reason=reason))  # schedules on the running reactor's loop
    else:  # pragma: no cover - older Scrapy
        engine.close_spider(crawler.spider, reason)


def _reason(reason: str) -> str:
    return {"finished": "completed", "closespider_timeout": "max_duration", "closespider_itemcount": "max_records", "closespider_pagecount": "max_pages", "shutdown": "cancelled"}.get(reason, reason)


def settings_for(job: dict) -> dict:
    preset = job["preset"]
    limits = preset.get("request_limits") or {}
    delay = max(int(limits.get("min_delay_ms", 0)) / 1000, *(float(v.get("signals", {}).get("crawl_delay") or 0) for v in job["signals"].values()), 0)
    if limits.get("max_requests_per_second"):
        delay = max(delay, 1.0 / float(limits["max_requests_per_second"]))
    work = Path(job["work_dir"])
    settings = {
        "BOT_NAME": "dataforge",
        "USER_AGENT": job["user_agent"],
        "ROBOTSTXT_OBEY": job["purpose"] is not None and preset["policy"].get("robots_policy", "respect") == "respect",
        "ROBOTSTXT_PARSER": "scrapy.robotstxt.ProtegoRobotParser",
        "CONCURRENT_REQUESTS": max(1, int(limits.get("max_concurrency", 1))),
        "CONCURRENT_REQUESTS_PER_DOMAIN": max(1, int(limits.get("max_concurrency", 1))),
        "DOWNLOAD_DELAY": delay,
        "DOWNLOAD_DELAY_JITTER": 0,
        "AUTOTHROTTLE_ENABLED": True,  # AutoThrottle can only add delay above DOWNLOAD_DELAY
        "AUTOTHROTTLE_START_DELAY": max(delay, 0.5),
        "AUTOTHROTTLE_TARGET_CONCURRENCY": 1.0,
        "RETRY_ENABLED": True,  # mirrors the httpx engine: gateway errors and dropped connections only
        "RETRY_TIMES": 2,
        "RETRY_HTTP_CODES": [502, 503, 504],
        "COOKIES_ENABLED": False,
        "REDIRECT_MAX_TIMES": 5,
        "DOWNLOAD_MAXSIZE": 50 * 1024 * 1024,
        "DOWNLOAD_TIMEOUT": 30,
        "DOWNLOAD_VERIFY_CERTIFICATES": True,
        "DOWNLOADER_CLIENTCONTEXTFACTORY": "dataforge_scraping.engines.scrapy_engine.OSTrustContextFactory",
        "CLOSESPIDER_TIMEOUT": int(limits.get("max_duration_seconds", 900)),
        "CLOSESPIDER_ITEMCOUNT": int(job["max_records"]),
        "DEPTH_PRIORITY": 1,
        "SCHEDULER_DISK_QUEUE": "scrapy.squeues.PickleFifoDiskQueue",
        "SCHEDULER_MEMORY_QUEUE": "scrapy.squeues.FifoMemoryQueue",
        "HTTPERROR_ALLOW_ALL": False,
        "TELNETCONSOLE_ENABLED": False,
        "LOG_LEVEL": "WARNING",
        "HTTPCACHE_ENABLED": bool(job.get("cache_dir")),
        "HTTPCACHE_DIR": str(Path(job["cache_dir"]) / "scrapy") if job.get("cache_dir") else "httpcache",
        "HTTPCACHE_POLICY": "scrapy.extensions.httpcache.RFC2616Policy",
        "HTTPCACHE_IGNORE_HTTP_CODES": sorted(ACCESS_STOP_CODES),
        "DOWNLOADER_MIDDLEWARES": {"dataforge_scraping.engines.scrapy_engine.PolicyMiddleware": 50},
        "ITEM_PIPELINES": {"dataforge_scraping.engines.scrapy_engine.EmitPipeline": 900},
        "EXTENSIONS": {"scrapy.extensions.telnet.TelnetConsole": None},
        "SPIDER_MIDDLEWARES": {},
        "REQUEST_FINGERPRINTER_IMPLEMENTATION": "2.7",
        "FEEDS": {},
    }
    if job.get("incremental"):
        settings["SPIDER_MIDDLEWARES"]["dataforge_scraping.engines.scrapy_engine.DeltaFetchMiddleware"] = 100
        settings["DELTAFETCH_ENABLED"] = True
        settings["DELTAFETCH_DIR"] = job.get("deltafetch_dir") or str(work / "deltafetch")
    schema = _item_schema(preset)
    if schema:
        schema_path = work / "item-schema.json"
        schema_path.write_text(json.dumps(schema), encoding="utf-8")
        settings["EXTENSIONS"]["spidermon.contrib.scrapy.extensions.Spidermon"] = 500
        settings["SPIDERMON_ENABLED"] = True
        settings["ITEM_PIPELINES"]["spidermon.contrib.scrapy.pipelines.ItemValidationPipeline"] = 800
        settings["SPIDERMON_VALIDATION_SCHEMAS"] = [str(schema_path)]
        settings["SPIDERMON_VALIDATION_ADD_ERRORS_TO_ITEMS"] = False
        settings["SPIDERMON_VALIDATION_DROP_ITEMS_WITH_ERRORS"] = False
    if job.get("resume_dir"):
        settings["JOBDIR"] = job["resume_dir"]
    return settings


def _item_schema(preset: dict) -> dict | None:
    fields = (preset.get("extraction") or {}).get("fields") or []
    if not fields:
        return None
    types = {"string": ["string"], "url": ["string"], "decimal": ["number"], "integer": ["integer"]}
    return {
        "$schema": "http://json-schema.org/draft-07/schema#", "type": "object",
        "properties": {f["key"]: {"type": types.get(f.get("type", "string"), ["string"]) + ([] if f.get("required") else ["null"])} for f in fields},
        "required": [f["key"] for f in fields if f.get("required")],
    }


def run(job_path: str) -> int:
    global _EMIT
    _EMIT = sys.stdout
    sys.stdout = sys.stderr  # only protocol lines reach the parent's pipe
    job = json.loads(Path(job_path).read_text(encoding="utf-8"))
    from scrapy.crawler import CrawlerProcess

    process = CrawlerProcess(settings=settings_for(job), install_root_handler=True)
    process.crawl(PresetSpider, job=job)
    try:
        process.start()
    except Exception as error:  # noqa: BLE001 - report instead of dying silently
        emit("error", message=f"{type(error).__name__}: {error}")
        return 1
    return 0


if __name__ == "__main__":
    # Delegate to the importable module so settings paths and this file share one module instance.
    from dataforge_scraping.engines.scrapy_engine import run as package_run

    raise SystemExit(package_run(sys.argv[1]))
