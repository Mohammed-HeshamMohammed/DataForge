# DataForge Development

## What runs today

| Layer | Location | Implemented |
| --- | --- | --- |
| Desktop UI | `apps/desktop/src` | React + TypeScript in a frameless window. **Title bar:** theme, updates, minimize, close. **Left sidebar:** review ring, datasets, actions, project card. **Main pane:** clock header, tabs. **Right sidebar:** job center, service status. **Tabs:** Overview, Scraping, Scrape Studio, Datasets, Match & Deduplicate, Settings. |
| Desktop host | `apps/desktop/src-tauri` | Tauri 2. Supervised service sidecar (dev Python or packaged exe), command validation, credential injection, OS credential store, Scrape Studio child WebView + bridge, signature-verified updater, native dialogs. |
| Application services | `services/application` | **Data:** projects with pre-migration backups, tracked migrations, CSV/JSON/XLSX import, content-addressed artifacts, profiling, fuzzy-assisted mapping proposals. **Jobs:** pause/cancel/retry/restart recovery with in-memory secrets. **Scraping:** HTTP/API and rendered-page staging, presets, health checks, signed packages. **Matching:** match jobs, review, split/lock groups, ranking, exports. |
| Scraping worker | `workers/scraping` | **Engines:** in-process httpx runtime and a Scrapy child process with identical extraction. **Sources:** pages with pagination, sitemaps, feeds, scoped crawls, llms.txt, open data APIs with request templates, PDFs and linked files, Wayback and Common Crawl, Web Data Commons N-Quads. **Extraction:** CSS/XPath selectors (BeautifulSoup, parsel, selectolax), schema.org structured data, article text, PDF tables, normalizers. **Policy:** robots.txt (RFC 9309), TDMRep, AIPREF, ai.txt, per-host politeness, HTTP cache, challenge and redirect stops. **Maintenance:** fingerprints, relocation and example-based suggestions, drift, diffs, draft proposals, WARC capture. |
| Matching worker | `workers/matching` | Normalization, capped multi-pass blocking, evidence, guards, decisions, constrained clustering with bridge rejection and locks, canonical records, ordering-only ranking model. |
| Presets | `packages/presets` | Generic list/table (1.0.0 deprecated, 1.1.0 WebView-capable), generic JSON/API, Wikipedia search API, Hacker News search API. Fixtures are in `fixtures/`. |
| Packaging and release | `packaging`, `scripts`, `.github/workflows` | PyInstaller service, packaged-service smoke test, NSIS installer with bundled resources, updater artifacts, CI, and a tag-driven release with SHA256SUMS and SBOM. |

## Setup

Requirements: Python 3.11+, Node 20+, Rust/Cargo, and WebView2 on Windows.

```powershell
python -m pip install -r requirements-dev.txt
npm run setup:desktop
```

## Run

```powershell
npm run dev:tauri          # desktop app; the host starts the Python service automatically
```

- `DATAFORGE_PYTHON` picks the interpreter.
- `DATAFORGE_ROOT` points at a checkout.
- `DATAFORGE_APP_DATA` relocates recent projects and trusted preset keys.

UI-only development in a browser (no Studio, credentials, or updates):

```powershell
npm run dev:bridge         # service on http://127.0.0.1:8765 (loopback only)
npm run dev                # Vite on http://127.0.0.1:1420, proxies /rpc
```

## Checks

```powershell
npm run test:python        # application, scraping, matching: property, contract, golden-file, migration tests
npm run typecheck
npm run test:desktop       # node:test units + Vitest component and axe-core accessibility specs
npm run build
cd apps/desktop/src-tauri; cargo test   # sidecar round trip, OS credential store, Studio guards, update pinning
```

## Release build

```powershell
.\scripts\build-release.ps1
```

The build:

1. Runs the tests.
2. Builds `build/service/dataforge-service/` with PyInstaller.
3. Smoke-tests that executable with Python removed from `PATH`: project, import, matching, presets, and health checks.
4. Builds the NSIS installer with `tauri.release.conf.json`, which bundles `service/`, `migrations/`, and `packages/presets/` as resources.
5. Writes `SHA256SUMS.txt`.

Updater artifacts are signed with `TAURI_SIGNING_PRIVATE_KEY`, taken from `%USERPROFILE%\.dataforge\keys\updater.key` locally or from environment secrets in CI.

