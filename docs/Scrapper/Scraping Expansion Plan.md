# DataForge: Scraping Expansion Plan

**Status:** Proposed
**Date:** 2026-09-16
**Builds on:** [Scraper spec](Scrapper.md), [Website Preset Specification](Website%20Preset%20Specification.md), [Implementation decisions](../decisions.md)

This plan widens how DataForge collects data. It adds new ways to *find* sources, *read* them, and *stay compliant*, using open-source libraries and open data services researched in September 2026. Every new method plugs into the existing architecture:

- versioned presets and policy gates;
- the embedded WebView as the only rendering surface;
- 10-record test runs;
- immutable staged datasets with provenance;
- fixture-based health checks.

## 1. What exists today

| Area | Current capability |
| --- | --- |
| Strategies | `api` (JSON), `http` (HTML), `webview` (Scrape Studio child WebView) |
| Extraction | CSS selectors with `::text`/`::attr()`, transforms, typed fields, required-field validation, dedupe |
| Pagination | `next_link`, `page_parameter`, `cursor`, `api_cursor`, `detail_links` (HTTP); next link, infinite scroll, detail links (Studio) |
| Policy | Authorization acknowledgement, host/path scope, `urllib.robotparser`, access-challenge and 401/403/429 stops, redirect scope checks, record/page/duration caps |
| Credentials | Bearer or named header from the OS credential store, injected by the host |
| Presets | Generic list/table/JSON, Wikipedia search API, Hacker News search API; signed packages, fixture health checks |

**Gaps this plan closes:**

- No source discovery: sitemaps, feeds, or crawling.
- Robots parsing is not RFC 9309-grade.
- AI/TDM usage-preference signals are not read.
- Schema.org structured data embedded in pages goes unused.
- No document or PDF extraction.
- Only two open-data API presets.
- No web-archive or bulk-corpus sources.
- No HTTP caching.
- No change monitoring.
- Selectors break silently when layouts change.

## 2. Guardrails (unchanged, restated because new tools tempt breaking them)

These are **excluded** even though they are open source, because the spec forbids evading access controls or using external browsers:

| Tool or technique | Why excluded |
| --- | --- |
| TLS/browser fingerprint impersonation (for example `curl-cffi` impersonate mode) | Its purpose is to look like a browser to bypass bot detection. |
| Stealth or undetected browser modes (Scrapling stealth fetchers, Crawlee fingerprint rotation, playwright-stealth, undetected-chromedriver) | Detection evasion. |
| Rotating residential proxies and session spoofing | Evasion of rate limits and blocks. |
| CAPTCHA-solving services | The spec treats challenges as a stop condition. |
| Headless Playwright/Selenium/Puppeteer as a rendering path (including through Crawl4AI or Crawlee) | The spec requires rendering only in DataForge's visible embedded WebView. |
| Ignoring `Retry-After`, robots, TDMRep, or AI-preference signals | They are consent signals. |

