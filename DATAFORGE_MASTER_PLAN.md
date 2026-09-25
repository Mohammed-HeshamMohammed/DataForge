# DataForge Production Plan

**A local-first desktop application that helps non-technical users collect, clean, match, and export data safely.**

- **Status:** active product roadmap and single source of truth.
- **Updated:** 2026-09-19.
- **Primary outcome:** a normal Windows user can install DataForge and complete a useful workflow without knowing scraping, selectors, APIs, matching algorithms, or developer tooling.
- **Companion documents:** [development guide](docs/development.md), [implementation decisions](docs/decisions.md), [IPC contract](docs/ipc-contract.md), and the detailed implemented subsystem specifications under `docs/`.

---

## 1. Product goal

DataForge turns permitted websites, public APIs, documents, and local files into clean datasets through a guided desktop workflow:

1. Create or open a project.
2. Import a file or paste a URL.
3. Let DataForge detect the collection method and preview ten records.
4. Collect the dataset with visible progress and understandable stops.
5. Confirm field meanings.
6. Find duplicates, review uncertain matches, and export the result.

The simple path is the product. Presets, selectors, engines, schemas, pagination, credentials, and policy signals remain available when needed, but they do not dominate the default experience.

## 2. Definition of production-ready

DataForge is ready for production use only when all of these are true:

| Area | Exit criterion |
| --- | --- |
| **Installation** | A signed installer works on clean, supported Windows 10 and 11 machines without Python, Node.js, Rust, or a terminal. Install, update, repair, and uninstall are tested. |
| **First success** | A first-time user can create a project and import a file in under five minutes, or collect a permitted URL in under ten minutes, without documentation. |
| **Core workflow** | Import/collect → map → match → review → export works end to end in the packaged application. |
| **Reliability** | Forced restarts do not corrupt projects. Jobs recover, retry, or fail with an actionable explanation. Backups and project recovery are tested. |
| **Data safety** | Raw inputs are immutable, credentials stay in the OS credential store, destructive actions require clear confirmation, and exports never silently omit unresolved work. |
| **Clarity** | Errors explain what happened, what DataForge protected, and the exact next action. Technical details are available behind “Details.” |
| **Accessibility** | Keyboard-only operation succeeds for the primary workflow; focus, labels, contrast, zoom, and reduced motion meet WCAG 2.2 AA. |
| **Supportability** | Users can create a redacted diagnostics bundle containing versions, health status, and logs without exposing credentials or record values. |
| **Updates** | Signed updates can be checked, downloaded, verified, postponed, installed, and rolled back safely. |

## 3. Current implementation

| Area | Current state |
| --- | --- |
| **Desktop** | Tauri 2, React/TypeScript, title-bar menus and command search, Settings-based scraping, compact layouts, and independently minimizable sidebars. |
| **Projects and jobs** | SQLite projects with tracked migrations and backups; durable jobs support progress, pause, cancel, retry, and restart recovery. |
| **Imports** | CSV, JSON/JSONL, XLSX, XML, Parquet, DOCX, ZIP, and GZIP import with profiling, mapping, immutable raw artifacts, and staged datasets. |
| **Collection** | Automatic URL and preset detection; HTTPX and Scrapy engines; one fixed organization proxy; OS-protected bearer, API-key, Basic, and OAuth API authentication; Scrape Studio launcher for every method; embedded WebView with expiring user-controlled sign-in/MFA; internal-host Playwright/Selenium selector-script compatibility; feeds, sitemaps, crawls, XML/SOAP, GraphQL, OAI-PMH, APIs, PDFs, archives, and bulk corpora. |
| **Safety** | Purpose and authorization acknowledgement; robots.txt, TDMRep, AIPREF, and ai.txt checks; rate limits; stops for CAPTCHA, login, paywall, denial, and rate limiting. |
| **Matching** | Conservative matching, contradiction guards, constrained clustering, review queue, undo, canonical values, and cross-dataset matching. |
| **Exports** | Canonical, clean, original, and audit exports with hashes and unresolved-review handling. |
| **Maintenance** | Preset fixtures and health checks, selector suggestions, drift reports, watches, sanitized captured fixtures, and signed preset packages. |
| **Verification** | 172 Python tests, 37 desktop tests, and 9 Rust tests currently pass; TypeScript is clean and the production frontend builds. |

The application is functionally mature, but it is not production-ready until packaging, clean-machine end-to-end testing, onboarding, recovery, and usability gates pass.

