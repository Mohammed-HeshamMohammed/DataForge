# DataForge

DataForge is a proposed local-first desktop platform for collecting permitted website data, turning it into structured datasets, matching and deduplicating records, reviewing uncertain decisions, and exporting traceable results.

This repository contains the DataForge architecture and implementation specification pack plus the first executable foundation. The current implementation is intentionally small: it establishes versioned contracts, SQLite project/job persistence, and independently checkable desktop and worker boundaries.

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
- **Scraping boundaries:** Use authorized APIs first, HTTP/HTML where permitted, and WebView rendering only when needed. Never bypass CAPTCHA, login, paywalls, access denials, rate limits, robots restrictions, or anti-bot controls.
- **Data integrity:** Preserve immutable raw imports/scrapes. Matching creates derived canonical records and audit artifacts rather than overwriting sources.
- **Matching safety:** Auto-merge only with strong evidence and no contradiction. Uncertain candidates require review.
- **Updates:** Use GitHub Releases for discovery/distribution, but install only artifacts verified against a signed manifest and signature/checksum.

## Planned Build Order

1. Establish the desktop shell, local project storage, SQLite migrations, typed contracts, and durable job service.
2. Build generic scraping presets, Scrape Studio, embedded WebView bridge, and staged dataset output.
3. Build deterministic matching/deduplication, review queue, canonicalization, and audit exports.
4. Add curated website presets, health checks, custom preset editor, and secure GitHub release updates.
5. Add optional ML ranking and remote/shared deployment capabilities only after local workflows are measured and stable.

## Repository Status

**All implementation-plan phases are built, and a Windows installer builds locally.**

- **Workflow:** create a project, then import CSV/XLSX/JSON, run a policy-gated HTTP/API scrape, or collect from rendered pages in Scrape Studio. Confirm a mapping, preview and run matching, review and group decisions, and export traceable results.
- **Preset operations:** health checks, signed packages, and rollback.
- **Credentials:** the OS credential store holds API secrets.
- **Review ranking:** ordering only.
- **Updates:** signature-verified, installed only with your approval.

The per-phase status and remaining release hardening are in [IMPLEMENTATION_PLAN.md](IMPLEMENTATION_PLAN.md#implementation-status).

## Quick Start

```powershell
python -m pip install openpyxl httpx beautifulsoup4 rapidfuzz cryptography pytest
npm run setup:desktop
npm run dev:tauri
```

Build the installer with `.\scripts\build-release.ps1`; installed users do not need Python. Development requires Python 3.11+, Node 20+, Rust/Cargo, and WebView2 on Windows. See [docs/development.md](docs/development.md).

## Safety and Privacy

DataForge must process only data users are authorized to collect and use. Credentials belong in the operating system credential store; logs minimize sensitive values; raw data, canonical output, and export/audit artifacts remain under the user’s control.

