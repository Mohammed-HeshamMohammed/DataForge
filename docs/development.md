# DataForge Development

## What runs today

| Layer | Location | Implemented |
| --- | --- | --- |
| Desktop UI | `apps/desktop/src` | React + TypeScript in a frameless window. **Title bar:** theme, updates, minimize, close. **Left sidebar:** review ring, datasets, actions, project card. **Main pane:** clock header, tabs. **Right sidebar:** job center, service status. **Tabs:** Overview, Scraping, Scrape Studio, Datasets, Match & Deduplicate, Settings. |
| Desktop host | `apps/desktop/src-tauri` | Tauri 2. Supervised service sidecar (dev Python or packaged exe), command validation, credential injection, OS credential store, Scrape Studio child WebView + bridge, signature-verified updater, native dialogs. |
| Application services | `services/application` | **Data:** projects with pre-migration backups, tracked migrations, CSV/JSON/XLSX import, content-addressed artifacts, profiling, fuzzy-assisted mapping proposals. **Jobs:** pause/cancel/retry/restart recovery with in-memory secrets. **Scraping:** HTTP/API and rendered-page staging, presets, health checks, signed packages. **Matching:** match jobs, review, split/lock groups, ranking, exports. |
| Scraping worker | `workers/scraping` | HTTP and API strategies, selectors, transforms, pagination, robots.txt, challenge/redirect stops, fixture extraction, preset validation, health checks, Ed25519 package signing. |
| Matching worker | `workers/matching` | Normalization, capped multi-pass blocking, evidence, guards, decisions, constrained clustering with bridge rejection and locks, canonical records, ordering-only ranking model. |
| Presets | `packages/presets` | Generic list/table (1.0.0 deprecated, 1.1.0 WebView-capable), generic JSON/API, Wikipedia search API, Hacker News search API. Fixtures are in `fixtures/`. |
| Packaging and release | `packaging`, `scripts`, `.github/workflows` | PyInstaller service, packaged-service smoke test, NSIS installer with bundled resources, updater artifacts, CI, and a tag-driven release with SHA256SUMS and SBOM. |

## Setup

Requirements: Python 3.11+, Node 20+, Rust/Cargo, and WebView2 on Windows.

```powershell
python -m pip install openpyxl httpx beautifulsoup4 rapidfuzz cryptography pytest
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
npm run test:python        # application, scraping, matching (64+ tests)
npm run typecheck
npm run test:desktop
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

Pushing a `vX.Y.Z` tag runs `.github/workflows/release.yml`. It verifies the tag matches the app version, runs the tests, builds and signs the installer, writes `latest.json`, generates an SBOM, and publishes the GitHub Release. Prerelease tags (`v1.2.0-beta.1`) also update the rolling `beta` release used by the beta channel.

## Signed preset packages

```powershell
python scripts/preset-package.py generate-key --out $env:USERPROFILE\.dataforge\keys\presets.pem
python scripts/preset-package.py build --name vendor-pack --version 1.0.0 --preset my-preset.json --key $env:USERPROFILE\.dataforge\keys\presets.pem --out vendor-pack-1.0.0.dfpreset
```

To trust the printed key, add it to `%LOCALAPPDATA%\DataForge\trusted-preset-keys.json` for one machine, or to `packages/presets/trusted_keys.json` after review. Then install from Settings → Presets.

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
- **Guards:** a conflicting identifier, house number, or unit means "Cannot auto-merge".
- **Clustering** rejects single-link bridges between groups.
- **Review:** decisions become dataset-scoped constraints with optimistic versions and undo.
- **Groups:** splitting adds `must_not_link` constraints, and locking keeps a group exactly as it is; both apply to future runs and can be undone.
- **Ranking:** the model only reorders the queue.
- **Exports:** full jobs write canonical, clean, original, and audit files. Unresolved review blocks export unless the files are explicitly marked not final.

### Scraping

- **Every job** requires the authorization acknowledgement and pins its resolved preset.
- **Test runs** are capped at 10 records. Custom presets need a successful test before a full run.
- **Preset status:** `degraded` presets warn, `disabled` presets are blocked, and `deprecated` presets name a successor.
- **Stops:** 401/403/429-class responses, challenge markers, login forms (Studio), robots.txt disallow, and out-of-scope redirects or navigation all stop collection.
- **API credentials** are resolved by the host from the OS store and never persisted.

### Scrape Studio

1. Load a permitted URL.
2. Pick the repeated item, the fields, and the next-page link.
3. Adjust the selectors and test 10 records.
4. Save the custom preset and run a full multi-page collection in the visible embedded WebView.

The run stages an immutable `scrape` dataset.

### Logs

JSON on stderr. Emails, phone-like numbers, URL query strings, and secret-named keys are redacted.
