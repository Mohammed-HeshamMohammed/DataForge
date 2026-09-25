# DataForge

DataForge is a local-first Windows desktop application for collecting permitted website data, turning it into structured datasets, matching and deduplicating records, reviewing uncertain decisions, and exporting clean results.

The application is functionally mature. Current work is focused on production packaging, first-run onboarding, plain-language workflows, recovery, accessibility, and clean-machine end-to-end testing for non-technical users. The active scope and release gates are in [DATAFORGE_MASTER_PLAN.md](DATAFORGE_MASTER_PLAN.md).

## What DataForge Will Do

- Collect permitted public/API data using versioned website and page-type presets.
- Render JavaScript-dependent pages inside DataForge’s visible embedded WebView—without opening a separate browser window.
- Let users select elements, repeated records, and pagination controls to create custom extraction presets.
- Stage raw scraped/imported data with provenance before transformation.
- Match and deduplicate records conservatively, with explanations, review queues, canonical records, and audit exports.
- Check for verified future desktop releases through GitHub Releases without trusting unsigned downloads.

## Architecture at a Glance

```text
React + TypeScript Desktop UI
             │
       Tauri / Rust Host
             │
  Application Services and Jobs
       │                 │
Scraping Worker    Matching Worker
       │                 │
Child WebView      Polars + RapidFuzz
       │                 │
        SQLite + local artifact storage
             │
  Signed presets, mappings, models, and releases
```

## Documentation

| Document | Purpose |
| --- | --- |
| [Platform architecture plan](docs/architecture.md) | Master architecture decision: layers, services, workers, data flow, versioning, migrations, and secure GitHub release updates. |
| [Scraping engine upgrade](docs/Scrapper/Scrapper.md) | Job-based scraping engine, embedded WebView design, Scrape Studio, policy limits, implementation phases, and preset catalog scope. |
| [Website preset specification](docs/Scrapper/Website Preset Specification.md) | Versioned page-type preset contract: URL scope, strategies, extraction fields, pagination, validation, health, and custom presets. |
| [Matching and deduplication backend](docs/Matching and Deduplication/Matching and Deduplication.md) | Import, role mapping, normalization, candidate generation, scoring, safe clustering, review, canonicalization, APIs, and operations. |
| [Matching & Deduplication UI/UX](docs/Matching%20and%20Deduplication/Matching%20%26%20Deduplication%20Tab.md) | Dedicated tab’s user journey, layouts, components, review experience, accessibility, and backend integration rules. |
| [Development guide](docs/development.md) | Setup, run, checks, and implemented behavior. |
| [IPC contract](docs/ipc-contract.md) | Versioned command API, job events, and worker contracts. |
| [Implementation decisions](docs/decisions.md) | Choices made while building, and when to revisit them. |

## Recommended Reading Order

### Product and technical leadership

1. [Platform architecture plan](docs/architecture.md)
2. [Scraping engine upgrade](docs/Scrapper/Scrapper.md)
3. [Matching and deduplication backend](docs/Matching%20and%20Deduplication/Matching%20and%20Deduplication.md)

### Scraping and preset engineering

1. [Scraping engine upgrade](docs/Scrapper/Scrapper.md)
2. [Website preset specification](docs/Scrapper/Website%20Preset%20Specification.md)
3. [Platform architecture plan](docs/architecture.md)

### Matching and desktop UI engineering

1. [Matching and deduplication backend](docs/Matching%20and%20Deduplication/Matching%20and%20Deduplication.md)
2. [Matching & Deduplication UI/UX](docs/Matching%20and%20Deduplication/Matching%20%26%20Deduplication%20Tab.md)
3. [Platform architecture plan](docs/architecture.md)

## Guiding Decisions

- **Desktop stack:** React/TypeScript in Tauri, with a Rust host and isolated Python workers.
- **Rendered pages:** DataForge’s embedded child WebView is the only rendered-page surface. Selenium is not treated as an in-WebView runtime.
- **One collection entry point:** Scrape Studio can launch every supported method, while XML/SOAP, APIs, sitemaps, feeds, crawls, repositories, documents, archives, bulk corpora, and local files open in their purpose-built workflow with the method already selected.
- **Sign-in:** A user may sign in manually inside the visible, temporary Studio session. DataForge does not read, store, or submit page credentials and does not automate login forms.
- **Authorized networking and API auth:** Collection workers support one fixed organization proxy plus OS-protected bearer, API-key, query-parameter, Basic, and OAuth 2 client credentials. OAuth access tokens remain memory-only.
- **Internal compatibility:** Reviewed Studio selectors can be copied as plain Playwright or Selenium scripts for loopback, private-network, and internal-test hosts without bundling another browser engine.
- **Scraping boundaries:** Use authorized APIs first, HTTP/HTML where permitted, and WebView rendering only when needed. Never bypass CAPTCHA, login, paywalls, access denials, rate limits, robots restrictions, or anti-bot controls.
- **Data integrity:** Preserve immutable raw imports/scrapes. Matching creates derived canonical records and audit artifacts rather than overwriting sources.
- **Matching safety:** Auto-merge only with strong evidence and no contradiction. Uncertain candidates require review.
- **Updates:** Use GitHub Releases for discovery/distribution, but install only artifacts verified against a signed manifest and signature/checksum.

## Planned Build Order

1. Produce a repeatable packaged build and test it on clean Windows machines.
2. Automate the complete packaged workflow: create, import or collect, map, match, review, export, restart, and update failure.
3. Add first-run onboarding, sample data, plain-language defaults, and actionable recovery guidance.
4. Complete accessibility, backup/restore, diagnostics, performance, and non-technical usability gates.
5. Sign the installer and updater artifacts, then publish only after every release gate passes.

## Repository Status

**The core application is implemented and its automated suites pass, but it is not yet production-ready.**

- **Workflow:** create a project, then import CSV, JSON/JSONL, XLSX, XML, Parquet, DOCX, ZIP, or GZIP; run a policy-gated HTTP, XML/SOAP, JSON, or GraphQL collection; or collect dynamic pages in Scrape Studio. Confirm a mapping, preview and run matching, review and group decisions, and export traceable results.
- **Preset operations:** health checks, signed packages, and rollback.
- **Credentials:** the OS credential store holds API secrets.
- **Review ranking:** ordering only.
- **Updates:** signature-verified, installed only with your approval.

The active product roadmap and production exit criteria are in [DATAFORGE_MASTER_PLAN.md](DATAFORGE_MASTER_PLAN.md). The older phase-by-phase implementation record remains in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#implementation-status).

## Quick Start

```powershell
python -m pip install -r requirements-dev.txt
npm run setup:desktop
npm run dev:tauri
```

Build the installer with `.\scripts\build-release.ps1`; installed users will not need Python. On the current development machine, antivirus removes newly generated PyInstaller executables, so production packaging must be completed in an excluded build folder or a clean CI runner. Development requires Python 3.11+, Node 20+, Rust/Cargo, and WebView2 on Windows. See [docs/development.md](docs/development.md).

## Safety and Privacy

DataForge must process only data users are authorized to collect and use. Credentials belong in the operating system credential store; logs minimize sensitive values; raw data, canonical output, and export/audit artifacts remain under the user’s control.

