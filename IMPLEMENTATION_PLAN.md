# DataForge Implementation Plan

**Status:** All phases implemented in code; only production publishing steps remain (see phase 10)
**Date:** 2026-09-13 (status updated 2026-09-16)

This document converts the DataForge architecture, scraping, preset, matching, and UI specifications into an executable implementation roadmap.

## Implementation Status

| Phase | Status | Notes |
| --- | --- | --- |
| 0 — Bootstrap | Done | Frameless Tauri window: custom title bar, left sidebar, main pane, right job-center sidebar. Typed IPC, dev scripts. Decisions are in `docs/decisions.md`. |
| 1 — Contracts, storage, durable jobs | Done | Tracked migrations with pre-migration backups, versioned envelope (`docs/ipc-contract.md`), job runner (pause/cancel/retry/restart recovery), stage events, job center, redacted logs, OS credential store with host-side injection and in-memory job secrets (D9). |
| 2 — Dataset import and staging | Done | CSV/JSON/XLSX import jobs, content-addressed artifacts, profiling, whole-word then strict fuzzy (≥92) proposals that require confirmation, immutable mapping versions, sensitive-value masking, deletion. |
| 3 — Generic scraping engine | Done | Preset validation and status gating, user-host scoping, acknowledgement, limits, robots, challenge/redirect stops, transforms/types, all HTTP pagination modes (`next_link`, `page_parameter`, `cursor`, `api_cursor`, `detail_links`), authorized API credentials, test mode, staged datasets. |
| 4 — Embedded WebView and Scrape Studio | Done | Native incognito child WebView, allow-listed bridge, host-enforced navigation scope, repeated-item/element/next-page/detail-link pickers with relative selectors, next-link, infinite-scroll, and detail-link collection, 10-record tests that use the same pagination as full runs, derived custom presets, login/challenge stops (D7). All modes verified in the running desktop app. |
| 5 — Deterministic matching | Done | Normalization, multi-pass blocking with caps, evidence, guards, decisions, reproducible output. Within-dataset or cross-dataset scope with per-dataset mappings (D14). |
| 6 — Clustering, canonicalization, exports | Done | Constrained union-find with bridge rejection and locks. Survivor order: locked, trusted source, verified fields, completeness, earliest row. Reviewer-chosen canonical values, role-based canonical fields for cross-dataset output, field provenance, export exclusions for sensitive columns, four exports with hashes and final/unresolved marking, full audit trail. |
| 7 — Review queue and UI | Done | 5-step tab with comparison scope and source trust, preview staleness, optimistic versions, undo, shortcuts, masking, choose values before merging, mark bad mapping, split/lock/unlock groups, per-field canonical choices, run metrics, responsive layout. |
| 8 — Quality and performance | Done | 115 tests: 88 Python (including Hypothesis property tests, JSON-schema contract tests, a golden-file export test, and a schema-004 upgrade test), 6 Node unit, 12 component and axe-core accessibility, 9 Rust. Plus a packaged-service smoke test and CI. Metrics cover stage timings, rows per second, group sizes, decisions by scope, survivor sources, and review turnaround. Measured 48.5k rows in about 3.4 s. Colour-contrast checks need a real browser and remain manual. |
| 9 — Curated presets and operations | Done | Wikipedia and Hacker News documented-API presets (D13). Fixture health checks with degrade/disable, deprecation with successors, Ed25519-signed packages with install/rollback, custom preset import/export, signing CLI. Amazon/Zillow are intentionally not shipped. |
| 10 — Secure updates and release | Done (unpublished) | PyInstaller service, NSIS installer with bundled resources, signed updater artifacts, GitHub-pinned update checks with approval and an active-job block, CI and tag-driven release workflows (SHA256SUMS, SBOM, beta channel). Production steps left to the owner: add the `release` environment secrets, choose repository visibility for update checks, and optionally add Authenticode code signing. |
| 11 — ML and scale | Done (ordering only) | Review-queue ranking from human labels with holdout metrics and version metadata (D12). ML auto-merge and sharding stay out by design until evaluation gates and measured load justify them. |

## 1. Product Goal

Build a local-first desktop application that can:

- Import and collect permitted data.
- Extract data through versioned website and page-type presets.
- Render JavaScript-dependent pages only inside DataForge's visible embedded WebView.
- Preserve immutable raw inputs and complete provenance.
- Match and deduplicate records conservatively and explainably.
- Route uncertain decisions to human review.
- Produce canonical, clean, original, and audit exports.
- Update safely through verified GitHub release artifacts.