Releases run from `.github/workflows/release.yml` in two ways:

- **Tag:** push `vX.Y.Z` (or `vX.Y.Z-beta.1`) and the workflow builds and publishes that version.
- **Manual:** GitHub > Actions > Release > Run workflow, then enter the version to build. Options: the branch or commit to build (default `main`), whether to publish a GitHub Release (creates tag `v<version>`; off keeps the installer as a workflow artifact only), whether to sign (off builds an unsigned installer with no auto-update manifest), and whether to run the tests first.

The requested version is stamped into every version file at build time by `python scripts/set-version.py <version>`, so nothing needs to be bumped and committed first. `python scripts/set-version.py --check` (also run in CI) fails if the version files disagree, and the UI shows the version from `apps/desktop/package.json`. The workflow runs the tests, builds and signs the installer, writes `latest.json`, generates an SBOM, and publishes the release. Prerelease versions (`1.2.0-beta.1`) are marked as prereleases and also update the rolling `beta` release used by the beta channel. Unsigned builds are always marked as prereleases and never update the beta manifest.

Signed builds run in the `release` GitHub environment (add required reviewers there and store `TAURI_SIGNING_PRIVATE_KEY` and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` as environment secrets). Unsigned builds run in `release-unsigned`.

## Signed preset packages

```powershell
python scripts/preset-package.py generate-key --out $env:USERPROFILE\.dataforge\keys\presets.pem
python scripts/preset-package.py build --name vendor-pack --version 1.0.0 --preset my-preset.json --key $env:USERPROFILE\.dataforge\keys\presets.pem --out vendor-pack-1.0.0.dfpreset
```

To trust the printed key, add it to `%LOCALAPPDATA%\DataForge\trusted-preset-keys.json` for one machine, or to `packages/presets/trusted_keys.json` after review. Then install from Settings → Presets.

## Command line

```powershell
$env:PYTHONPATH = "services/application/src;workers/matching/src;workers/scraping/src"
python -m dataforge_application.cli --create --project C:\data\demo --name Demo health.check
python -m dataforge_application.cli --project C:\data\demo --wait dataset.import '{"path": "C:\\data\\leads.csv"}'
```

The CLI uses the same command API as the desktop app. With `--wait`, it follows a started job to its end. Service logs are JSON on stderr and are also written to a rotating `%LOCALAPPDATA%\DataForge\logs\service.log` (5 MB × 3, redacted); set `DATAFORGE_LOG_FILE=0` to disable the file.

## Behavior notes

### Imports and mapping

- **Imports** run as jobs. The original file is copied to `artifacts/sha256/<ab>/<hash>`.
- **Headers:** blank CSV/XLSX headers become `column_<n>`, and repeated headers get `(2)`, `(3)` suffixes.
- **Row numbers:** blank lines keep original row numbers.
- **Mapping proposals:** only whole-header matches are proposed. Partial matches, and strict fuzzy matches (score ≥ 92, for typos), are `ambiguous`, default to `other`, and must be confirmed.

### Jobs and projects

- **Job states:** `draft -> validating -> queued -> running -> paused/completed/failed/cancelled`.
- **Checkpoints:** pause and cancel are honored at safe checkpoints.
- **Output:** results are published in one transaction.
- **Restart:** active jobs from a crash become `failed` on project open and can be retried.
- **Backups:** opening a project with pending migrations first writes `backups/dataforge-before-<migration>-<time>.sqlite3`.

### Matching

- **Full runs** need a completed preview with the same mapping version and settings.
- **Auto-match** needs an identifier match, or strong evidence plus a score at or above the threshold and at least two comparable fields.
- **Guards:** a conflicting identifier, house number, or unit means "Cannot auto-merge". For people, so does a different first name (household members share phones, addresses, and surnames), unless the records share an email address, which sends the pair to review.
- **People:** names are compared as given names and surname: nicknames (Peggy for Margaret), short forms, typos, initials (including a nickname's initial, B. for Robert), "Last, First" order, titles, and suffixes. An initial alone counts for less, so Safer mode reviews it. Gmail dots and +tags at major providers are ignored when comparing emails.
- **Clustering** rejects single-link bridges between groups.
- **Review:** decisions become dataset-scoped constraints with optimistic versions and undo.
- **Groups:** splitting adds `must_not_link` constraints, and locking keeps a group exactly as it is; both apply to future runs and can be undone.
- **Ranking:** the model only reorders the queue.
- **Comparison scope:** a job can compare against a second dataset. The trusted source supplies the survivor; other sources only fill empty fields. Canonical exports add `canonical.<role>` fields, and `original.csv` and `clean.csv` add `source_dataset`.
- **Reviewer tools:** "Choose values" picks which record supplies each differing field during a merge, and groups allow per-field canonical choices; both can be undone. "Mark bad mapping" flags a field for the mapping screen. "Exclude from export" removes a column from files but keeps it as evidence.
- **Exports:** full jobs write canonical, clean, original, and audit files. Unresolved review blocks export unless the files are explicitly marked not final.

### Scraping

- **Automatic mode** inspects one policy-permitted response, detects feeds, sitemaps, OAI-PMH, JSON APIs, data files, structured records, repeated cards, or article pages, then selects a bundled preset or saves a versioned host-scoped preset.
- **Every job** requires the authorization acknowledgement and pins its resolved preset.
- **Test runs** are capped at 10 records. Custom presets need a successful test before a full run.
- **Preset status:** `degraded` presets warn, `disabled` presets are blocked, and `deprecated` presets name a successor.
- **Stops:** 401/403/429-class responses, challenge markers, login forms (Studio), robots.txt disallow, and out-of-scope redirects or navigation all stop collection.
- **API credentials** are resolved by the host from the OS store and never persisted.

### Scrape Studio

1. Load a permitted URL.
2. Pick the repeated item and the fields, then choose pagination: next-page link, infinite scroll, or detail links (one record per item page).
3. Adjust the selectors and test 10 records.
4. Save the custom preset and run a full multi-page collection in the visible embedded WebView.

The run stages an immutable `scrape` dataset.

### Menus and shortcuts

Every action is in the title-bar menus and the command palette (Ctrl+K). Help → Keyboard shortcuts (Ctrl+/) lists them all. The command catalog is in `apps/desktop/src/app/App.tsx`, and the plan's Pillar F documents it.

### Collection sources and live checks

- **Sources:** Settings → Scraping offers Automatic, Website, Sitemap, Feed, Site crawl, Open data API, Research repository (OAI-PMH), Documents, Web archive, and Bulk corpus. Every job declares a purpose; site signals are applied to it (decision D18).
- **Research APIs:** curated presets cover Crossref scholarly works with cursor pagination and GLEIF legal entities with JSON:API next links. Direct CSV is parsed in the collection path; direct JSON/XLS/XLSX is staged through the normal importer.
- **Engines:** `engine: auto` uses Scrapy for sitemap and crawl jobs above 200 pages. `python -m dataforge_scraping.engines.scrapy_engine <job.json>` runs the child directly for debugging.
- **Settings → Collection:** contact identity (required by SEC EDGAR and Wikidata), default purpose, HTTP cache, WARC capture with retention, and suggestion provider.
- **Licenses:** `python scripts/check-licenses.py` must pass before a release build.
- **Scrape Studio:** collections need a purpose; each automated navigation is checked against site signals (decision D24). Picked fields carry XPath fallbacks. Pages with schema.org data offer "Use structured data".
- **Maintenance:** failing health checks show suggested selectors; Settings → Presets can check a preset against a live page; a run with page capture on can save a sanitized fixture (`project:fixtures/...`).
- **Archives:** the Wayback source can compare each page's earliest and latest capture and report field changes.
- **Optional extras:** `pip install -e "workers/scraping[ocr]"` (plus the Tesseract program) for scanned PDFs, `[tables]` for camelot, `[docling]`, and `[language]`.
- **Live verification:** `python scripts/live-check.py [--only signals,crawl_engines,...]` runs every source type against permitted public sites with small caps in a throwaway project and prints counts only. It is never part of CI.
- **UI sandbox:** `powershell -File scripts/dev-bridge-sandbox.ps1` starts the dev bridge with empty app data, so browser previews never open real projects.

### Logs

JSON on stderr. Emails, phone-like numbers, URL query strings, and secret-named keys are redacted.
