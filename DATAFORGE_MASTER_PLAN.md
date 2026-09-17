# DataForge Master Plan

**A research-grade, local-first platform for collecting, certifying, cleaning, and linking data from public sources.**

- **Status:** living plan. It replaces the earlier phase plans as the single source of truth.
- **Written:** 2026-09-17. Library versions and licenses were checked on PyPI that day.
- **Context:** a graduation project. The system will be used in a research paper, and the collected data will be certified by the sources it was fetched from.
- **Companion documents:** [Scraping Expansion Plan](docs/Scrapper/Scraping%20Expansion%20Plan.md) (implemented), [decisions](docs/decisions.md) (D1–D25), [IPC contract](docs/ipc-contract.md), [development guide](docs/development.md).

---

## Contents

1. [Vision and success criteria](#1-vision-and-success-criteria)
2. [Where DataForge stands today](#2-where-dataforge-stands-today)
3. [Principles, including the new licensing policy](#3-principles)
4. [Pillar A — Source certification and research-grade provenance](#pillar-a--source-certification-and-research-grade-provenance)
5. [Pillar B — Collection engine, now with GPL tools](#pillar-b--collection-engine-now-with-gpl-tools)
6. [Pillar C — Entity resolution as a research instrument](#pillar-c--entity-resolution-as-a-research-instrument)
7. [Pillar D — Data quality, analytics, and scale](#pillar-d--data-quality-analytics-and-scale)
8. [Pillar E — Research workflow and publication](#pillar-e--research-workflow-and-publication)
9. [Pillar F — UX overhaul](#pillar-f--ux-overhaul)
10. [Pillar G — Platform, engineering, and delivery](#pillar-g--platform-engineering-and-delivery)
11. [Architecture after the plan](#11-architecture-after-the-plan)
12. [Roadmap](#12-roadmap)
13. [The research paper: what DataForge must let you show](#13-the-research-paper)
14. [Risks and mitigations](#14-risks-and-mitigations)
15. [Appendix: library catalog](#appendix-library-catalog)

---

## 1. Vision and success criteria

DataForge turns scattered public web and open-data sources into **trustworthy, citable, deduplicated datasets**. Every record can be traced to:
- the exact source response,
- the rules that allowed its collection,
- the preset and engine that extracted it,
- the human review that merged it,
- a signed, timestamped certificate that the source can verify.

The project succeeds when:

| # | Criterion | Measured by |
| --- | --- | --- |
| S1 | A dataset can be handed to a source owner who can **verify** every record came from their site at a stated time, unaltered | Certificate verifier passes on an exported packet; independent RFC 3161 timestamp validates |
| S2 | Entity resolution quality is **reported like a paper**: precision, recall, F1, pair completeness, reduction ratio, with confidence intervals | Benchmark harness on Abt-Buy, DBLP-ACM, Amazon-Google, and a hand-labelled sample of the user's own data |
| S3 | Any result in the paper can be **reproduced** from a run manifest on another machine | "Reproduce run" re-executes from pinned presets, WARC captures, and settings, and produces identical hashes |
| S4 | Collection is **lawful and polite by construction** | Signals ledger shows robots.txt, TDMRep, AIPREF, and ai.txt decisions for every host; no stop rule can be disabled |
| S5 | Common tasks fit on one screen with **no page scrolling** at 1366×768 | UX audit checklist (Pillar F) |
| S6 | New users complete "collect → clean → match → certify → export" in under 15 minutes | Moderated usability sessions (5 participants) |

---

## 2. Where DataForge stands today

| Area | Implemented |
| --- | --- |
| **Desktop shell** | Tauri 2 frameless window; React/TypeScript UI with two sidebars and tabs; OS credential store; signed updater |
| **Service** | Python sidecar over JSON lines; SQLite with 9 tracked migrations and backups; durable job runner with pause, cancel, retry, and restart recovery |
| **Collection** | httpx engine and Scrapy child engine with identical extraction; pages with pagination, sitemaps, feeds, crawls, llms.txt, 7 open-data API families, PDFs and linked files, Wayback and Common Crawl, Web Data Commons |
| **Extraction** | CSS/XPath selectors with fallbacks, schema.org structured data, article text, PDF tables and OCR, normalizers for phones, addresses, prices, dates, and domains |
| **Policy** | Required purpose; robots.txt (RFC 9309), TDMRep, AIPREF, and ai.txt; per-host politeness; stop rules for challenges, logins, and rate limits |
| **Scrape Studio** | Embedded WebView picker, pagination modes, structured-data offer, and signals on automated navigation |
| **Maintenance** | Fingerprints, relocation suggestions, drift reports, watches with diffs, WARC capture, sanitized fixtures, draft preset proposals |
| **Matching** | Normalization, capped blocking, evidence and guards, constrained clustering, review queue with undo, split and lock, cross-dataset matching, survivor rules, ordering-only ranking, and exports |
| **Quality** | 154 Python, 25 desktop, and 9 Rust tests; JSON contracts; property tests; live verification script against real sites |

**Known gaps this plan closes:**
- no certification or trusted timestamping;
- no quantitative ER evaluation;
- SQLite plus Python structures limit scale beyond about 1M rows;
- the UI scrolls heavily and has no menu bar or command palette;
- the packaged build is blocked by antivirus on the development machine;
- no research-publication exports (RO-Crate, DataCite, datasheets).

---

## 3. Principles

1. **Local-first.** Data stays on the user's machine unless the user exports or deposits it.
2. **Evidence over assertion.** Every derived value can be traced back to raw evidence; every human decision is logged and reversible.
3. **Lawful and polite collection is non-negotiable.** A research dataset certified by its sources must be collected in a way those sources accept. DataForge keeps:
   - robots.txt, TDMRep, and AIPREF enforcement;
   - rate limits;
   - stops on CAPTCHAs, logins, paywalls, and access denials.

   It never adds CAPTCHA solving, proxy rotation, fingerprint spoofing, or stealth browsers. This is what makes source certification credible.
4. **Reproducible by default.** Runs pin presets, settings, and engine versions; captures are optional but one click away.
5. **Honest measurement.** The app reports uncertainty (confidence intervals, review coverage) and never hides unresolved items.
6. **New licensing policy (2026-09-17).** DataForge is an academic, non-commercial graduation project.
   - **GPL-2.0, GPL-3.0, AGPL-3.0, LGPL, and MPL components are allowed** where they add value (for example html2text, ultimate-sitemap-parser, pywb, warcprox, OCRmyPDF with Ghostscript, unidecode, igraph).
   - `scripts/check-licenses.py` becomes an **inventory report** that feeds the paper's software citation appendix, not a gate.
   - Consequence to record in the thesis: a redistributed binary that bundles GPL components is covered by the GPL as a whole. Publishing the source code alongside the thesis (for example on GitHub or Zenodo) satisfies that.

---

## Pillar A — Source certification and research-grade provenance

This is DataForge's signature capability for the paper: a source owner can confirm that a dataset really came from them.

### A1. Evidence store (extends WARC capture)
- **Always-on evidence mode for research projects:** every fetched response is written to per-run WARC files (warcio), content-addressed. Cookies and authorization headers are stripped.
- **Per-record evidence pointer:** `evidence = {warc_file, record_offset, payload_sha256, source_url, retrieved_at}`, stored in a new `record_evidence` table.
- **Evidence for Studio collections:** the embedded WebView's rendered HTML snapshot and the page's HAR-like request log, hashed and stored. Optionally a full-page screenshot via the WebView capture API.
- **Evidence replay:** a built-in **pywb** (GPL-3.0) replay server on loopback lets reviewers and source owners see the page exactly as captured, with a banner showing capture time and hash.

### A2. Collection certificates
A signed, machine-verifiable document per run and per exported dataset:

```jsonc
{
  "certificate_version": 1,
  "dataset": {"name": "...", "record_count": 12034, "merkle_root": "sha256:..."},
  "collection": {
    "project": "...", "purpose": "research", "runs": ["job-uuid", "..."],
    "presets": [{"id": "...", "version": "...", "sha256": "..."}],
    "engine": {"name": "httpx|scrapy|webview", "dataforge_version": "0.3.0"},
    "sources": [{"host": "example.org", "first_fetch": "...", "last_fetch": "...", "requests": 412,
                 "signals": {"robots": "ok", "tdmrep": null, "content_usage": {}}, "evidence_warcs": ["sha256:..."]}]
  },
  "processing": {"normalizers": ["..."], "match_job": "...", "review_actions": 318, "policy_version": "..."},
  "signatures": [{"alg": "Ed25519", "key_id": "...", "value": "..."}],
  "timestamps": [{"type": "rfc3161", "tsa": "https://freetsa.org/tsr", "token": "base64..."},
                 {"type": "opentimestamps", "proof": "base64..."}]
}
```

- **Merkle tree** over canonical record hashes (sorted keys, normalized Unicode). A single record can be proven to belong to the dataset without revealing the others.
- **Ed25519 signing key per researcher.** It reuses the existing `.dfpreset` signing code; the key stays in `%USERPROFILE%\.dataforge\keys`.
- **Trusted timestamps:** RFC 3161 via `rfc3161ng` (MIT) against one or more free TSAs, plus an optional OpenTimestamps Bitcoin anchor (`opentimestamps-client`, LGPL-3.0). This gives independent proof the data existed at that time.
- **C2PA manifests** (`c2pa-python`, MIT/Apache) embedded in exported CSV/Parquet/PDF reports, so provenance travels with the files.

### A3. Source attestation packets (what you send to the source to certify)
One packet per source host:
- `attestation.pdf`: a human-readable summary rendered with WeasyPrint (BSD). It covers what was collected, when, how politely (request counts, delays, robots decisions), sample records with their source URLs, and a signature block for the source's representative.
- `records.csv`: only that source's records, with evidence pointers.
- `evidence/`: the WARC files and a pywb replay config, so the source can open the pages as captured.
- `certificate.json` plus a detached signature and timestamps.
- `verify.html`: an offline, single-file verifier. The source drops in the packet and sees hashes, signature, and timestamp validity without installing anything.
- **Countersignature flow:** the source signs `certificate.json` (Ed25519 key, GPG via `python-gnupg`, or a scanned signed PDF). DataForge imports the countersignature, verifies it, and marks the dataset **Source-certified** for that host.
- **Certification tracker:** a new "Certification" view per project that lists each source host and its state (not requested → sent → certified → rejected, with notes). It records contact details and dates, and exports a table for the paper's appendix.

### A4. Standard provenance exports
- **W3C PROV** (`prov` 3.2.1, MIT): PROV-JSON, PROV-N, and PROV-O Turtle graphs of `Agent` (researcher, DataForge version), `Activity` (fetch, extract, normalize, match, review), and `Entity` (responses, records, datasets).
- **RO-Crate** (`rocrate` 0.15.1, Apache-2.0): a one-click research object with data, certificates, WARCs, run manifests, preset definitions, and software versions, ready for Zenodo.
- **DCAT 3 and schema.org `Dataset`** metadata (via `rdflib`, BSD); **SHACL** validation of exports (`pyshacl`, Apache-2.0).
- **MLCommons Croissant** (`mlcroissant` 1.1.0) for datasets intended for ML use.
- **Frictionless Data Package** (`frictionless` 5.19, MIT) with a table schema, so tabular exports are self-describing and validated.

### A5. Verification CLI and API
- `dataforge verify <packet|crate>` checks signatures, timestamps, Merkle proofs, and WARC payload hashes, and exits non-zero on any mismatch.
- `dataforge prove-record <dataset> <record-id>` outputs a Merkle inclusion proof for a single record.

**Acceptance:**
- Tampering with any byte of a record, WARC, or certificate fails verification.
- Timestamps verify against the TSA certificate chain offline.
- A source owner can verify a packet on a machine without DataForge installed.

---

## Pillar B — Collection engine, now with GPL tools

### B1. Adopt tools the earlier license gate excluded
| Tool (license) | Use |
| --- | --- |
| **ultimate-sitemap-parser** 1.8.1 (GPL-3.0+) | Second sitemap parser for robustness (news, image, and video sitemaps; robots.txt discovery). Run as a cross-check against the in-house parser; disagreements become warnings. |
| **html2text** 2025.4.15 (GPL-3.0+) | Alternative Markdown conversion for article and AI-suggestion views; pick per preset. |
| **pywb** 2.9.1 (GPL-3.0) | Evidence replay (A1) and a local Wayback-style archive browser for captured runs. |
| **warcprox** 2.13.1 (GPL-2.0+) | Optional capture proxy for Scrape Studio. The embedded WebView's traffic is routed through a loopback warcprox instance, so rendered sessions produce complete WARCs, including XHR/fetch data calls. The proxy only records; it never alters requests. |
| **OCRmyPDF** 17.12 (MPL-2.0, with Ghostscript AGPL) | Adds OCR text layers to scanned PDFs before table extraction; better than raw Tesseract. |
| **Docling** 2.128 (MIT) | Bundled (no longer optional) for complex tables, DOCX, PPTX, and HTML documents. |
| **unidecode** 1.4 (GPL) | Transliteration for matching names across scripts (Arabic ↔ Latin, accents). |
| **igraph** 1.0 (GPL) | Fast cluster analytics and community detection on large match graphs. |

### B2. New source types
- **Documents from repositories:** DOI-resolved papers via Crossref and DataCite REST (polite pool with contact identity), OpenAIRE, CORE, and arXiv OAI-PMH, including an OAI-PMH harvester (`Sickle`-style, in-house).
- **Government registries:** SEC EDGAR full-text search; UK Companies House; OpenCorporates (API key); GLEIF LEI records (entity identifiers are strong match evidence); EU open data portals via CKAN and DCAT-AP harvesting.
- **Geospatial:** OpenStreetMap extracts (Geofabrik PBF plus `osmium`) for bulk offline POIs instead of Overpass for large areas; a Nominatim self-hosted option.
- **Knowledge graphs:** Wikidata dumps (offline JSON), DBpedia SPARQL.
- **Email-free social and community sources with official APIs:** Reddit (OAuth API), Mastodon public timelines, Stack Exchange data dumps. Only through official APIs with keys and rate limits.
- **Files on disk:** bulk-import a folder of HTML, PDF, or WARC files (for example, a source sends its own export for certification).

### B3. Engine improvements
- **Adaptive politeness:** measure server response time and back off automatically (AutoThrottle-style) in the httpx engine too; show the live request rate per host in the job view.
- **Distributed-friendly queue:** a persisted frontier with priorities, so long crawls survive restarts on both engines (the Scrapy JOBDIR already does this; unify the UI).
- **Change detection at scale:** ETag and Last-Modified revalidation plus content fingerprints (SimHash) to skip unchanged pages and detect near-duplicates.
- **Structured-data first:** a site "profile" run samples 20 pages and reports schema.org coverage, sitemap coverage, and API availability, then recommends the best preset automatically.
- **LLM extraction (opt-in, local-first):** local models via Ollama or llama.cpp propose extraction rules. Deterministic code still extracts, and proposals must pass the evaluation gate (already implemented).
- **Named-entity extraction from text:** spaCy 3.8 or GLiNER 0.2 (Apache-2.0) to pull organizations, people, and places from article bodies into structured fields.

**Acceptance:**
- Studio sessions produce WARCs that replay in pywb with data calls intact.
- An OAI-PMH harvest of 10k records resumes after a restart.
- A site profile recommends the correct preset on 8 of 10 test sites.

---

## Pillar C — Entity resolution as a research instrument

### C1. Pluggable matching engines (compare them in the paper)
| Engine | Method | License |
| --- | --- | --- |
| **DataForge rules** (current) | Evidence and guards, constrained clustering | — |
| **Splink** 4.0.17 | Fellegi–Sunter probabilistic linkage with EM, running on DuckDB | MIT |
| **dedupe** 3.0.3 | Active learning with a logistic regression over string distances | MIT |
| **recordlinkage** 0.16 | Classic toolkit (blocking, comparisons, classifiers) | BSD-3 |
| **Embedding matcher** | sentence-transformers 6.0 embeddings plus faiss 1.15 approximate nearest-neighbour blocking, then cross-encoder re-ranking | Apache-2.0 / MIT |
| **LLM adjudicator** (opt-in) | A local LLM judges only ambiguous pairs, with explanations; never auto-merges without the policy gate | — |

All engines read the same normalized input and write the same decision and cluster contract, so the review UI, exports, and certificates work unchanged.

### C2. Benchmark and evaluation harness
- **Built-in benchmark datasets:** Leipzig DBLP-ACM, DBLP-Scholar, Abt-Buy, Amazon-Google, plus Febrl synthetic person data. They are downloaded with checksums, and their licenses are recorded.
- **Metrics:**
  - precision, recall, and F1 on pairs and clusters (B³, CEAF);
  - pair completeness and reduction ratio for blocking;
  - runtime and memory.
- **Uncertainty:** 95% bootstrap confidence intervals; McNemar's test when comparing engines on the same pairs.
- **Ground truth from the user's own data:**
  - a stratified labelling session samples pairs across score bands;
  - review decisions become gold labels, with inter-annotator agreement (Cohen's κ) when two people label;
  - cleanlab 2.9 flags likely label errors.
- **Experiment registry:** every evaluation run stores its engine, parameters, dataset hash, code version, and metrics. A comparison view shows side-by-side tables and PR curves, exportable as LaTeX tables and SVG figures.

### C3. Matching quality features
- **Blocking diagnostics:** block-size histograms, top offending keys, and estimated recall loss.
- **Explainability:** per-pair evidence waterfall (already partially implemented), Splink match-weight charts, and SHAP-style feature contributions for learned models.
- **Transliteration and multilingual names:** unidecode, ICU transliteration, and phonetic encodings (jellyfish: Metaphone, NYSIIS) for Arabic, Latin, and mixed-script names.
- **Geo-aware matching:** H3 cells (`h3` 4.5, Apache-2.0) as blocking keys; distance-based evidence for addresses geocoded via a self-hosted Nominatim.
- **Temporal matching:** records valid over time (company names change); survivorship by recency and source trust.
- **Graph clustering options:** connected components, correlation clustering, and Leiden community detection (igraph), with the bridge-rejection guard retained.

**Acceptance:**
- The harness reproduces published F1 figures for Splink and dedupe on DBLP-ACM within ±2 points.
- Every table in the evaluation chapter is generated by DataForge from stored runs.

---

## Pillar D — Data quality, analytics, and scale

### D1. Scale
- **DuckDB 1.5** (MIT) as the analytical store for large datasets. SQLite remains the transactional store for jobs, reviews, and settings.
  - Raw and normalized rows live in Parquet files, content-addressed, and are queried through DuckDB.
  - Blocking and comparison run as SQL (the approach Splink uses) instead of Python loops.
- **Polars 1.44** (MIT) for transforms and normalizers in columnar form.
- **Targets:** 10M rows imported under 5 minutes; matching 1M rows under 15 minutes on a 16 GB laptop; UI tables virtualized so a grid of any size scrolls smoothly.

### D2. Quality
- **Profiling:** column statistics, distributions, missingness patterns, and outliers (a ydata-profiling 4.18-style report, rendered natively).
- **Validation rules:** Pandera 0.33 / Frictionless schemas per dataset, including type, range, pattern, uniqueness, and referential checks, with a quality score over time.
- **PII detection:** Microsoft Presidio 2.2 (MIT) finds personal data in collected text. It feeds sensitive-column flags, redaction, and pseudonymization for publication exports (keyed HMAC tokens, so matching still works).
- **Encoding repair:** ftfy 6.3 fixes mojibake on import.

### D3. Analytics
- **Dashboards per dataset:** records over time, source mix, field coverage, match rate, review progress, and quality score.
- **Maps:** geocoded records on an interactive map (Leaflet/folium-style, rendered in the UI with MapLibre), with H3 hexbin density.
- **Charts** follow one visual system (dataviz guidelines) and export as SVG/PNG for the paper.
- **Notebook bridge:** "Open in Jupyter" writes a notebook with the dataset loaded through DuckDB and the run manifest attached; papermill re-runs analysis notebooks as part of reproduction.

---

## Pillar E — Research workflow and publication

- **Research project mode:** a project template that enables evidence capture, certificates, experiment registry, data management plan, and ethics checklist by default.
- **Data management plan (DMP):** a guided form (storage, retention, access, sharing, deletion) exported as a PDF appendix.
- **Ethics and legal checklist:** covers the purpose of collection, terms-of-service review per source, personal data classification, retention, and anonymization. The checklist has to be complete before a full run in research mode.
- **Datasheets for Datasets and Data Cards:** generated from real run metadata, with the researcher filling in motivation and intended use.
- **Citations:**
  - BibTeX and CSL-JSON for every dataset, source, and software component (bibtexparser 2.0, citeproc-py 0.11);
  - a "Cite this dataset" panel;
  - a software citation list generated from the dependency inventory (replaces the license gate).
- **Deposit (optional, explicit consent):** Zenodo via its REST API (`zenodo-client`) with the RO-Crate as the upload and a DOI returned into the certificate; DataCite metadata via the `datacite` client.
- **Reproduce run:** re-executes a run from its manifest. Live mode re-fetches politely; evidence mode replays the WARCs. It diffs the output hashes against the original and reports any drift.
- **Paper assets export:** a zip of LaTeX tables, SVG figures, PROV graph image, dataset statistics, and a methods paragraph generated from actual settings (editable).

---

## Pillar F — UX overhaul

### F1. Window chrome (implemented in this change)
- **Menu bar in the title bar:** File, Edit, Selection, View, Go, Run, Help. The menus are keyboard-navigable, and every item runs a real command (catalog below).
- **Command center in the title bar:** one wide field shows the project name and folder. Clicking it or pressing Ctrl+K turns it into a search over every command (project actions and recent projects included), with results dropping down beneath it.
- **Settings button** in the title bar next to theme, updates, and window controls; **maximize and restore** added.
- **No buttons in the left sidebar:** the project card and action buttons are removed. Their actions live in the command center and the File and Run menus, which frees the sidebar for review progress and datasets.

#### Menu catalog
| Menu | Items (shortcut) |
| --- | --- |
| **File** | New project… (Ctrl+Shift+N) · Open project… (Ctrl+O) · Open recent ▸ · Import data file… (Ctrl+I) · New scrape… · Open project folder · Copy project path · Close project · Exit (Alt+F4) |
| **Edit** | Undo last review decision (Ctrl+Z) · Cut / Copy / Paste / Select all (standard, in text fields) · Find dataset… (Ctrl+F) · Settings (Ctrl+,) |
| **Selection** | Select all (Ctrl+A) · Clear selection (Esc) · Focus datasets list · Focus main pane · Focus jobs panel |
| **View** | Command palette… (Ctrl+Shift+P) · Toggle left sidebar (Ctrl+B) · Toggle right sidebar (Ctrl+J) · Toggle theme · Zoom in / out / reset (Ctrl+= / Ctrl+- / Ctrl+0) · Compact density · Toggle full screen (F11) |
| **Go** | Overview (Ctrl+1) · Scraping (Ctrl+2) · Scrape Studio (Ctrl+3) · Datasets (Ctrl+4) · Match & Deduplicate (Ctrl+5) · Settings (Ctrl+6) · Next tab / Previous tab (Ctrl+Tab / Ctrl+Shift+Tab) |
| **Run** | Start matching… · New collection… · Run fixture health checks · Run system check · Pause all jobs · Cancel all jobs · Refresh (F5) |
| **Help** | Command palette · Keyboard shortcuts (Ctrl+/) · Open documentation folder · Open logs folder · Check for updates · About DataForge |

### F2. Less scrolling (layout rules applied across the app)
1. **The whole window is the viewport.** Panes scroll internally; the page itself never scrolls.
2. **Two-column work layouts:** configuration on the left, live results on the right, both independently scrollable.
3. **Secondary tabs instead of stacked panels:**
   - Scraping splits into Collect · Site signals · Watches · Customize preset.
   - Settings uses a vertical section list (Project · Collection · Updates · Credentials · Presets) and shows one section at a time.
4. **Compact density option:** 8 px rhythm, 13 px body text, and denser tables. Denser layouts are toggled from View.
5. **Collapsible sidebars** (Ctrl+B / Ctrl+J), with state remembered.
6. **Sticky action bars:** primary buttons (Test, Start) stay visible at the bottom of long forms.
7. **Progressive disclosure:** advanced options sit in drawers, not in the main flow.

### F3. Next UX work
- **Onboarding tour** for first launch, and **empty states** that teach (for example "Import your first file" with sample data).
- **Notification center** with a job-complete toast history, and system notifications when the window is in the background.
- **Resizable split panes** with persisted sizes; a data grid with column pinning, sorting, filtering, and virtualization.
- **Undo/redo stack** extended beyond review decisions (mapping edits, preset edits, watch changes).
- **Arabic localization with right-to-left layout** (i18next), plus English; locale-aware number and date formats.
- **Accessibility:** WCAG 2.2 AA; full keyboard operation; screen-reader labels (axe-core already runs in tests); a reduced-motion mode.
- **Visual refresh:** a consistent icon set, chart palette, and motion; a light-theme contrast audit.

**Acceptance:**
- At 1366×768 the Overview, Scraping (Collect), Datasets, and Settings sections show their primary actions without page scrolling.
- Every menu item works in the desktop app.
- A keyboard-only walkthrough of "import → map → match → review → export" succeeds.

---

## Pillar G — Platform, engineering, and delivery

- **Packaging:**
  - An antivirus exception for `build/` on the development machine (Avast removes every new PyInstaller executable).
  - Evaluate Briefcase (BSD) or Nuitka (AGPL-3.0, now allowed) as alternatives that trigger fewer heuristics.
  - Code-sign the installer with a certificate to reduce false positives.
- **End-to-end tests** of the real desktop app with WebdriverIO and `tauri-driver`, replacing the ad-hoc DevTools scripts; nightly `scripts/live-check.py` with results archived.
- **Mutation testing** (mutmut) on the policy and matching guards; **benchmarks** (pytest-benchmark) tracked in CI; **load tests** of the job runner.
- **Plugin system:**
  - presets, engines, normalizers, and exporters discovered via Python entry points;
  - signed plugin packages, reusing the `.dfpreset` signing;
  - a plugin manager in Settings.
- **Local REST API (opt-in, loopback, token-protected)** so notebooks and scripts can drive DataForge; an OpenAPI description generated from the IPC contract.
- **Observability:** a structured log viewer in Help; per-job resource charts; crash reports kept locally.
- **Security:** threat model document; dependency audit (pip-audit, npm audit) remains a CI gate; SQLCipher option for encrypted projects; project export and import with integrity hashes.

---

## 11. Architecture after the plan

```
┌──────────────── Tauri desktop host (Rust) ────────────────┐
│ window chrome · menus · credential store · WebView Studio │
│ warcprox capture routing · updater · local REST (opt-in)   │
└───────────────┬───────────────────────────────────────────┘
                │ JSON lines (versioned contract)
┌───────────────▼──────────── Application service (Python) ─┐
│ jobs · projects · settings · certification · experiments   │
│ ┌──────────────┐ ┌──────────────┐ ┌─────────────────────┐ │
│ │ Collection   │ │ Matching     │ │ Research & export   │ │
│ │ httpx/Scrapy │ │ rules/Splink │ │ PROV · RO-Crate     │ │
│ │ OAI · docs   │ │ dedupe/embed │ │ certificates · TSA  │ │
│ └──────┬───────┘ └──────┬───────┘ └──────────┬──────────┘ │
│        │ evidence       │ Parquet             │ packets    │
│ ┌──────▼────────────────▼─────────────────────▼──────────┐ │
│ │ SQLite (transactions) · DuckDB/Parquet (analytics)     │ │
│ │ WARC evidence store · pywb replay · Merkle index       │ │
│ └────────────────────────────────────────────────────────┘ │
└────────────────────────────────────────────────────────────┘
```

New tables: `record_evidence`, `certificates`, `timestamps`, `source_certifications`, `experiments`, `experiment_metrics`, `gold_labels`, `dmp_answers`, `plugins`.

---

## 12. Roadmap

The phases assume one developer; durations are in working weeks. Items marked ✅ are done.

| Phase | Weeks | Scope | Exit criteria |
| --- | --- | --- | --- |
| **0. UX chrome** | ✅ this change | Menu bar, title-bar command center (project location plus command search), Settings in the title bar, sidebar buttons removed, Scraping and Settings sub-navigation, compact density | Menus work in the desktop app; no page scroll for primary actions on the redesigned screens |
| **1. Evidence and certificates** | 3 | A1, A2, A5: always-on evidence, Merkle trees, Ed25519 certificates, RFC 3161 and OpenTimestamps, verifier CLI | Tamper tests fail verification; offline timestamp verification |
| **2. Source attestation** | 2 | A3, A4: packets, offline `verify.html`, countersignature import, certification tracker, PROV and RO-Crate exports | A test "source" verifies and countersigns a packet on a clean machine |
| **3. Evaluation harness** | 3 | C2: benchmarks, metrics with confidence intervals, gold labelling, experiment registry, LaTeX/SVG export | Reproduces published baselines within ±2 F1 |
| **4. Engines** | 3 | C1, C3: Splink, dedupe, and embedding matchers behind one contract; transliteration; geo and temporal evidence | All engines evaluated on the same benchmark table |
| **5. Scale** | 2 | D1: DuckDB/Parquet storage, SQL blocking, virtualized grids | 1M-row match under 15 minutes |
| **6. GPL tooling and new sources** | 2 | B1, B2: pywb replay, warcprox Studio capture, OCRmyPDF, Docling bundled, OAI-PMH, GLEIF, Crossref | Studio WARC replays with data calls; OAI harvest resumes |
| **7. Quality and analytics** | 2 | D2, D3: validation rules, Presidio PII, dashboards, maps, notebook bridge | Quality score and PII report on every dataset |
| **8. Publication workflow** | 2 | E: research mode, DMP, ethics checklist, datasheets, citations, Zenodo deposit, reproduce run, paper assets | Full paper asset bundle generated from one project |
| **9. UX polish and study** | 2 | F3: onboarding, notifications, Arabic RTL, accessibility audit, usability study (S6) | S5 and S6 met |
| **10. Hardening and release** | 1 | G: E2E tests, packaging, signed installer, documentation, demo dataset | Clean install on a fresh Windows VM; thesis demo script runs end to end |

**Total: about 22 weeks.** If time is short, the minimum publishable path is **0 → 1 → 2 → 3 → 8** (about 12 weeks): certified collection, quantitative evaluation, and publication exports.

---

## 13. The research paper

What DataForge will produce for each chapter:

| Chapter | Evidence DataForge generates |
| --- | --- |
| **Methodology: data collection** | Sources table (host, purpose, signals, request counts, dates); politeness statistics; preset definitions as appendix; PROV diagram |
| **Methodology: data processing** | Normalizer list with versions; matching configuration; blocking diagnostics |
| **Evaluation** | Benchmark tables (P/R/F1 with 95% CI) for each engine; PR curves; runtime and scale charts; ablations (without transliteration, without geo evidence) |
| **Case study** | The user's own dataset: record counts through each stage (collected → valid → deduplicated → reviewed), review effort, quality score |
| **Validity and trust** | Certification tracker results (sources contacted, certified, rejected); verification results; reproduction diff |
| **Ethics** | Completed ethics checklist, DMP, PII handling report, signals ledger |
| **Appendix** | Software citations (dependency inventory with licenses), RO-Crate DOI, certificate example |

**Threats to validity the app helps address:** source drift between collection and certification (evidence replay), label bias (κ agreement, cleanlab), benchmark mismatch (the case study uses the user's own data), and reproducibility (manifests and hash diffs).

---

## 14. Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Sources decline or ignore certification requests | Independent RFC 3161 and OpenTimestamps proofs still establish time and integrity; the tracker records outreach for the paper; prioritize sources with contactable data teams |
| Free TSAs unavailable | Support several TSAs and retry; OpenTimestamps as a second anchor; certificates remain signed without a timestamp and can be timestamped later |
| Scope exceeds graduation timeline | Minimum publishable path (Section 12); each phase ships independently |
| GPL obligations when distributing binaries | Publish full source alongside the thesis; license inventory included; academic non-commercial distribution |
| Antivirus blocks packaged builds | Exceptions on dev machines; code signing; alternative packagers |
| Evidence storage grows large | Deduplicated, compressed WARCs; per-project retention; storage dashboard |
| LLM or embedding components need a GPU | CPU-friendly small models by default; features are optional and evaluated separately |
| Personal data in collected records | Presidio detection, sensitive-column masking (existing), pseudonymized exports, retention limits |

---

## Appendix: library catalog

Checked on PyPI on 2026-09-17. **Status:** Adopt = planned in a phase above; Optional = add-on; Reference = design input only.

| Library | Version | License | Status | Pillar |
| --- | --- | --- | --- | --- |
| prov | 3.2.1 | MIT | Adopt | A4 |
| rocrate | 0.15.1 | Apache-2.0 | Adopt | A4 |
| rfc3161ng | 2.1.3 | MIT | Adopt | A2 |
| opentimestamps-client | 0.7.2 | LGPL-3.0 | Adopt | A2 |
| c2pa-python | 0.37.10 | MIT OR Apache-2.0 | Adopt | A2 |
| python-gnupg | 0.5.6 | BSD | Adopt | A3 |
| weasyprint | 70.0 | BSD | Adopt | A3, E |
| rdflib / pyshacl / pyld | 7.6.0 / 0.40.1 / 3.3.0 | BSD / Apache-2.0 / BSD | Adopt | A4 |
| mlcroissant | 1.1.0 | Apache-2.0 | Optional | A4 |
| frictionless | 5.19.0 | MIT | Adopt | A4, D2 |
| ultimate-sitemap-parser | 1.8.1 | GPL-3.0+ | Adopt | B1 |
| html2text | 2025.4.15 | GPL-3.0+ | Adopt | B1 |
| pywb | 2.9.1 | GPL-3.0 | Adopt | A1, B1 |
| warcprox | 2.13.1 | GPL-2.0+ | Adopt | B1 |
| ocrmypdf | 17.12.1 | MPL-2.0 (+ Ghostscript AGPL) | Adopt | B1 |
| docling | 2.128.0 | MIT | Adopt | B1 |
| unidecode | 1.4.0 | GPL | Adopt | B1, C3 |
| igraph | 1.0.0 | GPL | Adopt | C3 |
| spacy / gliner | 3.8.16 / 0.2.29 | MIT / Apache-2.0 | Optional | B3 |
| splink | 4.0.17 | MIT | Adopt | C1 |
| dedupe | 3.0.3 | MIT | Adopt | C1 |
| recordlinkage | 0.16 | BSD-3 | Adopt | C1 |
| sentence-transformers | 6.0.1 | Apache-2.0 | Adopt | C1 |
| faiss-cpu / hnswlib | 1.15.1 / 0.8.0 | MIT / Apache-2.0 | Adopt | C1 |
| jellyfish | 1.2.1 | MIT | Adopt | C3 |
| scikit-learn / lightgbm | 1.9.1 / 4.7.0 | BSD-3 / MIT | Adopt | C1, C2 |
| cleanlab | 2.9.0 | Apache-2.0 | Adopt | C2 |
| h3 / geopandas / shapely / pyproj | 4.5.0 / 1.1.4 / 2.1.2 / 3.8.0 | Apache-2.0 / BSD / BSD / MIT | Adopt | C3, D3 |
| duckdb / polars | 1.5.5 / 1.44.2 | MIT / MIT | Adopt | D1 |
| pandera / great-expectations | 0.33.1 / 1.23.0 | MIT / Apache-2.0 | Adopt / Reference | D2 |
| ydata-profiling | 4.18.4 | MIT | Reference | D2 |
| presidio-analyzer | 2.2.364 | MIT | Adopt | D2 |
| ftfy | 6.3.1 | Apache-2.0 | Adopt | D2 |
| pycountry | 26.2.16 | LGPL-2.1 | Adopt | C3, D2 |
| networkx | 3.6.1 | BSD-3 | In use / Adopt | C3 |
| papermill / nbconvert | 2.7.0 / 7.17.1 | BSD / BSD | Adopt | D3, E |
| bibtexparser / citeproc-py | 2.0.1 / 0.11.1 | MIT / BSD | Adopt | E |
| zenodo-client / datacite | 0.4.2 / 1.4.1 | MIT / BSD-3 | Optional | E |
| label-studio / argilla / snorkel | 1.23.0 / 2.8.0 / 0.10.0 | Apache-2.0 | Reference | C2 |
| mutmut / pytest-benchmark / locust | 3.8.0 / 5.3.0 / 2.46.5 | BSD / BSD / MIT | Adopt | G |
| briefcase / nuitka | 0.4.5 / 4.2.1 | BSD / AGPL-3.0 | Optional | G |

Out of scope by principle (not license): CAPTCHA solvers, proxy rotation, fingerprint spoofing, stealth or headless browsers that bypass site protections, and login automation for sites that do not offer an API.