The UI must coordinate these workflows but must not contain scraping, matching, persistence, or transformation logic.

## 2. Source Specifications

The implementation is governed by:

- `docs/architecture.md`
- `docs/Scrapper/Scrapper.md`
- `docs/Scrapper/Website Preset Specification.md`
- `docs/Matching and Deduplication/Matching and Deduplication.md`
- `docs/Matching and Deduplication/Matching & Deduplication Tab.md`
- `README.md`

The architecture document is the source of truth if two specifications disagree.

## 3. Target Architecture

### Presentation layer

- React and TypeScript inside Tauri.
- Navigation, forms, accessibility, local view state, and event rendering.
- No direct database, filesystem, worker, or network access.

### Desktop host

- Rust and Tauri.
- Main WebView and native child WebViews.
- Typed IPC validation.
- Project paths, process lifecycle, file dialogs, OS notifications, credential-store access, and updater integration.
- No business-specific selectors, matching scores, or record transformations.

### Application layer

Services own orchestration and project-state mutation:

- `ProjectService`
- `DatasetService`
- `JobService`
- `PresetService`
- `ScrapeService`
- `MatchService`
- `ExportService`
- `PolicyService`
- `UpdateService`
- `EventService`

### Workers

- Scraping worker: Python, Scrapy/HTTPX, Parsel/lxml or Selectolax, and embedded-WebView bridge integration.
- Matching worker: Python, Polars, RapidFuzz, and optional later scikit-learn model support.
- Workers communicate using versioned request/result schemas and durable job state.

### Infrastructure

- SQLite with ordered, idempotent migrations.
- Per-project content-addressed artifact storage.
- Operating-system credential store.
- Structured local logs and SQLite job events.
- Signed preset packages and release metadata.

## 4. Non-Negotiable Rules

- Preserve raw imported and scraped data; never overwrite it during matching or export.
- Store exact preset, mapping, policy, model, software, and migration versions with jobs.
- Use durable backend job events as the source of progress.
- Use authorized APIs first, permitted HTTP/HTML second, and embedded WebView rendering only when needed and allowed.
- Never bypass CAPTCHA, login walls, paywalls, access denial, rate limits, robots restrictions, or anti-bot controls.
- Do not launch an external browser for rendered-page collection.
- Do not use an HTML iframe as the Scrape Studio child WebView.
- Do not auto-merge using name or region similarity alone.
- Hard contradictions must prevent automatic merges.
- Uncertain matches must enter review and must not be silently merged.
- ML may rank review items but cannot be the sole authority for destructive automatic merges.
- Do not install an update until its manifest and artifact signatures and checksums are verified.

## 5. Junior Engineer Execution Guide

This section explains how to begin the work. Follow the phases in order. Do not start by implementing website-specific selectors or matching heuristics; those depend on contracts and storage that must exist first.

### 5.1 Required Directory Structure

Create the following structure as the implementation grows:

```text
DataForge/
|-- apps/
|   `-- desktop/                         React + TypeScript UI
|       |-- src/
|       |   |-- app/                     App shell, routes, providers
|       |   |-- features/
|       |   |   |-- dashboard/
|       |   |   |-- scraping/
|       |   |   |-- datasets/
|       |   |   |-- matching/
|       |   |   |-- pipelines/
|       |   |   `-- settings/
|       |   |-- components/               Shared UI components
|       |   |-- lib/                      Typed IPC client and utilities
|       |   `-- styles/                   Design tokens and global styles
|       `-- tests/                        UI and accessibility tests
|
|-- crates/
|   `-- desktop-host/                     Tauri/Rust host
|       |-- src/
|       |   |-- commands/                 IPC command handlers
|       |   |-- webview/                  Child WebView and bridge
|       |   |-- process/                  Worker lifecycle management
|       |   |-- filesystem/               Safe project paths and dialogs
|       |   |-- updater/                  Verified update installation
|       |   `-- credentials/               OS credential-store adapter
|       `-- tests/
|
|-- services/
|   `-- application/                      Application orchestration layer
|       |-- src/
|       |   |-- projects/
|       |   |-- datasets/
|       |   |-- jobs/
|       |   |-- presets/
|       |   |-- scraping/
|       |   |-- matching/
|       |   |-- exports/
|       |   |-- policy/
|       |   |-- updates/
|       |   |-- events/
|       |   `-- storage/                   SQLite repositories and artifacts
|       `-- tests/
|
|-- workers/
|   |-- scraping/                         Python scraping worker
|   |   |-- src/
|   |   |   |-- adapters/                  API, HTTP, and WebView adapters
|   |   |   |-- extraction/                Selectors, transforms, validation
|   |   |   |-- pagination/
|   |   |   |-- policies/
|   |   |   `-- runtime/
|   |   `-- tests/
|   `-- matching/                          Python matching worker
|       |-- src/
|       |   |-- profiling/
|       |   |-- mapping/
|       |   |-- normalization/
|       |   |-- blocking/
|       |   |-- evidence/
|       |   |-- clustering/
|       |   |-- canonicalization/
|       |   `-- exports/
|       `-- tests/
|
|-- packages/
|   |-- contracts/                         Shared command/event/data schemas
|   |-- presets/                           Bundled preset packages and fixtures
|   `-- test-data/                         Sanitized datasets and golden files
|
|-- migrations/                            Ordered SQLite migrations
|-- docs/                                  Architecture and feature specifications
|-- scripts/                               Development and release automation
|-- IMPLEMENTATION_PLAN.md
`-- README.md
```

Do not move business logic into `apps/desktop` or `crates/desktop-host` to make a feature seem faster. The desktop layers should call application services through typed contracts.

### 5.2 Ownership Rules

| Code location | May do | Must not do |
| --- | --- | --- |
| React UI | Render state, validate basic form input, call typed commands | Query SQLite, read project files, scrape, score, merge, or decide policy |
| Rust host | Validate IPC, manage windows/processes/filesystem/credentials | Calculate match scores or contain website selectors |
| Application services | Validate requests, orchestrate jobs, mutate project state, publish events | Implement UI-specific state or bypass repositories |
| Scraping worker | Fetch through allowed strategies, extract, validate, stage rows | Bypass access controls or overwrite canonical data |
| Matching worker | Normalize, block, score, cluster, canonicalize, explain | Change raw rows or make unreviewed destructive decisions |
| Contracts package | Define compatible schemas and enums | Contain runtime business behavior |
| Preset package | Declare scope, policy, fields, limits, pagination, fixtures | Broaden permissions or hide executable arbitrary scripts |

### 5.3 Packaging Contract: UI, Rust, Python, and Resources

The distributable desktop application must package all required runtime pieces together. A Tauri build is not complete merely because the Rust binary compiles.

The final installed application must contain or reliably locate:

- The compiled React/TypeScript frontend assets.
- The compiled Rust/Tauri desktop host.
- The packaged scraping worker.
- The packaged matching worker.
- SQLite migration files.
- Bundled preset packages and schema files.
- Required static assets, icons, and release metadata.

The operating system WebView runtime is a platform dependency. It may be supplied by Windows WebView2 or the equivalent supported runtime; it should not be assumed that the application can use an arbitrary browser installed by the user.

#### Recommended packaging model

1. Build the React UI into the Tauri frontend distribution directory.
2. Build the Rust host with Tauri bundling enabled.
3. Build each Python worker as a standalone executable with PyInstaller or an equivalent supported bundler.
4. Produce one worker binary per supported operating system and architecture.
5. Declare the worker binaries, migrations, presets, schemas, and static assets as Tauri bundle resources.
6. Resolve resource paths through Tauri's runtime resource directory, never through the source checkout or the current working directory.
7. Start workers as managed child processes from the Rust host.
8. Pass job requests through the versioned worker contract and capture worker stdout/stderr through structured logging with redaction.
9. Stop and clean up worker processes when jobs are cancelled or the application exits.
10. Fail startup with an actionable diagnostic if a required worker or resource is missing or has the wrong version.

#### Expected installed layout

The exact platform layout is controlled by Tauri, but the logical resource layout should remain stable:

```text
installed-app/
|-- application executable             Rust/Tauri host
|-- resources/
|   |-- workers/
|   |   |-- scraping-worker[.exe]
|   |   `-- matching-worker[.exe]
|   |-- migrations/
|   |-- presets/
|   |-- contracts/
|   `-- release/
|       `-- compatibility.json
`-- frontend assets                     React build output, bundled by Tauri
```

Do not require users to install Python, Node.js, Rust, or the project source code to run a released application. Development mode may execute Python modules directly, but release mode must use the packaged worker executables.

#### Version and compatibility checks

At startup and before each job, verify:

- Application version.
- Worker executable version.
- Contract schema version.
- Minimum and target database schema versions.
- Preset package compatibility.
- Worker platform and architecture.

The host must refuse to start a worker when its contract version is incompatible. The diagnostic should identify the expected and discovered versions without exposing credentials or user data.

#### Build matrix and verification

The release pipeline must build and test every supported target separately, for example:

```text
Windows x86_64  -> Tauri installer + Windows worker executables
macOS target    -> Tauri bundle + macOS worker executables
Linux target    -> Tauri bundle + Linux worker executables
```

The first release may support Windows only, but the packaging boundaries must not hard-code Windows paths or executable names into application logic.

For every built installer, run an installed-artifact smoke test that:

1. Installs the application on a clean test machine or environment.
2. Opens the desktop UI without a development toolchain installed.
3. Creates or opens a project.
4. Starts a fake job and confirms host-to-worker communication.
5. Imports a fixture dataset.
6. Runs a fixture-backed scrape and matching job.
7. Applies migrations from the previous supported version.
8. Confirms that bundled presets, workers, and exports are found through runtime resource paths.
9. Uninstalls or rolls back without deleting user project data.

Packaging is complete only when the installed artifact passes these checks. A successful development run from the repository is not sufficient evidence.

### 5.4 How to Start a Task

For every task, write down these five items before coding:

1. The user-visible behavior being added or corrected.
2. The owning layer and exact module.
3. The input and output contract.
4. The failure and privacy behavior.
5. The narrowest test that proves the task works.

Then implement in this order:

1. Add or update the shared schema.
2. Add the application service behavior.
3. Add the worker or host implementation.
4. Add the UI integration.
5. Add unit, contract, and failure tests.
6. Run formatting, linting, type checks, and the focused test.

Do not begin a later phase until the previous phase's exit criteria and tests pass. When a requirement is ambiguous, record the decision in `docs/` and keep the contract explicit instead of guessing inside a component.

### 5.5 First Implementation Tasks

Complete these tasks in order during the initial setup:

1. Confirm installed versions of Node, Rust, Python, and the package managers.
2. Create the workspace directories and minimal build files.
3. Create a Tauri window that loads the React application.
4. Add one typed `health.check` IPC command and an integration test.
5. Add the contract package with `schema_version` fields.
6. Add the first SQLite migration for projects and jobs.
7. Implement project create/open and a filesystem path validator.
8. Implement a fake long-running job that emits durable stage events.
9. Display that job in the global job center.
10. Restart the application and verify that the job state is recovered from SQLite.

Use a fake job before a real scraper or matcher. This proves the host, application service, worker lifecycle, database, event stream, and UI contract independently.

### 5.6 Definition of Done

A task is done only when:

- The behavior is implemented in its owning layer.
- The public contract is versioned if it crosses a process boundary.
- Success, cancellation, failure, and retry behavior are defined.
- Sensitive values are not written to logs or unintended UI accessibility labels.
- Raw data and provenance are preserved where applicable.
- Focused tests pass.
- Formatting, linting, and type checks pass.
- The change is documented if it changes setup, contracts, migrations, or user behavior.

### 5.7 Daily Engineering Checks

Before opening a pull request, run the repository's equivalent commands for:

```text
format
lint
typecheck
unit tests
contract tests
integration tests for the changed slice
migration tests when storage changed
```

Keep pull requests focused on one vertical slice. Do not combine a database migration, a UI redesign, and a new website preset in one change. Include fixture data instead of depending on live websites in automated tests.

### 5.8 Recommended Branch and Commit Units

Use small changes that can be reviewed independently:

1. `contracts: add project and job schemas`
2. `storage: add initial project and job migrations`
3. `jobs: persist state transitions and events`
4. `desktop: show durable job progress`
5. `datasets: import and profile CSV files`
6. `scraping: add generic fixture-backed HTML extraction`
7. `matching: add deterministic normalization and blocking`
8. `matching: add review decisions and canonical exports`

Do not commit generated artifacts, credentials, cookies, raw website responses, or real personal data.

## 6. Phase 0 - Decisions and Project Bootstrap

### Objectives

Resolve the few infrastructure decisions that otherwise create rework, then create the workspace skeleton and shared conventions.

### Tasks

- Confirm the Tauri child-WebView/WebView2 API and lifecycle model.
- Decide whether rendering sessions are isolated per job or per workspace.
- Confirm Tauri IPC as the desktop boundary and a local worker API as the application-to-Python boundary.
- Define repository structure:
  - `apps/desktop`
  - `crates/desktop-host`
  - `services/application`
  - `workers/scraping`
  - `workers/matching`
  - `packages/contracts`
  - `packages/presets`
- Select Python, Node, Rust, and package-manager versions.
- Establish formatting, linting, type-checking, and test commands.
- Define project directory and artifact naming conventions.
- Define privacy classification and log-redaction rules.
- Correct README links that currently point to filenames not present at the repository root.

### Deliverables

- Working buildable desktop shell.
- Initial package and service structure.
- Development setup documentation.
- Decision records for WebView and IPC boundaries.

### Exit criteria

The application opens a Tauri window, the UI can call one typed host command, and the project builds in a clean checkout.

## 7. Phase 1 - Shared Contracts, Storage, and Durable Jobs

### Tasks

Define versioned schemas for:

- Commands and command results.
- Event envelopes.
- Projects and settings.
- Datasets and source rows.
- Presets and preset health.
- Mappings and normalized values.
- Scrape jobs and match jobs.
- Review decisions and constraints.
- Clusters and canonical records.
- Export artifacts and errors.

Implement:

- SQLite migrations.
- Project create/open services.
- Content-addressed artifact storage.
- Immutable source-file registration.
- Job state machine:
  `draft -> validating -> queued -> running -> paused/completed/failed/cancelled`
- Durable state-transition events.
- Pause, cancel, retry, and restart recovery.
- Structured logs with sensitive-value redaction.
- Credential-store abstraction.
- Global job center and basic error handling.

### Exit criteria

A project can be created, reopened, and recovered after application restart. A long-running test job reports durable progress and cannot publish an incomplete final artifact.

## 8. Phase 2 - Dataset Import and Staging

### Tasks

- Import CSV, JSON, and XLSX files.
- Hash each source artifact.
- Preserve source row numbers and exact raw values.
- Register immutable datasets and staged scrape outputs.
- Profile headers, types, null rates, cardinality, and safe sample values.
- Detect likely entity types and field roles using whole-word matching first.
- Use strict fuzzy header matching only when safe, with a default threshold of at least 92.
- Store proposed and confirmed mappings as immutable versions.
- Identify ambiguous mappings and require confirmation.
- Support sensitive-field classification and export exclusion without removing matching evidence.
- Expose dataset samples and provenance.
- Provide deletion controls for datasets, cached artifacts, and exports.

### Data contract baseline

Each source row must retain:

- Immutable row ID.
- Dataset ID.
- Source artifact hash.
- Original row number.
- Exact raw values.
- Mapped values.
- Normalized values when available.
- Source URL and extraction timestamp when scraped.
- Creation timestamp.

### Exit criteria

A user can import a file, inspect its profile, confirm a mapping, reopen the project, and verify that the original values remain unchanged.

## 9. Phase 3 - Generic Scraping Engine

Build against sanitized fixtures before adding live website presets.

### Preset foundation

- Validate the complete preset schema.
- Support provider, page type, semantic version, URL scope, policy, strategy, limits, extraction, pagination, validation, mappings, and health sections.
- Resolve exact immutable preset versions.
- Support active, degraded, deprecated, and disabled states.
- Reject disabled presets for new jobs.
- Store parent preset references for derived presets.

### Policy and strategy

- Validate allowed hosts and path patterns before a job starts.
- Require explicit permitted-use acknowledgement.
- Enforce concurrency, delay, page, record, and duration limits.
- Select strategies in this order:
  1. Authorized documented API.
  2. Permitted HTTP/HTML.
  3. Embedded WebView rendering when allowed and required.
- Stop immediately on access challenges or prohibited conditions.
- Record the selected strategy and human-readable rationale.

### Extraction runtime

- Generic HTML list, detail, table, and JSON/API presets.
- Record roots or API item paths.
- CSS/XPath selector fallbacks.
- Approved attributes only: text, href, src, and preset-approved attributes.
- Standard transforms and typed output.
- Required-field and record-level validation.
- Uniqueness checks and duplicate suppression within a run.
- Pagination modes:
  `none`, `next_link`, `page_parameter`, `cursor`, `infinite_scroll`, `api_cursor`, and `detail_links`.
- Stop on limits, repeated canonical URLs, repeated responses, missing continuation, or no-new-record conditions.

### Test mode and staging

- Test mode must be capped at 10 records.
- Preview extracted values, warnings, field coverage, pagination, and strategy.
- Require a successful bounded test before a full custom-preset run.
- Write completed output to an immutable staged dataset with scrape provenance.

### Exit criteria

A fixture-backed generic scrape can run in test mode and full mode, report validation and policy failures, persist job events, and produce staged rows with complete provenance.

## 10. Phase 4 - Embedded WebView and Scrape Studio

### Desktop bridge

Implement a native child WebView owned by the Tauri host. It must not be an iframe.

The bridge should expose only bounded commands for:

- Navigation to permitted URLs.
- Page-ready notification.
- Reload, stop, and cancellation.
- Limited DOM inspection.
- Element selection.
- Repeated-item selection.
- Detail-link selection.
- Pagination selection.
- Sample extraction.

The bridge must redact or refuse:

- Passwords.
- Form values.
- Cookies.
- Tokens and session identifiers.
- Hidden sensitive fields.
- Arbitrary script injection.
- Inaccessible cross-origin frames.

### Scrape Studio UI

- Visible page preview.
- Control rail with URL, preset/version, strategy rationale, limits, and policy state.
- Pick element, repeated item, detail link, and next-page actions.
- Relative selectors rooted at `record_root` where possible.
- Field attribute, transform, type, requiredness, mapping, and sample values.
- Selector editing inside schema and policy limits.
- Test 10 records action.
- Save custom preset as a draft or derived preset.
- Start, pause, and cancel actions.
- Real-time extraction and pagination events.

### Exit criteria

A user can select a repeated record and fields from a permitted rendered page, review generated selectors and sample values, run a bounded test, and save a schema-valid custom preset.

## 11. Phase 5 - Deterministic Matching Backend

Use Polars for tabular operations and RapidFuzz for deterministic similarity.

### Import and normalization

- Confirm entity type and mapping before matching.
- Normalize role-specific values while retaining raw values.
- Support identifiers, phones, emails, addresses, names, regions, URLs, and unknown fields.
- Preserve leading zeros and email `@` characters.
- Do not apply provider-specific email alias rules globally.
- Treat missing values as no evidence.
- Treat positional address fields separately; do not pool unrelated address columns.

### Candidate generation

- Generate multiple blocking passes.
- Support provider ID, phone, email, address, product, business, person, and source URL blocks.
- Union duplicate pairs from multiple blocks.
- Record all block IDs producing a candidate.
- Enforce `max_block_size` of 200 by default.
- Split or diagnose oversized blocks instead of performing quadratic comparisons.
- Never block solely on a city, county, common surname, category, or generic title.

### Evidence and decisions

- Compare structured values before fuzzy text.
- Produce field-level evidence and human-readable explanations.
- Start with documented entity-specific weights and thresholds.
- Enforce hard contradictions before automatic matching.
- Require strong deterministic evidence for automatic matches.
- Produce `match`, `possible_match`, `non_match`, and `rejected` decisions.
- Persist policy and configuration versions with every decision.

### Exit criteria

A dataset can run a preview that reports candidate counts and decisions without modifying source data. The same inputs and versions reproduce the same output.

## 12. Phase 6 - Safe Clustering, Canonicalization, and Exports

### Clustering

- Use union-find only with compatibility checks.
- Reject conflicting exact provider IDs, emails, phones, or house numbers.
- Require direct strong evidence for new cluster members.
- Reject ambiguous transitive bridges.
- Preserve cluster status and confidence.

### Survivor and canonical records

Select survivors in this order:

1. User-locked record.
2. Trusted-source priority.
3. Valid identifiers and verified contact fields.
4. Field completeness.
5. Most recently verified value when source trust is equal.
6. Earliest source row ID as the final deterministic tie-breaker.

Build canonical values field by field. Store source row, source dataset, rule, normalization, and timestamp for each selected value. Preserve conflicts in the audit history.

### Exports

Implement:

- Canonical dataset.
- Clean survivor rows.
- Original immutable rows.
- Audit report.

Exports must include provenance by default and may include cluster ID, member count, confidence, source row IDs, and field provenance. Do not mark partial or stale output as final.

### Exit criteria

A completed job produces reproducible canonical, clean, original, and audit artifacts while leaving the source dataset intact.

## 13. Phase 7 - Review Queue and Matching UI

### Backend review support

- Paginated review queue.
- Pair evidence, normalized values, differences, scores, guards, and provenance.
- Durable decisions scoped to dataset, entity type, or provider schema.
- Optimistic version checking to prevent silent review conflicts.
- Merge, keep separate, choose values, split cluster, lock cluster, and bad mapping actions.
- Undo or reversal route.

### UI workflow

Implement the Match & Deduplicate tab as:

1. Data source selection.
2. Field mapping confirmation.
3. Preview settings.
4. Preview results.
5. Full job progress.
6. Review queue.
7. Results and export.

Required UI behavior:

- Persistent job history rail.
- Backend-driven counts and stage progress.
- One primary action per state.
- Explicit confirmation for destructive actions.
- Hard contradictions displayed as `Cannot auto-merge`.
- No `Merge all similar` action.
- Export blocked when required review is incomplete unless the user explicitly chooses an unresolved export.
- Responsive behavior below 1024px.
- Keyboard-only operation and review shortcuts.
- WCAG 2.1 AA contrast and focus behavior.
- Reduced-motion support.
- Sensitive values hidden until deliberately revealed.
- Status communicated with text and icons, never color alone.

### Exit criteria

A user can complete the full workflow from imported or scraped data through preview, review, canonicalization, and provenance-preserving export without leaving the tab.

## 14. Phase 8 - Quality, Observability, and Performance

### Tests

- Unit tests for schemas, normalization, transforms, policy gates, selectors, and decision rules.
- Property tests for normalization and candidate generation.
- Fixture tests for every preset.
- Picker-to-preset integration tests.
- Contract tests across UI, application services, and workers.
- Migration tests from every supported schema.
- Export golden-file tests.
- Cancellation, pause, retry, restart, and failure-recovery tests.
- Review conflict and undo tests.
- Accessibility tests at desktop, tablet, and narrow widths.
- Reduced-motion tests.
- Update verification and rollback tests.

### Metrics

Capture:

- Imported, mapped, normalized, rejected, and unmatchable rows.
- Candidate blocks and block sizes.
- Candidate pairs generated, deduplicated, skipped, and capped.
- Decision counts by entity, source, and mapping version.
- Cluster sizes and bridge rejections.
- Survivor source distribution.
- Review turnaround.
- Stage duration, memory, and records per second.
- Precision and recall only when measured against labeled data.

### Performance target

The initial local implementation should target approximately 100,000 records per matching job using Polars and bounded blocks. Introduce sharding only after measured workloads justify it.

## 15. Phase 9 - Curated Presets and Preset Operations

Start with generic presets, then add curated coverage in this order:

1. Amazon representative search and product-detail presets.
2. Zillow representative search and property-detail presets.
3. One representative page-type family per remaining category.
4. Additional providers only after fixture, policy, and maintenance ownership exists.

Every production preset requires:

- Exact URL scope.
- Policy metadata.
- Typed extraction fields and mappings.
- Pagination and stop conditions.
- Sanitized fixtures.
- Health checks.
- Coverage thresholds.
- Semantic version.
- Owner and maintenance review date.

Add:

- Signed preset package loading.
- Health status and degradation warnings.
- Deprecation and successor migration.
- Package rollback.
- Custom preset import/export without credentials or raw responses.
- Compatibility checks for preset updates.

## 16. Phase 10 - Secure Updates and Release Operations

### Update client

- GitHub Releases discovery.
- Stable and opt-in beta channels.
- ETag and `If-None-Match` caching.
- SemVer and platform compatibility selection.
- Signed manifest verification using an embedded Ed25519 public key.
- HTTPS and allowed-host validation.
- SHA-256 and artifact-signature verification.
- Package staging outside the active application path.
- Explicit user approval before download and installation.
- No installation during active jobs or unsaved review decisions.
- Backup before migrations.
- Restart-based installation.
- Rollback and failed-migration recovery.

### Release pipeline

For protected release tags:

1. Format, lint, unit, integration, contract, migration, and security checks.
2. Reproducible platform builds.
3. UI and update smoke tests.
4. SBOM and checksum generation.
5. Protected signing of manifests and artifacts.
6. GitHub Release publication.
7. Clean-install and prior-version upgrade tests.

Each release should publish installers, signatures, signed manifest, checksums, SBOM, release notes, compatibility information, and rollback instructions.

## 17. Phase 11 - Optional ML and Scale

Only begin this phase after reviewed labeled data exists.

- Train from human-reviewed pair decisions.
- Mark weak labels separately and exclude them from evaluation claims.
- Use role-based explainable features.
- Calibrate probabilities.
- Report precision and recall by entity type, source, and score band.
- Store model, feature, training hash, threshold, and evaluation metadata.
- Start with review-queue ordering only.
- Enable ML-assisted auto-match only after entity-specific evaluation gates pass.
- Add deterministic worker sharding only when workload measurements require it.
- Consider PostgreSQL and object storage only for shared or hosted deployments.

## 18. Recommended MVP Scope

The first usable release should include:

- Tauri desktop shell.
- React/TypeScript navigation.
- Project storage and SQLite migrations.
- Durable job service and job center.
- CSV, JSON, and XLSX import.
- Immutable staged datasets and provenance.
- Generic HTTP/HTML scraping with test mode.
- Generic preset validation.
- Deterministic matching preview.
- Basic safe clustering.
- Review queue with merge and keep-separate decisions.
- Canonical, clean, original, and audit exports.
- Basic Match & Deduplicate UI.

Defer broad website coverage, ML, remote deployment, and automatic update installation until the local workflow is reliable and measurable.

## 19. Cross-Phase Acceptance Criteria

The implementation is ready for a closed beta when:

- The UI starts and observes scraping, matching, and export jobs without containing domain logic.
- Raw sources, staged rows, canonical records, review decisions, and exports are traceable.
- A preset, mapping, policy, model, or database update cannot silently alter a completed job.
- Users can import a file or scrape output, confirm mapping, preview matching, review uncertainty, and export results.
- No pair is auto-merged using name or region similarity alone.
- Conflicting provider IDs or house numbers block automatic merging.
- Candidate generation has deterministic caps and no unbounded all-pairs comparison.
- Ambiguous cluster bridges are rejected or sent to review.
- Failed or cancelled jobs leave source data intact and do not publish incomplete canonical output.
- The application handles narrow layouts, keyboard navigation, reduced motion, and sensitive-value protection.
- Unsigned, tampered, incompatible, or untrusted update artifacts are refused.
- Failed migrations and updates leave project data recoverable.

## 20. Main Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| Child WebView API limitations | Resolve the native bridge design in Phase 0 and build a fixture-backed vertical slice early. |
| Website selector churn | Use versioned presets, fixtures, health checks, degradation states, and ownership. |
| False-positive merges | Require deterministic evidence, hard guards, bounded blocks, review, and constrained clustering. |
| Raw-data loss | Make source artifacts immutable and test cancellation, failure, deletion, and export behavior. |
| Worker/UI contract drift | Centralize schemas and run contract tests in CI. |
| Sensitive data leakage | Redact logs, use the OS credential store, minimize raw response retention, and gate previews. |
| Unsafe updates | Verify signed manifests and artifacts, back up before migrations, and test rollback. |
| Premature complexity | Defer ML, server deployment, broad preset coverage, and sharding until measured need exists. |

## 21. Open Decisions Before Implementation

- Which exact Tauri child-WebView API and supported platforms are required for the first release?
- Are WebView sessions isolated per job or per workspace?
- Which APIs receive first-class integrations?
- Who owns maintenance and policy review for bundled presets?
- What is the preset package signing and distribution process?
- Which fields require encryption at rest on each target platform?
- What are the supported database migration rollback guarantees?
- Which update signing service or protected environment will hold release keys?
- What is the closed-beta scope and representative test dataset?

## 22. Suggested Delivery Order

1. Resolve Phase 0 decisions and create the workspace skeleton.
2. Build contracts, SQLite migrations, artifacts, projects, and durable jobs.
3. Implement dataset import and profiling.
4. Build the generic fixture-backed scraping engine.
5. Build the deterministic matching backend.
6. Add canonicalization and audit exports.
7. Add the native WebView bridge and Scrape Studio.
8. Add the Match & Deduplicate review UI.
9. Add quality, performance, and recovery testing.
10. Add curated presets and preset operations.
11. Add secure updates and release operations.
12. Add optional ML and scale features only after measurement and evaluation.

Each phase should end with a usable, tested vertical slice and a migration-safe data contract for the next phase.
