# DataForge presets: where everything lives

A **preset** is one JSON file that tells DataForge how to collect from a kind of page: which
hosts and paths are in scope, the politeness limits, how to render it, which fields to pull, how
to paginate, and how to validate the result. Presets are data, not code — the same engine runs
all of them.

This doc maps **every folder a preset touches** and lists **what each preset is**. For the meaning
of individual JSON fields, see [`docs/Scrapper/Website Preset Specification.md`](docs/Scrapper/Website%20Preset%20Specification.md);
for the record-detail keys a preset produces, see [`docs/Scrapper/Record Details.md`](docs/Scrapper/Record%20Details.md).

---

## 1. Folders that hold a preset's pieces

| Folder | What it holds for a preset |
| --- | --- |
| `packages/presets/` | **The preset definitions.** One `*.json` file per preset, named `<id>@<version>.json` (e.g. `generic.html_list@1.1.0.json`). This is the source of truth. Loaded at startup from here (`Service.presets_dir`, default `packages/presets`). |
| `packages/presets/fixtures/` | **Offline sample pages** used by each preset's health check and by tests, grouped by provider: `generic/`, `hackernews/`, `wikipedia/`, `open_data/`, `documents/`. A preset points at its fixture through `health.fixture_tests`. |
| `packages/contracts/` | **JSON Schemas** the engine and job results are validated against, e.g. `record-details.schema.json`. Not per-preset, but every preset's output is checked here. |
| `workers/scraping/src/dataforge_scraping/` | **The engine that runs a preset.** `presets.py` validates a preset, `extraction.py` pulls records, `details.py` adds record details, `fetch.py`/`signals.py` enforce scope, robots.txt, and politeness, `engines/` are the httpx and Scrapy runners. |
| `workers/scraping/src/dataforge_scraping/data/` | `schemaorg_mappings.json` — the schema.org → canonical-field map used when a preset reads structured data (`detail.*`, `page.*`). |
| `services/application/src/dataforge_application/` | **Loads, validates, saves, and runs presets as jobs.** `scraping.py` (`list_presets`, `save_custom_preset`, the scrape/rendered job kinds), `sources.py` (signal checks, `detect_fields`), `api.py` (the IPC commands the desktop app calls). |
| `apps/desktop/src/features/scraping/` and `.../studio/` | **The UI.** The Scraping tab (pick a preset, run it) and Scrape Studio (build a custom preset by clicking a page). Custom presets are saved to the project database, not to `packages/presets/`. |
| `migrations/` | The `custom_presets` and `preset_packages` tables where **user-made and installed** presets are stored (bundled presets stay as files). |

A single preset therefore = **one file in `packages/presets/`** + **one fixture under `packages/presets/fixtures/`**, run by the engine in `workers/scraping/`, exposed by `services/application/`, and shown in `apps/desktop/`.

---

## 2. What one preset file contains

Every `packages/presets/*.json` has the same top-level blocks:

| Block | Purpose |
| --- | --- |
| `id`, `version`, `display_name`, `page_type`, `description` | Identity. The filename is `<id>@<version>.json`. |
| `status`, `owner`, `maintenance_review_date` | `active` / `deprecated` / `disabled`, who maintains it, when to review. |
| `provider`, `category` | Who the data is from, and the grouping (`generic`, `open_data`, `news_community`, …). |
| `policy` | The rules: collection basis, whether the user must acknowledge authorization, `robots_policy`, and what to do at authentication / CAPTCHA / paywall (never bypass — stop). |
| `request_limits` | Politeness: `max_concurrency`, `min_delay_ms`, `max_pages_default`, `max_records_default`, `max_duration_seconds`. |
| `url_scope` | `allowed_hosts`, `user_supplied_host`, `allowed_path_patterns` — the only URLs this preset may touch. |
| `strategy` | `preferred` and `allowed` collection methods (`api`, `http`, `webview`) and `prohibited_escalations`. |
| `extraction` | `record_root` + `fields` (selectors and transforms), or an API item path. |
| `pagination` | `type` (`none`, `next_link`, `infinite_scroll`, `detail_links`, …) and `stop_conditions`. |
| `validation`, `field_mappings` | Minimum coverage / unique keys, and the canonical entity the output maps to. |
| `health` | `fixture_tests` (the sample page under `fixtures/`) and `expected` (e.g. `minimum_records`). CI runs each preset against its fixture. |

---

## 3. Every bundled preset

22 presets ship in `packages/presets/`. Each row is one file plus the fixture its health check uses.

### Generic — the reusable building blocks (`category: generic`)

| Preset file | Strategy | What it collects | Fixture |
| --- | --- | --- | --- |
| `generic.html_list@1.0.0.json`, `@1.1.0.json` | http | One record per repeated card on an HTML page. | `fixtures/generic/html-list.html` |
| `generic.html_table@1.0.0.json`, `@1.1.0.json` | http | One record per table row. | `fixtures/generic/html-table.html` |
| `generic.structured_data@1.0.0.json` | http | schema.org / JSON-LD entities on a page. | `fixtures/generic/structured.html` |
| `generic.crawl_structured@1.0.0.json` | http | Follows in-scope links, reads structured data on each. | `fixtures/generic/structured.html` |
| `generic.sitemap_structured@1.0.0.json` | http | Structured data for URLs listed in a sitemap. | `fixtures/generic/structured.html` |
| `generic.article@1.0.0.json` | http | Article main text and metadata. | `fixtures/generic/article.html` |
| `generic.sitemap_article@1.0.0.json` | http | Articles for URLs listed in a sitemap. | `fixtures/generic/article.html` |
| `generic.llms_txt@1.0.0.json` | http | Content a site offers via `llms.txt`. | `fixtures/generic/article.html` |
| `generic.feed@1.0.0.json` | http | RSS / Atom feed entries. | `fixtures/generic/feed.xml` |
| `generic.document_tables@1.0.0.json` | http | Tables inside a linked PDF or document. | `fixtures/documents/table.pdf` |
| `generic.json_api@1.0.0.json` | api | Records from a generic JSON endpoint. | `fixtures/generic/api.json` |

### Named data sources (official APIs)

| Preset file | Provider | Category | Fixture |
| --- | --- | --- | --- |
| `ckan.package_search@1.0.0.json` | CKAN | open_data | `fixtures/open_data/ckan.json` |
| `socrata.dataset_rows@1.0.0.json` | Socrata | open_data | `fixtures/open_data/socrata.json` |
| `openalex.works@1.0.0.json` | OpenAlex | open_data | `fixtures/open_data/openalex.json` |
| `wikidata.sparql@1.0.0.json` | Wikidata | open_data | `fixtures/open_data/wikidata.json` |
| `osm.overpass_pois@1.0.0.json` | OpenStreetMap | open_data | `fixtures/open_data/overpass.json` |
| `sec.submissions@1.0.0.json` | U.S. SEC | open_data | `fixtures/open_data/sec_submissions.json` |
| `gdelt.doc_search@1.0.0.json` | GDELT Project | open_data | `fixtures/open_data/gdelt.json` |
| `wikipedia.search_api@1.0.0.json` | Wikipedia | news_community | `fixtures/wikipedia/search.json` |
| `hackernews.search_api@1.0.0.json` | Hacker News | news_community | `fixtures/hackernews/search.json` |

> Two versions of a preset (e.g. `generic.html_list@1.0.0` and `@1.1.0`) can coexist; versions are
> immutable, so a change ships as a new file. Custom presets built in Scrape Studio use ids like
> `custom.local.<name>` and live in the project database, not in this folder.