## 4. Product principles

1. **Simple by default, powerful on demand.** Ask for a file or URL first. Put advanced controls behind a clearly labelled section.
2. **No terminal required.** Every normal user workflow must work from the installed desktop application.
3. **Local-first.** User data stays on the machine unless the user explicitly exports it.
4. **Protect the user.** Preserve raw data, make destructive operations recoverable, and never hide unresolved or rejected records.
5. **Safe collection is non-negotiable.** Never bypass CAPTCHA, authentication, paywalls, access denial, rate limits, robots restrictions, or anti-bot controls.
6. **Explain in ordinary language.** Prefer “How should DataForge collect this page?” over “Select discovery strategy.”
7. **Progress must be real.** Job status comes from durable backend events, not UI estimates.
8. **One reliable path before many options.** Improve the common workflow before adding more engines, integrations, or configuration.
9. **No feature is done without failure handling.** Empty, loading, partial, offline, denied, cancelled, and recovery states are part of the feature.

## 5. User experience target

### Default collection experience

The default screen asks for:

- a URL;
- what the user wants the data for, described in plain language;
- confirmation that they are allowed to collect it.

DataForge then:

1. checks whether the URL can be collected;
2. identifies the source type and best method;
3. shows a ten-record preview;
4. explains any missing fields or restrictions;
5. offers one primary action: **Collect data**.

“Preset,” “engine,” “CSS,” “XPath,” “cursor,” and similar implementation terms belong in an Advanced section. Scrape Studio is offered only when automatic collection cannot obtain useful records.

### Default matching experience

DataForge should ask the user what each important column means, suggest answers, preview likely duplicates, and explain uncertain comparisons in field-level language. Thresholds and blocking configuration remain advanced controls.

### Error format

Every user-facing failure follows this structure:

- **What happened** — one sentence without an error code.
- **Why** — the relevant source, file, or project condition.
- **What to do next** — one safe primary action and, when useful, one alternative.
- **Details** — expandable technical information and a copy button.

## 6. Production roadmap

This roadmap uses Now / Next / Later to avoid false date precision. One developer should keep roughly 70% of capacity on the committed release path, 20% on reliability and technical health, and 10% for defects discovered during real use.

### Now — production release blockers

| Initiative | Status | Outcome | Dependency |
| --- | --- | --- | --- |
| **Reliable Windows installer** | **CI gate added; awaiting first green run** | Build and install on clean Windows 10/11 without development tools. | Main/manual CI now builds the packaged service and unsigned NSIS installer on a clean Windows runner, smoke-tests the service, verifies the installer, and uploads checksums. Authenticode and clean-VM install tests remain. |
| **Packaged end-to-end suite** | **Not started** | Automated packaged-app tests cover create project, import, automatic URL detection, test collection, full collection, mapping, matching, review, export, restart, and update failure. | Reliable installer/build artifact. |
| **First-run onboarding** | **In progress** | Welcome flow, sample project, sample file, URL walkthrough, and a visible “Try with sample data” path. | The idempotent fictional sample project and first-run action are implemented; the guided URL walkthrough remains. |
| **Plain-language collection flow** | **In progress** | Automatic mode is primary; advanced scraping terms are hidden until requested; blocked URLs and incomplete extraction have guided recovery. | Automatic URL checking is primary and JavaScript-heavy pages open directly in Scrape Studio. XML/SOAP, GraphQL, JSON network data, common tabular/document formats, load-more, open Shadow DOM, same-origin frames, and scoped downloads are implemented. Guided recovery still needs wider coverage. |
| **Recovery and backups** | **In progress** | Project-open recovery, migration rollback, interrupted-job recovery, backup restore, and disk-space checks are exercised from the UI. | Existing migrations, backups, and durable jobs. |
| **Actionable errors** | **In progress** | Shared error taxonomy and consistent recovery actions across imports, scraping, matching, updates, and exports. | A shared UI translator now provides a plain-language summary, next step, and expandable technical details. Command-specific retry actions and broader error-code coverage remain. |
| **Release security** | **At risk** | Dependency audits, signed manifests, code-signed installer, updater rollback, and a documented release checklist. | Signing certificates and release secrets. |

### Next — make everyday use comfortable