These tools can still inform **design** (for example Scrapling's adaptive element relocation), but they are not adopted as runtime dependencies.

## 3. Expansion tracks

Each track lists the open-source component, its license (checked on PyPI, 2026-09-16), where it plugs in, and how it is gated.

### Track A — Source discovery

| Capability | Component | License | Integration |
| --- | --- | --- | --- |
| RFC 9309 robots.txt (longest-match, wildcards, `Crawl-delay`, sitemap lines) | **Protego 0.6.2** (used by Scrapy) | BSD-3-Clause | Replace `urllib.robotparser` in `extraction._check_robots`; honour `Crawl-delay` as a floor on `min_delay_ms`. |
| XML sitemaps and sitemap indexes, including gzip | **In-house parser** following the sitemaps.org protocol | — | `ultimate-sitemap-parser` 1.8.1 is **GPL-3.0-or-later**, so avoid it in a distributed desktop app. The protocol is small: `urlset`, `sitemapindex`, `lastmod`, gzip. |
| RSS, Atom, and JSON Feed | **feedparser 6.0.14** | BSD-2-Clause | New `discovery.feeds` source; each entry becomes a candidate URL or a record directly. |
| `llms.txt` (v2) | In-house Markdown link parser | — | Optional discovery hint: sites that publish Markdown versions of pages give cleaner, cheaper extraction. Never a permission signal. |

**New preset section:**

```yaml
discovery:
  mode: sitemap | feed | crawl | llms_txt | none
  sitemap: { url: /sitemap.xml, lastmod_after: 2026-01-01, url_pattern: "^/product/" }
  crawl:   { max_depth: 2, same_host_only: true, link_pattern: "^/listings/" }
```

**Rules:** discovered URLs pass the same scope, robots, and challenge checks as start URLs. Crawl frontiers are persisted in SQLite so pause, resume, and restart recovery work like other jobs.

### Track B — Usage-preference and consent signals

| Signal | Status (Sept 2026) | Integration |
| --- | --- | --- |
| **TDMRep** (`/.well-known/tdmrep.json`, `tdm-reservation` header or meta) | W3C Community Group spec for EU DSM Directive Art. 4 opt-outs | Read and store per host. If a reservation applies to the job's declared purpose, stop with an explanation. |
| **IETF AIPREF** `Content-Usage` (HTTP header or robots.txt rule; categories such as `train-ai`, `search`) | Working-group drafts; interim meetings held in 2026 | Parse and record. Map to the job's declared purpose (DataForge collection is neither AI training nor search indexing by default). Stop when the signal disallows the declared purpose. |
| `ai.txt` (Internet-Draft) | Individual draft | Record as advisory provenance only until standardized. |

**Changes:** add a required `policy.purpose` to scrape jobs (for example `internal_analysis`, `lead_research`, `dataset_building`) and a per-host `usage_signals` record stored with each scrape run. Unknown or unparseable signals become warnings, never silent allows. Reports indicate that EU AI Act transparency obligations for general-purpose AI took effect in August 2026, so these signals must be kept in the audit export.

### Track C — Selector-free structured extraction

| Capability | Component | License | Integration |
| --- | --- | --- | --- |
| JSON-LD, Microdata, RDFa, Open Graph, Microformats | **extruct 0.18.0** (+ **mf2py 2.0.1** for microformats2) | BSD / MIT | New `extraction.mode: structured_data`: pick schema.org types (`Product`, `LocalBusiness`, `JobPosting`, `RealEstateListing`, `Article`, `Event`) and map properties to canonical entity fields. |
| Main article text and metadata (title, author, date, site) | **trafilatura 2.2.0** | Apache-2.0 | New `extraction.mode: article`: boilerplate removal, Markdown/JSON output. |

**Why first:** schema.org markup is a high-signal source on a large share of commercial pages. The Web Data Commons project extracted 3.1 billion entities from 12.8 million sites. Structured data survives redesigns far better than CSS selectors, which cuts preset maintenance.

**Mapping:** ship `schemaorg_mappings.json` from schema.org types to DataForge canonical entities, so structured records flow straight into matching (for example `LocalBusiness.telephone` becomes `phone` and `PostalAddress.streetAddress` becomes `address`).

**Health:** fixtures cover JSON-LD with nested `@graph`, Microdata, and conflicting duplicate blocks. Rule: when JSON-LD and Microdata disagree, keep both, flag a conflict, and never merge silently.

### Track D — Documents and downloadable files

| Capability | Component | License | Integration |
| --- | --- | --- | --- |
| PDF text and coordinate-based tables (default) | **pdfplumber 0.11.10** | MIT | New `extraction.mode: document_tables`; best accuracy of the lightweight tools in published comparisons. |
| Lattice/stream tables | **camelot-py 2.0.0** | MIT | Fallback for ruled tables. |
| Complex layouts and scanned documents (optional add-on) | **Docling 2.127.0** (TableFormer models) | MIT | Optional downloadable component; heavy ML models, runs locally. Not bundled by default, to keep the installer small. |
| CSV, XLSX, or JSON linked from a page | Existing importers | — | `discovery.file_links`: download in-scope files and hand them to the dataset import job with source-URL provenance. |

Unstructured 0.27.6 (Apache-2.0) is a comparable alternative but pins Python below 3.14. Docling is preferred.

**Rules:** the file type is verified by magic bytes, not the extension. Size caps default to 50 MB. Documents are stored content-addressed like other imports, and each extracted row keeps its page number and table index as provenance.

### Track E — Open data API connector family

Presets that need only JSON, plus two small runtime additions: query-parameter API-key auth, and a project-level **contact identity** for the `User-Agent` (several services require it).

| Source | Access notes (Sept 2026) | Preset(s) | Canonical entity |
| --- | --- | --- | --- |
| **CKAN** portals (data.gov, data.gov.uk, and many national/city portals) | Public Action API; `ckanapi` (MIT) as reference | `ckan.dataset_search`, `ckan.resource_download` → Track D | dataset/table |
| **Socrata SODA** (US state and city open data) | Public; app token raises limits; `sodapy` (MIT) as reference | `socrata.dataset_rows` with `$limit`/`$offset` paging | table rows |
| **Wikidata SPARQL** | 60 s query timeout, 5 parallel queries per IP, time budget per UA and IP | `wikidata.sparql` (user query, capped, `LIMIT` enforced) | business, person, place |
| **OpenAlex** | API key required since Feb 2026 (replaced email "polite pool"); about 100k calls/day guidance | `openalex.works`, `openalex.institutions` | article, organization |
| **OpenStreetMap Overpass** | Shared public servers with load shedding; heavy use belongs on your own instance | `osm.overpass_pois` (bounded bbox, small quotas; option to point at a self-hosted endpoint) | business/place |
| **SEC EDGAR** (`data.sec.gov`) | Max 10 requests/s; **User-Agent with name and contact email required**, otherwise 403 | `sec.company_facts`, `sec.filings_index` | business |
| **GDELT** | Public document/event APIs | `gdelt.doc_search` | article/event |
| Existing: Wikipedia, Hacker News | — | — | article |

**Runtime additions:**

1. **Auth type `query_param`** (for example `api_key`): the value is injected by the host like header auth, and `logs.redact` already strips query strings.
2. **`request_limits.max_requests_per_second`** and **`respect_retry_after: true`** for APIs that document back-off. The default policy still stops on 429 unless the preset explicitly declares documented back-off.
3. **Project contact identity** in Settings (organization plus contact email), inserted into the `User-Agent` only for presets marked `requires_contact_user_agent`.
4. **`api_graphql`** and **`api_sparql`** request styles (POST body templates with whitelisted variables).

### Track F — Web archives and bulk corpora

These fit DataForge's matching and deduplication strengths.

| Source | Component | License | Integration |
| --- | --- | --- | --- |
| **Common Crawl** CDX index plus WARC byte-range reads (monthly crawls since 2008) | **cdx-toolkit 0.9.39**, **warcio 1.8.1** | Apache-2.0 | New job kind `archive_query`: query an index for URL patterns, fetch only matching WARC records by byte range, then run the normal extraction modes on archived HTML. |
| **Wayback Machine** CDX API (no auth) | cdx-toolkit | Apache-2.0 | Historical snapshots of a page for change analysis or recovering removed listings. |
| **Web Data Commons** schema.org subsets (class-specific extractions from Common Crawl) | Direct download (N-Quads) | Data under Common Crawl terms | `bulk_import` job: stream a class subset (for example `LocalBusiness`) filtered by country or domain into a staged dataset, then match against user data. |

**Why:** archives let users collect at scale **without** hitting live sites, which is the least disruptive option. Archive provenance records the crawl ID, capture timestamp, and WARC digest.

### Track G — HTTP runtime hardening

| Capability | Component | License | Integration |
| --- | --- | --- | --- |
| RFC 9111 caching, `ETag`/`Last-Modified` revalidation | **hishel 1.3.1** (httpx integration, SQLite storage) | BSD | Per-project cache under `cache/`; re-runs and health checks send conditional requests, so unchanged pages cost a 304. Cache purge sits in the deletion controls. |
| Per-host politeness scheduler | In-house | — | One queue per host with the minimum of the preset delay, `Crawl-delay`, and the declared rps; global concurrency cap; persisted so pauses survive restarts. |
| Faster HTML parsing for large crawls | **selectolax 0.4.11** (Lexbor) with **parsel 1.11.0** for XPath/JMESPath | MIT / BSD-3 | Pluggable parser: selectolax for CSS-only presets, parsel when a preset uses XPath or JMESPath selectors. BeautifulSoup stays as the compatibility default. |

**Decision:** keep the in-process httpx runtime for tests, single pages, API presets, and small jobs, and add **Scrapy as an optional high-volume engine** (Track L) in its own process. Its Twisted reactor cannot run inside the sidecar's job threads, but works well as an isolated child process. Crawlee (Apache-2.0) remains a design reference only; its browser and fingerprint features are out of scope (section 2).

### Track H — Extraction resilience and preset maintenance

| Capability | Inspiration or component | Integration |
| --- | --- | --- |
| **Adaptive element relocation**: when a selector stops matching, find the most similar element using stored tag, attribute, and text fingerprints | Scrapling 0.4.15 (BSD-3) adaptive selectors, as a *technique* | Health checks store a fingerprint per field. On failure, compute a candidate selector and mark the preset **degraded with a suggested fix**. A human reviews and saves a new preset version; fixes are never applied silently. |
| Selector fallbacks from multiple strategies | parsel CSS plus XPath | Studio picker proposes an ordered list (id/class CSS, structural XPath, text-anchored XPath); the runtime already tries them in order. |
| Coverage drift alerts | In-house | Compare field coverage per run to the fixture baseline; a drop beyond the threshold moves the preset to degraded. |
| **Example-based field picking**: type a value you can see ("$1,299", "Austin, TX") and get candidate selectors | **autoscraper 1.1.14** (MIT) | Studio and the selector editor propose rules learned from example values. Proposals still go through the 10-record test and review, like picker output. |

### Track I — Change monitoring and scheduled re-collection

| Capability | Inspiration or component | Integration |
| --- | --- | --- |
| Scheduled re-runs with field-level diffs | changedetection.io design (version history per watch) | New `watch` object: preset plus URL plus schedule. Each run stages a new immutable dataset version; a diff job reports added, removed, and changed records keyed by `unique_by`. |
| Feed watches | feedparser plus hishel conditional GETs | Cheap polling that honours cache headers. |
| Archive diffs | Wayback CDX (Track F) | "What changed on this page since date X" without re-fetching live pages. |

Schedules run only while the desktop app is open (local-first). Each watch shows its next run time, last result, and pause and stop controls in the job center.

### Track J — Optional provenance archiving (WARC)

**warcio** (Apache-2.0) writes fetched responses to per-job WARC files **only when the user opts in**. The spec says to avoid retaining raw content unless the user elects to. The payoff:

- exact reproducibility of extraction;
- one-click creation of sanitized fixtures for new presets (strip cookies and tokens, then review);
- audit evidence.

The retention period and deletion controls live in project settings.

### Track K — Opt-in AI-assisted extraction (proposal only, never authority)

The spec allows AI-assisted mapping only if it is **opt-in, labeled, reviewable, and evaluated**. Design:

- **LLM proposes, deterministic code extracts.** Given a page's cleaned Markdown (trafilatura) or structured data (extruct), a model suggests record roots, selectors, and field mappings as a **draft preset**. The normal 10-record test and review must pass before saving.
- **Local-first.** A local model is used where available. Sending page content to a remote model requires explicit per-project consent and shows exactly what will be sent; personal-data fields are redacted before sending.
- **References, not dependencies:**
  - Crawl4AI 0.9.3 (Apache-2.0) for Markdown generation and schema-extraction patterns; its Playwright rendering is excluded.
  - Firecrawl's client SDK (MIT) and hosted API are a cloud service, not used by default.
  - ScrapeGraphAI 2.2.4 declares **no license on PyPI**; do not depend on it until that is clarified.
- **Evaluation gate.** Suggested selectors are scored against fixture pages. A suggestion is surfaced only if its test coverage meets the preset's thresholds.

### Track L — Scrapy as an optional high-volume engine

**Scrapy 2.19** (BSD-3-Clause), maintained by Zyte, is the most mature free Python crawling framework. It brings a request scheduler, per-domain concurrency, AutoThrottle, a built-in HTTP cache, robots handling through Protego, item pipelines, and crawl statistics. DataForge adopts it as a **second execution engine** for large sitemap and crawl jobs, while the httpx runtime keeps handling tests, API presets, and small jobs.

**How it runs**

- **Isolated process.** The job runner starts `dataforge-service --engine scrapy --job <job.json>` (a second entry point in the same PyInstaller build), so the Twisted reactor gets its own process. Items stream back as JSON lines; Scrapy stats become job stage events. Cancel sends a graceful stop, then terminates after a timeout. Pause stops scheduling new requests at the next checkpoint.
- **No site-specific Python code.** One generic `PresetSpider` reads the pinned preset (record root, parsel selectors, pagination, discovery settings). The spec forbids presets that hide executable scripts, so users never upload spiders.
- **Same validation and staging.** Items go through the existing transforms, `validate_candidates`, dedupe, provenance, and `register_staged_rows`. Job results are identical in shape whichever engine ran.

**DataForge policy enforced as Scrapy settings and middlewares**

| Concern | Scrapy mechanism |
| --- | --- |
| robots.txt | `ROBOTSTXT_OBEY = True`, `ROBOTSTXT_PARSER` = Protego (Scrapy's default) |
| Politeness | `CONCURRENT_REQUESTS_PER_DOMAIN = 1` by default, `DOWNLOAD_DELAY` from the preset and `Crawl-delay`, AutoThrottle only able to *slow down* |
| Caps | `CLOSESPIDER_ITEMCOUNT`, `CLOSESPIDER_PAGECOUNT`, `CLOSESPIDER_TIMEOUT` from preset limits |
| Scope | DataForge downloader middleware drops out-of-scope requests and redirects (with a warning), mirroring `validate_url` |
| Access challenges, 401/403/429 | DataForge middleware closes the spider with the stop reason, never retries around a block |
| TDMRep and AIPREF signals | DataForge middleware checks the per-host signal record (Track B) before the first request to each host |
| HTTP cache | Scrapy `HttpCacheMiddleware` with the RFC 2616 policy, stored in the project cache folder |
| Incremental re-crawls | **scrapy-deltafetch 2.1.0** (BSD) skips already-seen items for watches (Track I) |
| Item quality | **Spidermon 1.27.0** (BSD-3-Clause) validates items against the preset field schema and feeds coverage and health checks |
| Items | **itemadapter 0.13.1** and **itemloaders 1.4.0** (BSD-3-Clause); **w3lib 2.4.1** (BSD-3-Clause) for URL canonicalization |

**Not adopted from the Scrapy ecosystem**

- `scrapy-playwright` and `scrapy-splash`: they render with external browsers.
- Rotating-proxy and fake user-agent middlewares: evasion (section 2).
- **Scrapyd 1.6.0** (BSD): a server-deployment daemon that a local desktop job runner does not need. Worth revisiting for a future shared or server edition.

**Engine choice:** `engine: auto` uses Scrapy only for `discovery.mode: sitemap | crawl` jobs above a page threshold (default 200 pages), and httpx otherwise. Users can force either engine; tests always use httpx so they stay fast and deterministic.

## 4. Free and open-source tool catalog

Every tool below is free to use. Versions and licenses were checked on PyPI on 2026-09-16; Java crawlers were checked on their project pages. **Bundle** means safe to ship inside the installer under the CI license allow-list (MIT, BSD, Apache-2.0, PSF, W3C).

### Crawling and fetching

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| Scrapy (+ itemadapter, itemloaders, w3lib) | 2.19.0 | BSD-3-Clause | **Bundle**: optional high-volume engine (Track L) |
| httpx | 0.28.1 | BSD-3-Clause | **In use**: default runtime |
| hishel | 1.3.1 | BSD | **Bundle**: HTTP caching (Track G) |
| requests + requests-cache | 2.34.2 / 1.3.3 | Apache-2.0 / BSD-2-Clause | Not needed; httpx and hishel cover it |
| aiohttp | 3.14.3 | Apache-2.0 AND MIT | Not needed; httpx supports async |
| scrapy-deltafetch | 2.1.0 | BSD | **Bundle** with the Scrapy engine (incremental crawls) |
| Spidermon | 1.27.0 | BSD-3-Clause | **Bundle** with the Scrapy engine (item validation) |
| Scrapyd | 1.6.0 | BSD | Future server edition only |
| MechanicalSoup | 1.4.0 | MIT | Not adopted: its purpose is form filling and logins, which presets forbid |
| Apache Nutch, Apache StormCrawler, Heritrix | — | Apache-2.0 | Java, cluster-scale; reference only (Heritrix is the Internet Archive's archival crawler) |
| Colly (Go) | — | Free for commercial use | Reference only; wrong language for the worker |
| Crawlee (Python) | 1.10.1 | Apache-2.0 | Design reference; browser and fingerprint features excluded |

### Parsing and selectors

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| BeautifulSoup | 4.15.0 | MIT | **In use** |
| lxml | 6.1.3 | BSD-3-Clause | **Bundle**: fast parser backend |
| html5lib | 1.1 | MIT | Optional lenient parser for broken HTML |
| parsel | 1.11.0 | BSD-3-Clause | **Bundle**: XPath and JMESPath selectors |
| selectolax | 0.4.11 | MIT | **Bundle**: fast CSS for large crawls |
| autoscraper | 1.1.14 | MIT | **Bundle**: example-based selector suggestions (Track H) |
| Scrapling | 0.4.15 | BSD-3-Clause | Technique reference for adaptive relocation; stealth fetchers excluded |

### Content, articles, and metadata

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| trafilatura | 2.2.0 | Apache-2.0 | **Bundle**: primary article extractor (Track C) |
| htmldate | 1.10.0 | Apache-2.0 | **Bundle**: publication dates (a trafilatura companion) |
| courlan | 1.4.0 | Apache-2.0 | **Bundle**: URL filtering and canonicalization for crawls |
| newspaper4k | 0.9.6 | MIT | Fallback article extractor for news sites |
| goose3 | 3.1.22 | Apache | Alternative article extractor |
| readability-lxml | 0.9 | Apache-2.0 | Readability-style main-content fallback |
| jusText | 3.0.2 | BSD-2-Clause | Boilerplate removal for non-English text |
| extruct + mf2py | 0.18.0 / 2.0.1 | BSD / MIT | **Bundle**: structured data (Track C) |
| markdownify | 1.2.3 | MIT | **Bundle**: HTML to Markdown for AI suggestions (Track K) |
| html2text | 2025.4.15 | **GPL-3.0-or-later** | **Avoid**: use markdownify instead |
| feedparser | 6.0.14 | BSD-2-Clause | **Bundle**: feeds (Track A) |
| Protego | 0.6.2 | BSD-3-Clause | **Bundle**: robots.txt (Track A) |
| ultimate-sitemap-parser | 1.8.1 | **GPL-3.0-or-later** | **Avoid**: in-house sitemap parser instead |

### Field normalization (feeds matching quality)

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| phonenumbers | 9.0.39 | Apache-2.0 | **Bundle**: region-aware E.164 parsing, as the matching spec recommends |
| usaddress | 0.5.16 | MIT | **Bundle**: US address component parsing |
| libpostal / pypostal | — | MIT (model refreshes under Apache-2.0) | Optional add-on for international addresses; large model data, downloaded on demand |
| price-parser | 0.5.1 | BSD-3-Clause | **Bundle**: prices and currency from scraped text |
| dateparser | 1.4.3 | BSD-3-Clause | **Bundle**: human-written and multilingual dates |
| tldextract | 5.3.2 | BSD-3-Clause | **Bundle**: registrable domains for scope and URL matching |
| url-normalize | 3.0.0 | MIT | Covered by w3lib and courlan |
| lingua-language-detector / langdetect | 2.2.0 / 1.0.9 | Apache-2.0 | **Bundle** (lingua): tag record language for normalization |

### Documents and OCR

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| pdfplumber | 0.11.10 | MIT | **Bundle** (Track D) |
| camelot-py | 2.0.0 | MIT | **Bundle** (Track D) |
| Docling | 2.127.0 | MIT | Optional add-on (Track D) |
| tabula-py | 2.10.0 | MIT | Not bundled: needs a Java runtime |
| Apache Tika (tika-python) | 3.3.2 | Apache-2.0 | Not bundled: needs a Java server; optional for exotic formats |
| pytesseract (+ Tesseract binary) | 0.3.13 | Apache-2.0 | Optional OCR add-on for scanned PDFs and images |
| OCRmyPDF | 17.11.0 | MPL-2.0 | **Avoid bundling**: depends on Ghostscript (AGPL) |

### Archives

| Tool | Version | License | Status in plan |
| --- | --- | --- | --- |
| warcio | 1.8.1 | Apache-2.0 | **Bundle** (Tracks F and J) |
| cdx-toolkit | 0.9.39 | Apache-2.0 | **Bundle** (Track F) |
| warcprox | 2.13.1 | **GPL-2.0-or-later** | **Avoid**: WARC capture happens inside the runtime with warcio |

### Excluded free tools (policy, not license)

scrapy-playwright 0.0.48 and scrapy-splash 0.11.1 (external rendering); Playwright, Selenium, Puppeteer, and browser-based archivers (the rendering surface must be the embedded WebView); curl-cffi impersonation; rotating-proxy and fake user-agent middlewares; CAPTCHA solvers. See section 2.

## 5. Architecture changes

| Layer | Change |
| --- | --- |
| Preset schema (`packages/contracts/preset.schema.json`, `presets.validate_preset`) | Add `discovery`, `extraction.mode` (`selectors` \| `structured_data` \| `article` \| `document_tables` \| `api`), `policy.purpose`, `request_limits.max_requests_per_second`, `respect_retry_after`, `api_integration.auth: query_param`, `requires_contact_user_agent`. Bump the schema minor version; existing presets remain valid. |
| Scraping worker | New modules: `engines/scrapy/` (generic `PresetSpider`, policy middlewares, settings from preset limits), `discovery/` (sitemaps, feeds, crawl frontier, llms.txt), `signals/` (Protego robots, TDMRep, AIPREF), `structured/` (extruct/trafilatura adapters plus schema.org mapping), `documents/` (pdfplumber/camelot; optional Docling), `archives/` (cdx-toolkit/warcio), `http/` (hishel client, per-host scheduler). |
| Application service | Engine selection (`engine: auto | httpx | scrapy`) and a subprocess runner for the Scrapy engine. Job kinds `crawl`, `archive_query`, `bulk_import`, `watch_run`, `diff`. Migration adding `crawl_frontier`, `usage_signals`, `watches`, `dataset_versions`, `http_cache_index`. |
| Desktop UI | Scraping tab gains a **source picker** (Website / Sitemap / Feed / Open data API / Web archive / Documents), a signals panel (robots, TDMRep, AIPREF result per host), and a watch schedule editor. Studio gains "Use structured data" when JSON-LD is detected on the loaded page. |
| Host | Contact identity setting; query-param credential injection. No new rendering surfaces. |
| Packaging | Base installer adds only permissively licensed packages marked **Bundle** in section 4, including Scrapy and its Twisted dependency (a second entry point in the same PyInstaller build). Docling, libpostal models, and Tesseract OCR ship as optional downloads. |

## 6. Phased roadmap

| Phase | Scope | Effort | Exit criteria |
| --- | --- | --- | --- |
| **1. Compliance and politeness foundation** | Protego robots (RFC 9309, Crawl-delay), TDMRep and AIPREF signal parsing with `policy.purpose`, hishel caching, per-host scheduler, contact User-Agent | M | Fixture tests for robots edge cases (longest match, wildcards, 5xx or unreachable robots), TDMRep and AIPREF allow/deny per purpose, 304 revalidation, and delay floors. |
| **2. Structured data, articles, and field normalization** | extruct + mf2py `structured_data` mode, schema.org → canonical mappings, trafilatura/htmldate `article` mode, Studio "use structured data"; phonenumbers, usaddress, price-parser, dateparser normalizers | M | JSON-LD/Microdata/RDFa fixtures (nested `@graph`, conflicting blocks); structured product and business records flow into matching without selectors. |
| **3. Discovery and the Scrapy engine** | Sitemap/sitemap-index parser (in-house), feed discovery, scoped crawl with a persisted frontier, llms.txt hint; Scrapy engine subprocess with generic `PresetSpider`, DataForge policy middlewares, deltafetch, and Spidermon item validation | L | 10k-URL sitemap-index fixture within caps; crawl pause, resume, and restart; out-of-scope links never fetched; the same fixture site produces identical staged records from the httpx and Scrapy engines; cancel stops the subprocess cleanly. |
| **4. Open data API family** | Query-param auth, rps limits, Retry-After policy; presets for CKAN, Socrata, Wikidata SPARQL, OpenAlex, Overpass, SEC EDGAR, GDELT | M | Each preset has recorded-response fixtures and a health check; SEC presets refuse to run without a contact identity; the Wikidata preset enforces `LIMIT` and a timeout. |
| **5. Documents** | pdfplumber/camelot `document_tables`, file-link discovery to import; optional Docling add-on | M | PDF fixtures (ruled, unruled, multi-page); page/table provenance; magic-byte and size checks. |
| **6. Archives and bulk corpora** | Common Crawl and Wayback CDX queries, WARC range fetches, Web Data Commons class-subset `bulk_import` | L | Archive jobs extract from recorded WARC fixtures offline; bulk import streams a large subset within memory limits. |
| **7. Resilience and monitoring** | Field fingerprints, adaptive relocation suggestions, example-based selector suggestions (autoscraper), coverage drift, watches with scheduled runs, dataset-version diffs, opt-in WARC capture | L | A changed-layout fixture yields a reviewed suggested selector; the watch diff reports added, removed, and changed records correctly. |
| **8. Opt-in AI suggestions** | Draft-preset proposals from cleaned Markdown and structured data, local-first, consent-gated remote models, evaluation gate | M | Suggestions appear only when fixture coverage meets thresholds; nothing sent remotely without consent; redaction verified. |

Phases 1–2 deliver the most value at the lowest risk: compliance first, then the extraction mode that avoids brittle selectors. Phases 3–5 can run in parallel after that.

## 7. Testing approach

- **No live sites in automated tests**, consistent with the existing suites. Every connector ships recorded responses (for example JSON, XML, WARC, PDF), fixture health checks, and JSON-schema contract tests for new job results.
- **Property-based tests** (Hypothesis) for URL canonicalization, sitemap parsing, robots matching, and crawl-frontier deduplication.
- **Real-app verification**: WebView-related features (Studio structured-data detection) are checked in the desktop app through the existing WebView2 debugging harness.
- **Politeness assertions**: tests measure request timing against declared delays and rps, and assert that no request precedes its robots or TDMRep check.

## 8. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Emerging standards (AIPREF, ai.txt, llms.txt v2) change | Version the signal parsers; record raw signals in provenance; unknown values produce warnings, never implicit permission. |
| GPL or unclear licenses slip into the bundle | CI license check (for example `pip-licenses` with an allow-list: MIT, BSD, Apache-2.0, PSF, W3C). ultimate-sitemap-parser, html2text, warcprox (GPL), OCRmyPDF's Ghostscript dependency (AGPL), and ScrapeGraphAI (no declared license) stay out of the bundle. |
| Scrapy engine drifts from httpx behaviour | Shared validation and staging code; a parity test runs both engines on the same fixture site and compares staged records; policy middlewares have their own unit tests. |
| Installer size grows with Scrapy and Twisted | Measure the PyInstaller size delta in CI; if it is too large, ship the Scrapy engine as an optional component. |
| Heavy ML dependencies (Docling models) bloat the installer | Optional component downloaded on demand, with checksum verification. |
| Public API quotas and policy changes (OpenAlex keys, Overpass load shedding, EDGAR 403s) | Presets declare limits and docs links; health checks detect auth or quota errors and mark presets degraded; users can point Overpass or Wikidata presets at self-hosted endpoints. |
| Bulk archive data volume | Streaming readers, per-job byte and record caps, and a disk-space preflight check. |
| Structured data is spammy or contradictory | Keep every source block with provenance and flag conflicts; the matching guards stay authoritative. |

## 9. Implementation status (2026-09-16)

All eight phases are implemented. Automated tests stay offline; `scripts/live-check.py` verifies the same paths against real, permitted sources.

### What was built

| Phase | Implemented in | Notes and deviations from the plan |
| --- | --- | --- |
| 1. Compliance and politeness | `workers/scraping/.../signals.py`, `fetch.py` | Protego robots with RFC 9309 error handling (5xx or unreachable = disallow-all, 4xx = allow). TDMRep (well-known file, header, meta), AIPREF Content-Usage (robots rules and header), ai.txt as advisory. Required `purpose` on every job. hishel cache per project. Per-host scheduler (preset delay, Crawl-delay, rps). Contact User-Agent. **Added:** OS trust store for TLS (`truststore`), bounded retries for 502/503/504 and dropped connections. |
| 2. Structured data, articles, normalizers | `structured.py`, `normalize.py`, `data/schemaorg_mappings.json` | extruct + mf2py; nested property values (publisher, address) are read through mappings, not emitted as records; consistent duplicate blocks merge, conflicting ones are kept and flagged. trafilatura + htmldate article mode. phonenumbers, usaddress, price-parser, dateparser, tldextract (offline snapshot) as transforms. lingua is an optional extra. Studio offers "Use structured data" on pages that carry it. |
| 3. Discovery and Scrapy engine | `discovery.py`, `runtime.py`, `engines/` | In-house sitemap parser (gzip, index, text, entity-safe), feedparser on bytes only, persisted crawl frontier (retry resumes), llms.txt. Scrapy child process with a generic `PresetSpider`, DataForge policy middleware, OS-trusted TLS verification, deltafetch for incremental watches, Spidermon item validation. Parity test: both engines produce identical records. |
| 4. Open data APIs | presets `ckan.package_search`, `socrata.dataset_rows`, `wikidata.sparql`, `openalex.works`, `osm.overpass_pois`, `sec.submissions`, `gdelt.doc_search` | Request templates with typed, validated variables; `query_param` auth; read-only SPARQL with an enforced LIMIT; bounding-box area cap; columnar JSON. **Changed:** catalog.data.gov no longer serves the CKAN API, so the CKAN preset defaults to open.canada.ca. overpass-api.de disallows `/api/` in robots.txt, so the Overpass preset defaults to the Kumi Systems public instance. |
| 5. Documents | `documents.py` | pdfplumber tables (ruled, then text strategy) with page, table, and row provenance; magic-byte checks and a 50 MB cap; CSV links become rows, XLSX/JSON links are imported as datasets. **Changed:** camelot-py 2.0 needs numpy, pandas, and OpenCV, so it moved to an optional extra with Docling. |
| 6. Archives and bulk corpora | `archives.py`, `archive_query` and `bulk_import` jobs | Wayback CDX and Common Crawl index queries, WARC range reads (warcio), streaming Web Data Commons N-Quads. **Changed:** the CDX protocol runs over DataForge's policy client instead of cdx-toolkit, whose own `requests` session would bypass the scheduler, robots checks, and trust store. |
| 7. Resilience and monitoring | `resilience.py`, `diff.py`, watches | Field fingerprints saved on passing health checks, relocation suggestions, coverage drift, autoscraper example-based selectors, watches with a local scheduler and dataset diffs, opt-in WARC capture with retention. |
| 8. AI suggestions | `suggest.py` | Local heuristic proposals by default; OpenAI-compatible endpoint optional. Loopback endpoints need no consent; remote endpoints need per-project consent, HTTPS, and redacted page text. Every proposal must pass extraction on the page before it is shown. |

Packaging: `requirements.txt` pins the bundle and `scripts/check-licenses.py` gates it (108 packages, all permissive or weak copyleft). Spidermon's `jsonschema[format]` extra pulls rfc3987 (GPL-3.0-or-later), which is excluded from the PyInstaller build.

### Live verification

Run on 2026-09-16 with `scripts/live-check.py` (small caps, test mode unless noted).

| Check | Source | Result |
| --- | --- | --- |
| Signals | books.toscrape.com, theguardian.com, en.wikipedia.org, overpass-api.de | Allowed where permitted. Wikipedia refused: Wikimedia returns 403 for robots.txt without a contact User-Agent, treated as disallow-all. Overpass `/api/` disallowed by robots.txt. |
| Selectors + next link | books.toscrape.com | Test 10 records; full run 60 records over 3 pages; repeat run revalidated from cache. |
| Example-based and proposed selectors | books.toscrape.com | Examples produced a root matching 20 items; the local proposal extracted 20 records at 100% coverage. |
| Crawl, both engines | books.toscrape.com travel category | 11 books from 12 pages on each engine; identical UPC sets. |
| Feed + article | blog.python.org | 10 feed entries; article text and date extracted (803 words). |
| llms.txt and sitemap | llmstxt.org | 3 Markdown pages from llms.txt; 3 articles from the sitemap. |
| Structured data from a news sitemap | theguardian.com | One `LiveBlogPosting` record per page with author and publisher. |
| PDF tables | pdfplumber example PDF on raw.githubusercontent.com | Rows with page, table, and row provenance. |
| CKAN | open.canada.ca | 10 datasets, 100% field coverage; robots Crawl-delay of 20 s applied. Also run from the desktop UI. |
| Socrata | data.cityofchicago.org | 10 rows; Crawl-delay 1 s applied. |
| OpenAlex | api.openalex.org | 10 works without a key. |
| GDELT | api.gdeltproject.org | 10 articles (a first attempt hit a dropped connection, now retried). |
| Overpass | overpass.kumi.systems | 9 libraries in a downtown Austin bounding box (a first attempt hit a 504, now retried). |
| SEC EDGAR, Wikidata | — | Refused before any request until a contact identity is set. |
| Wayback Machine | web.archive.org | 10 records from a 2017 capture with archive provenance. |
| Common Crawl | index.commoncrawl.org, data.commoncrawl.org | Index query works; data.commoncrawl.org disallows all paths in robots.txt, so the job stops with an explanation. |
| Watch + diff | quotes.toscrape.com | Two runs, 20 records each: 0 added, 0 removed, 0 changed. |
| Web Data Commons | LocalBusiness sample file | 21 records from 31 pages. |
| Maintenance | books.toscrape.com page | No drift on the live page; a simulated class rename produced price drift and the suggestion `p.price-now`. |

Bugs found by the live run and fixed, each with a regression test: spurious coverage warnings under the test cap; nested structured-data entities emitted as records; unmerged duplicate JSON-LD blocks; missing schema.org Article subtypes; an invalid codec error handler in N-Quads unescaping; Socrata bookkeeping columns; no retries for transient gateway errors.

### Follow-up completed 2026-09-17

| Gap | Resolution | Verified |
| --- | --- | --- |
| Studio collections skipped site signals | Purpose in Studio; `scrape.check_url` with purpose on every automated navigation; `stage_rendered` requires a purpose and records signals (decision D24) | Service test with robots.txt and TDMRep; real desktop app on books.toscrape.com and theguardian.com |
| Studio "Use structured data" untested in the app | — | Real desktop app: a Guardian article offered `NewsArticle ×1` and staged one record (title, date, publisher) |
| Selector fallbacks | Label-anchored and structural XPath fallbacks from the picker, evaluated by the bridge and both engines | jsdom bridge tests; runtime test; real app (a book title was wrongly used as a label, now rejected) |
| Scrapy resume, pause, incremental | JOBDIR resume folder kept across retries; pause test; deltafetch via an async wrapper; Spidermon errors surfaced | Engine tests: no requests while paused, cancel then resume refetches nothing, second incremental run yields 0 |
| Contracts | New schemas for signals, scrape results, diffs, watches, settings; job kinds fixed | Contract test on live service responses |
| Fixtures from captures, health-check fixes, archive diffs, OCR | Implemented (decision D25); OCR is an optional add-on | Service and worker tests; live Wayback comparison of llmstxt.org's earliest and latest captures reported 1 changed record (title, text, author, date) after the Internet Archive recovered from an outage |

Still not verified: SEC EDGAR and Wikidata live runs (they need your contact identity), a model endpoint for AI proposals (none configured), real Tesseract OCR (not installed), and the packaged build. On this machine every newly built PyInstaller executable disappears as it is written, including a one-line "hello" program, so the block is the antivirus policy rather than DataForge's bundle; an exception for the build folder is needed.

## Sources

Research conducted 2026-09-16. Versions and licenses were read from PyPI metadata on the same date.

- Frameworks overview: [Scrapfly: 10 Best Open-Source Web Scrapers in 2026](https://scrapfly.io/blog/posts/best-open-source-web-scrapers), [Firecrawl: Best Open-Source Web Scraping Libraries in 2026](https://www.firecrawl.dev/blog/best-open-source-web-scraping-libraries), [ScrapeHero: Open Source Web Scraping Frameworks](https://www.scrapehero.com/open-source-web-scraping-frameworks-and-tools/)
- Structured data and text: [extruct](https://github.com/scrapinghub/extruct), [Trafilatura docs](https://trafilatura.readthedocs.io/), [Scrapfly: Scraping Microformats](https://scrapfly.io/blog/posts/web-scraping-microformats), [Web Data Commons Schema.org Data Set Series (WWW 2023)](https://dl.acm.org/doi/10.1145/3543873.3587331), [WDC Schema.org Table Corpus](http://webdatacommons.org/structureddata/schemaorgtables/2023/index.html)
- AI extraction tools: [Firecrawl: Best Web Extraction Tools for AI](https://www.firecrawl.dev/blog/best-web-extraction-tools), [ScrapeOps: Best AI Web Scraping Tools](https://scrapeops.io/web-scraping-playbook/best-ai-web-scraping-tools/)
- Archives: [Common Crawl Index announcement](https://commoncrawl.org/blog/announcing-the-common-crawl-index), [cdx_toolkit](https://github.com/commoncrawl/cdx_toolkit), [Wayback CDX Server API](https://github.com/internetarchive/wayback/tree/master/wayback-cdx-server), [warcio](https://github.com/webrecorder/warcio)
- Robots, sitemaps, feeds: [Protego on PyPI](https://pypi.org/p/protego), [Ultimate Sitemap Parser](https://ultimate-sitemap-parser.readthedocs.io/en/stable/index.html), [CPython issue: RFC 9309 in robotparser](https://github.com/python/cpython/issues/138907), [feedparser docs](https://feedparser.readthedocs.io/en/latest/), [changedetection.io](https://changedetection.io/tutorial/changedetectionio-can-be-your-new-favourite-rss-reader)
- Usage preferences: [IETF AIPREF documents](https://datatracker.ietf.org/group/aipref/documents/), [EDRLab notes, AIPREF April 2026](https://www.edrlab.org/2026/04/17/notes-from-the-ietf-ai-pref-toronto-meeting-april-2026/), [ScrapingBee: Parsing TDMRep and AI.txt](https://www.scrapingbee.com/blog/tdmrep-ai-txt-scraping-controls/), [draft-car-ai-txt-wellknown](https://datatracker.ietf.org/doc/draft-car-ai-txt-wellknown/), [llms.txt v2](https://llmstxt.org/)
- Documents: [Procycons PDF extraction benchmark](https://procycons.com/en/blogs/pdf-data-extraction-benchmark/), [Unstract: Extract tables from PDF (2026)](https://unstract.com/blog/extract-tables-from-pdf-python/)
- Scrapy ecosystem: [Scrapy deployment docs](https://docs.scrapy.org/en/latest/topics/deploy.html), [Spidermon](https://www.zyte.com/blog/spidermon-scrapy-spider-monitoring/), [Deploy Scrapy spiders](https://www.scrapy.org/deploy)
- Java and Go crawlers: [Apache Nutch](https://en.wikipedia.org/wiki/Apache_Nutch), [Apache StormCrawler](https://stormcrawler.apache.org/), [Heritrix](https://en.wikipedia.org/wiki/Heritrix), [Colly](https://github.com/gocolly/colly)
- Address parsing: [libpostal](https://github.com/openvenues/libpostal), [Libpostal, reborn (model refresh)](https://blog.graphlet.ai/libpostal-reborn-41e0539ebe78?gi=45fc8e2738f2)
- Parsing and resilience: [Scrapling adaptive scraping](https://www.scrapingbee.com/blog/scrapling-adaptive-python-web-scraping/), [parsel](https://github.com/scrapy/parsel), [selectolax](https://webscraping.fyi/lib/python/selectolax/)
- HTTP caching: [Hishel](https://hishel.com/), [RFC 9111](https://datatracker.ietf.org/doc/html/rfc9111)
- Open data services: [Socrata Open Data Network](https://dev.socrata.com/data/), [Wikidata SPARQL query limits](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/query_limits), [OpenAlex rate limits and authentication](https://github.com/ourresearch/openalex-docs/blob/main/how-to-use-the-api/rate-limits-and-authentication.md), [OpenAlex API keys announcement](https://groups.google.com/g/openalex-users/c/rI1GIAySpVQ), [OSMF API usage policy](https://operations.osmfoundation.org/policies/api/), [Overpass API wiki](https://wiki.openstreetmap.org/wiki/Overpass_API), [SEC: Accessing EDGAR Data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data), [public-api-lists](https://github.com/public-api-lists/public-api-lists)