| Initiative | Status | Outcome |
| --- | --- | --- |
| **Usability pass** | **Not started** | Five non-technical users complete the primary workflow; observed confusion is fixed before release. |
| **Accessibility pass** | **In progress** | WCAG 2.2 AA audit, keyboard walkthrough, light/dark contrast, zoom, reduced motion, and screen-reader fixes. |
| **Diagnostics and support bundle** | **Not started** | One-click redacted bundle plus an in-app system check that recommends fixes. |
| **Dataset grid polish** | **Not started** | Fast virtualized rows, clear filters, sorting, column visibility, empty states, and export preview. |
| **Notifications** | **Not started** | Understandable job completion/failure notifications with direct next actions. |
| **Preset reliability** | **In progress** | Health checks run on bundled presets; degraded sources explain the problem and suggest a working alternative. |
| **Performance budgets** | **Not started** | Startup, import, collection, matching, memory, and large-grid targets are measured in CI. |
| **Help inside the app** | **Not started** | Contextual explanations, searchable help, example URLs/files, and short troubleshooting guides. |

### Later — only after the release path is stable

- Optional Arabic localization and right-to-left layout.
- DuckDB-backed querying and virtualized processing when real datasets exceed the current in-memory limits; Parquet import itself is already supported.
- More curated public-data sources requested by users.
- Optional local AI suggestions when they measurably improve setup and always produce deterministic, testable extraction rules.
- Plugin support only if maintaining built-in connectors becomes a bottleneck.
- Remote or shared deployments only after the local desktop workflow is stable and demand is demonstrated.

### Won’t have for the production release

- Dataset certification, source countersignatures, Merkle proofs, trusted timestamps, or attestation packets.
- Thesis, paper, citation, research-experiment, benchmark-publication, DMP, ethics-appendix, Zenodo, DataCite, RO-Crate, PROV, or paper-asset features.
- CAPTCHA solving, proxy rotation, fingerprint spoofing, stealth browsers, paywall bypass, or login automation.
- Multiple competing matching engines added only for comparison.
- Social/community connectors without a clear user request and official supported API.
- A cloud account requirement, telemetry requirement, or subscription dependency for the core local workflow.

These items can be reconsidered only after the production exit criteria are met and real users request them.

## 7. Release gates

No public production release is declared until:

- all Python, desktop, Rust, contract, and accessibility tests pass;
- `git diff --check`, TypeScript typecheck, Clippy with warnings denied, dependency audits, and license inventory pass;
- the installer passes clean-machine install, first launch, repair/update, rollback, and uninstall tests;
- the full primary workflow succeeds in the packaged app using both sample data and one permitted live source;
- forced termination during import, collection, matching, and migration leaves the project recoverable;
- no credential or record value appears in logs or the diagnostics bundle;
- a keyboard-only test completes the primary workflow;
- the top usability failures from non-technical-user testing are fixed or explicitly accepted;
- release notes, supported Windows versions, backup/restore instructions, and known limitations are published.

## 8. Immediate execution order

1. Get the new clean-Windows packaging job green and preserve its unsigned installer artifact.
2. Add packaged-app end-to-end tests for the primary workflow.
3. Extend the implemented sample-project onboarding with a guided URL walkthrough.
4. Validate the simplified collection and matching language with non-technical users; keep expert controls under Advanced.
5. Extend the shared actionable-error layer with command-specific retry actions.
6. Test backup/restore and forced-restart recovery.
7. Run accessibility and non-technical usability sessions.
8. Fix the resulting issues, sign the installer, and run the release checklist on a clean VM.

## 9. Risks

| Risk | Mitigation |
| --- | --- |
| Antivirus blocks local packaging | Build in a clean CI runner, sign artifacts, submit false-positive samples, and retain a second packaging path for evaluation. |
| Websites change or block collection | Automatic detection, fixture health checks, clear stop reasons, Scrape Studio fallback, and no promise that every website is collectible. |
| Too many advanced options confuse users | Progressive disclosure, one recommended path, user testing, and plain-language defaults. |
| Project corruption or disk exhaustion | Preflight disk checks, transactional migrations, backups, content-addressed artifacts, and tested recovery. |
| Large datasets exceed current architecture | Publish supported limits, stream where possible, measure first, and move to DuckDB/Parquet only when evidence justifies it. |
| Support reports expose sensitive data | Redaction by default, preview the support bundle, and never include credentials or record values. |
| Scope expands before release | New work must either satisfy a release gate or replace an existing roadmap item. |

## 10. Completion statement

DataForge is complete when a non-technical Windows user can install it, understand it, finish the main workflow, recover from common failures, and safely update it without developer help. Feature count, research novelty, and paper output are not completion criteria.
