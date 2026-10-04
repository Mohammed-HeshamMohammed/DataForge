# Implementation Decisions

Short records of choices made while building. Each can be revisited when the stated trigger occurs.

## D1. Host-to-service boundary: one Python sidecar over JSON lines

The Tauri host starts the application service and forwards `service_call(command, payload)` as one JSON line per request. In development it runs `python -m dataforge_application.server`. Packaged builds run the PyInstaller executable from the bundle's `service/` resource folder, so no Python install is needed. Long work runs as jobs on service threads, so calls return quickly.
**Revisit when:** concurrent commands become a measured bottleneck.

## D2. Workers run in-process inside the service

The service calls the matching and scraping workers as Python modules through their versioned request/result contracts, on job threads with their own SQLite connections. Process isolation from the UI comes from the sidecar itself.
**Revisit when:** a worker crash or CPU load affects the service's responsiveness, or workers need independent dependency upgrades.

## D3. No Polars yet

Blocking and scoring use plain Python structures with RapidFuzz. Measured: 48,533 person rows match in about 3.4 s.
**Revisit when:** jobs above ~100k rows or memory pressure are measured.

## D4. Auto-match needs two comparable fields

Beyond the spec's rule, a non-identifier auto-match also needs at least two comparable fields. One shared phone with nothing else to compare goes to review instead.

## D5. Email and phone differences are not cluster conflicts

People and businesses legitimately have several of each, so they are set-valued evidence. Only identifiers (per column), house numbers, `must_not_link` constraints, and locks block a union.

## D6. Hard contradictions are `non_match`, not review items

Guarded pairs are stored as `non_match` with a "Cannot auto-merge" reason. The review queue holds `possible_match` pairs and rejected cluster bridges.

## D7. Scrape Studio architecture

- **Rendering:** a native Tauri child WebView (`Window::add_child`, Tauri `unstable` feature) placed over a placeholder in the Studio tab. It is never an iframe and never an external browser.
- **Sessions:** each Studio visit opens an **incognito session** (the open decision "per job or per workspace" resolved to "per Studio session"). No cookies or logins persist.
- **Scope:** the host's `on_navigation` enforces the preset's allowed hosts, independently of the UI.
- **Bridge:** the page script exposes five fixed functions. The host builds calls only from an allow-list with JSON-encoded arguments and reads results with `eval_with_callback`. Picks are polled, so the page has no channel into DataForge.
- **Staging:** extracted values go to `scrape.stage_rendered`, where Python applies transforms, validation, dedupe, the test cap, and provenance.
- **Stops:** login forms and challenge markers stop the run.
- **Threading:** Studio commands must be `async` Tauri commands; creating a child WebView from a synchronous command deadlocks the event loop.

## D8. Path validation and loopback bridge

Project paths must be absolute directories and cannot be a filesystem root. Import paths must be absolute existing `.csv`, `.json`, or `.xlsx` files. The HTTP dev bridge binds to loopback only.

## D9. Credentials never cross the UI boundary

Secrets live in the OS credential store (Windows Credential Manager via the `keyring` crate). The UI can save, delete, and list names, but never read values.

For API presets, the UI sends `credential_ref`. The host resolves it and injects `credential_secret` into the service request, and rejects any request where the UI supplied a secret itself. The service pops the secret into in-memory job secrets, so it is never written to `params_json`, events, or logs. The runtime sends it only as a header to the preset's hosts, and redirects out of scope are refused. Retrying after a restart resolves it again from the store.

## D10. Updates use the Tauri updater with a pinned GitHub endpoint

The spec calls for a signed manifest verified with an embedded Ed25519 key. The Tauri updater provides exactly that: a minisign (Ed25519) signature per artifact, verified against the public key in `tauri.conf.json` before install.

- **Endpoint:** the repository is configured in Settings, and the host builds the only allowed endpoint URL (`github.com/<owner>/<repo>/releases/...`). The download host must be GitHub.
- **Approval:** installation requires explicit approval and is refused while jobs run. Database backups happen on the next project open before migrations.
- **Keys:** the development signing key lives in `%USERPROFILE%\.dataforge\keys`, outside the repository. Releases use environment-scoped CI secrets.

## D11. Preset packages are Ed25519-signed JSON (`.dfpreset`)

Packages are signed over canonical JSON with the `cryptography` library.

- **Trust:** keys come from `packages/presets/trusted_keys.json` (reviewed, shipped) and `%LOCALAPPDATA%\DataForge\trusted-preset-keys.json` (per machine).
- **Install gate:** a package installs only if every preset validates and passes its bundled fixture health check.
- **Versions and rollback:** bundled preset versions cannot be shadowed. Rollback removes the newest installed version; jobs keep their pinned copy.
- **Health status:** fixture health checks mark a preset `degraded` on failure and `disabled` after 3 consecutive failures.

## D12. Review ranking is ordering-only

A logistic model trained only on human merge/keep-separate decisions (at least 20, both outcomes) reorders the review queue. It stores its model version, feature version, training hash, and holdout metrics. It never creates matches, and there is no ML auto-merge path.

## D13. Curated presets use documented public APIs

Wikipedia (MediaWiki search API) and Hacker News (Algolia search API) are the first curated presets.

Amazon and Zillow presets are **not** shipped. Their terms prohibit automated collection, and the spec requires policy review and a named maintenance owner before a site-specific preset is published. The generic presets and Scrape Studio still work for any site a user is authorized to collect from.

## D14. Cross-dataset matching reuses one engine with per-source mappings

A match job may add `compare_dataset_id`. Both datasets need confirmed mappings with the same entity type, and they must share at least one identifier, contact, address, or URL role. Their column names may differ.

- **Normalization:** each row is normalized with its own dataset's mapping.
- **Comparison:** pairs are compared within and across datasets.
- **Survivors:** `settings.source_trust` orders the survivor choice.
- **Canonical output:** role fields such as `canonical.phone` are added so values from differently named columns line up.
- **Storage:** constraints, locks, and reviewer choices are stored under the primary dataset and read across both.
- **Staleness:** the preview config hash includes both mapping versions and the trust order.

## D15. Reviewer choices and mapping reports are durable, scoped, and reversible

- **Chosen values:** "Choose values" on a merge, or per-field choices on a group, write `canonical_overrides` rows keyed by dataset, column, and row. They apply to whichever group contains that row, so they survive re-clustering and future runs. Undoing the merge revokes the choices made with it.
- **Bad mappings:** "Mark bad mapping" records a report without resolving the pair. Saving a new mapping version resolves all open reports for that dataset.
- **Export exclusions:** these are stored on the mapping version. They remove columns and derived role fields from every export while keeping those columns as matching evidence.

## D16. Test runs use the same collection path as full runs

In Scrape Studio, "Test 10 records" runs the full-run collection logic, capped at 10 records. Detail-link tests visit up to 10 item pages, next-link tests read one page, and infinite-scroll tests skip scrolling. A test therefore proves the exact pagination the full run will use. In the HTTP runtime, `detail_links` skips out-of-scope links with a warning rather than following them, and HTML `cursor` pagination reads a token from a selector into a query parameter.

## D17. Test strategy by layer

- **Matching:** Hypothesis property tests guard the invariants: bounded and deduplicated candidates, no conflicting house numbers within a group, and formatting-independent normalization.
- **Contracts:** `packages/contracts/*.schema.json` is the shared contract. Python tests validate live service responses and worker results against it, including a rule that job params never contain a credential secret.
- **UI:** Vitest with jsdom and Testing Library covers behaviour; axe-core covers accessibility rules that do not depend on layout. Colour contrast needs a real browser and stays manual.
- **Real app:** Studio flows were verified in the actual desktop app, driving WebView2's local debugging port. `apps/desktop/vite.config.ts` pre-bundles the lazily imported Tauri modules so the dev server cannot reload the page mid-session.

## D18. Every collection declares a purpose, and site signals are applied to it

Scrape and archive jobs require a purpose. Before the first request to a host, DataForge reads robots.txt (Protego, RFC 9309), `/.well-known/tdmrep.json`, and ai.txt; each response is also checked for `tdm-reservation` and `Content-Usage`.
- **robots.txt errors:** 5xx or unreachable means disallow-all; 401/403 means disallow-all; other 4xx means no restrictions. Wikimedia sites answer 403 without a contact User-Agent, so they are refused until one is configured.
- **TDMRep:** a reservation stops every purpose, since collecting for analysis is text and data mining.
- **AIPREF:** `bots=n` stops every purpose; `train-ai`/`train-genai` govern AI training and `search` governs search indexing.
- **ai.txt:** recorded as advisory provenance only.
Signals are stored per run (`usage_signals`) so audits show what applied.

## D19. TLS uses the operating system trust store

Desktop users sit behind corporate proxies and antivirus TLS inspection (this development machine inspects TLS). Python's bundled certifi roots reject those chains, so the httpx client uses `truststore` and the Scrapy engine builds its trust root from the Windows certificate store. Certificate verification is never disabled.

## D20. Two engines, one extraction path

`extract_page` is the only place records are produced; the httpx runtime, the Scrapy spider, health checks, Studio staging, and archive replays all call it, and both engines share `validate_candidates`. Scrapy runs as a child process (the packaged service's `--engine scrapy` entry point) because Twisted's reactor cannot live in job threads. `engine: auto` picks Scrapy only for sitemap or crawl jobs above 200 pages; tests always use httpx. Parity is tested on a fixture site and was confirmed live.
**Revisit when:** Scrapy's per-host concurrency is needed for jobs where the child-process start-up cost matters.

## D21. Library substitutions made during implementation

- **cdx-toolkit → CDX over the policy client.** cdx-toolkit uses its own `requests` session, which would bypass the scheduler, robots checks, and trust store. The CDX protocol is small.
- **camelot-py → optional extra.** Version 2.0 requires numpy, pandas, and OpenCV. pdfplumber's line strategy covers ruled tables in the base install.
- **rfc3987 excluded.** Spidermon requests `jsonschema[format]`, which installs rfc3987 (GPL-3.0-or-later). jsonschema imports it optionally, so the PyInstaller build excludes it and `scripts/check-licenses.py` gates everything else.

## D22. Watches run only while DataForge is open

A background thread starts due watches every 30 seconds while a project is open. Each run stages a new dataset; `watch_runs` links it to the previous version with a diff keyed by the preset's `unique_by`. Nothing runs when the app is closed, consistent with local-first operation.

## D23. Suggestions never apply themselves

Relocation suggestions, example-based selectors, and draft proposals all return data for review. Proposals must extract records from the page they came from before they are shown. Remote models need per-project consent and receive redacted text; the exact payload is returned.

## D24. Scrape Studio collections obey the same site signals

Browsing in Studio is the user's own navigation, so only scope is checked. Collection runs are automated: before the first page and before every next-link or detail-link navigation, Studio calls `scrape.check_url` with the declared purpose, which applies robots.txt, TDMRep, and AIPREF through a per-purpose checker cached for ten minutes. A robots.txt disallow on one detail link skips that link; anything else stops the run. `scrape.stage_rendered` requires the purpose, re-checks every page URL, and records the signals on the run.

## D25. Resilience features close the loop without applying changes

- **Fallback selectors:** Studio picks store the CSS selector plus XPath fallbacks. A label-anchored XPath is offered only when the caption looks like a label and repeats across items; a structural XPath is always offered. Both engines and the Studio bridge try selectors in order.
- **Health checks:** a failing selector preset gets suggested fixes from the newest stored fingerprints of any version.
- **Fixtures from captures:** `preset.fixture_from_capture` sanitizes a captured page (scripts except JSON-LD, comments, frames, form values, tokens in meta tags and URLs, contact details) into `project:fixtures/...`, which custom presets can reference.
- **Watches and credentials:** scheduled runs start without the desktop host, which alone reads saved credentials, so presets that need one cannot be watched.
- **Scrapy runs:** a per-run work folder is deleted after the run; a resume folder (JOBDIR) is kept after a cancel or failure so a retry continues, and deleted after completion. Incremental watches use deltafetch through an async-compatible wrapper, because scrapy-deltafetch 2.1.0 does not support Scrapy 2.19's async spider output.

## D26. Academic licensing policy

DataForge is a graduation project used in a research paper, so GPL, AGPL, LGPL, and MPL components are allowed when they add value (see `DATAFORGE_MASTER_PLAN.md`, principle 6). `scripts/check-licenses.py` now prints an inventory for the thesis software-citation appendix (`--markdown`) and exits 0; `--strict` restores the permissive-only gate. Redistributed binaries that bundle GPL components are covered by the GPL, which publishing the source alongside the thesis satisfies. Collection policy (robots.txt, TDMRep, AIPREF, stop rules, no evasion) is unchanged: it is what makes source certification credible.

## D27. Window chrome: menu bar, project location, command palette

- **Title bar:** a menu bar (File, Edit, Selection, View, Go, Run, Help), then a wide **command center**, then Settings, theme, updates, minimize, maximize, and close.
  - The command center shows the open project's name and folder. Clicking it (or Ctrl+K / Ctrl+Shift+P) turns it into a search field; results, including project actions and recent projects, drop down directly beneath it.
  - The left sidebar holds no buttons, and the sidebar project card was removed.
- **One command registry:** `lib/commands.ts` drives the menus, the palette (Ctrl+K or Ctrl+Shift+P), and global shortcuts. Text fields keep native editing keys; Edit → Undo sends `dataforge:undo`, which the review queue handles.
- **Less scrolling:**
  - the pane header is one row;
  - Scraping splits into Collect, Site signals, Watches, and Customize preset, with Collect in two independently scrolling columns;
  - Settings shows one section at a time;
  - long tables scroll inside their own area.

  At 1366×768 no tab needs a page scroll.
- **View options:** sidebars can be hidden (Ctrl+B, Ctrl+J), compact density, native WebView zoom (Studio bounds are scaled to match), and full screen. These need the `set-fullscreen`, `is-fullscreen`, `set-webview-zoom`, and scoped `open-path` capabilities.

## D28. Extend the existing engines instead of adding duplicate browser stacks

DataForge keeps three execution paths: policy-controlled HTTP/httpx, Scrapy for large crawls, and the native embedded WebView for rendered pages. Missing capabilities are added to those paths rather than bundling Selenium, Playwright, Crawlee, or another browser runtime.

- **Structured protocols:** XML/SOAP is a selector-free extraction mode; GraphQL reuses the API POST/JSON runtime and permits read-only queries only.
- **One Studio entry point:** Scrape Studio's collection-method selector routes websites, APIs, XML/SOAP, sitemaps, feeds, crawls, repositories, documents, archives, bulk corpora, and local-file imports to their purpose-built workflow with the method already selected.
- **Rendered pages:** the native WebView can query the top document, open Shadow DOM, and same-origin frames; it supports bounded infinite scroll, load-more buttons, detail links, same-origin JSON capture, and scoped downloads.
- **Interactive authentication:** a user may sign in manually in the visible, incognito Studio WebView. The bridge never reads form values, cookies, storage, request headers, or hidden inputs; collection stays blocked while a password field or challenge is visible. DataForge never fills, stores, or submits credentials for a page.
- **Downloaded data:** CSV, JSON/JSONL, XLSX, XML, Parquet, DOCX, ZIP, and GZIP enter through the normal immutable dataset importer. Archives must contain one supported file and obey compressed-size, expanded-size, ratio, and row limits.
- **Safety boundary:** cross-origin frames, request headers, cookies, storage, form values, login automation, CAPTCHA solving, proxy rotation, fingerprint evasion, and out-of-scope downloads remain unavailable.

**Revisit when:** a verified source requires a browser capability WebView2 cannot provide, and adding another runtime has a measurable production benefit greater than its installer size and maintenance cost.

## D29. Authorized network and authentication interoperability

- **Organization proxy:** collection workers can use one project-configured HTTP(S) proxy URL. The URL cannot contain credentials, and the route is never rotated or changed after a block. Scrape Studio continues to follow the operating system WebView proxy configuration.
- **API authentication:** bearer tokens, named API-key headers, query parameters, HTTP Basic, and OAuth 2 client credentials use named secrets in the OS credential store. OAuth access tokens exist only in worker memory, the token endpoint must be HTTPS and in the preset host allow-list, and neither credentials nor tokens enter job parameters, logs, artifacts, or project files.
- **Interactive authentication and MFA:** a user may complete sign-in and MFA personally in the visible incognito Studio WebView. The temporary session expires after 30 minutes and has an explicit end-session action that destroys the WebView and its cookies/storage.
- **Challenge fallback:** CAPTCHA or bot-challenge detection still stops collection and links the user to the official-API workflow.
- **Browser-tool compatibility:** for localhost, RFC 1918, and `.local`, `.internal`, or `.test` hosts, Studio can copy plain Playwright and Selenium extraction scripts from reviewed selectors. These scripts contain no credential handling, challenge solving, stealth, fingerprint modification, or proxy rotation. DataForge does not bundle or launch a second browser runtime.


## D30. Built-in listing and product collectors in Scrape Studio

- **What:** Scrape Studio reads listings (real-estate search pages, Zillow included) and Amazon search results without element picking. The bridge actions `listings`, `products`, and `nextPage` read the rendered page: embedded JSON (`__NEXT_DATA__`, JSON-LD, Next.js flight data, window state), the JSON and flight responses the site loads while the user browses, and repeated price cards as a fallback. Rows use the field keys of the bundled `generic.listings` and `generic.products` presets.
- **New extraction mode `collector`:** the preset declares fields but no selectors; the bridge hands over finished rows and the backend keeps only the declared fields, validates, de-duplicates, and stages them through `scrape.stage_rendered`. Collector presets must allow only the `webview` strategy, so HTTP runs refuse them. Fixture health checks run the field mapping on recorded rows (`fixtures/collector/*.json`).
- **Where rows live:** the Studio WebView is incognito, so collected rows are held by the app (and mirrored to app storage up to about 3 MB) until the user saves them as a dataset or clears them. Closing or reloading the Studio browser does not lose them.
- **Pages:** the app navigates the visible WebView to each next page with the preset's minimum delay, checks scope and site signals before reading each page, stops when a page repeats rows already collected (the site paginates in the browser), and stops at challenges or login pages. Craigslist is read page by page only, because its terms forbid automated browsing.
- **Privacy:** agent, contact, owner, and user fields are skipped; phone numbers and e-mail addresses are removed from free text. The bridge still fetches, stores, and sends nothing on its own.

- **Details:** the `details` action reads one listing or product page (Zillow home data and history tables, Amazon product sections, or any site's main listing and labelled facts). Studio opens each collected item in turn with the preset's delay, checks it first, and merges the fields into the row.
- **Zillow coverage:** Zillow's robots.txt excludes the map-move request and `?searchQueryState=` addresses but allows area, ZIP, result-page, and path-filter pages, each of which carries up to 500 map pins. Larger coverage therefore comes from sweeping allowed pages (Studio fills the ZIP pages of the area from the ZIP codes already collected), never from automating map moves.
- **Excel:** `dataset.export_workbook` writes any dataset as the formatted workbook (Summary with live formulas, table, Details, Photos) with the same layouts as the collector scripts.
- **Public records:** Harris County tax sales, foreclosure notices, and the HCAD appraisal roll run as jobs from the Public Records tab and create datasets. The HCAD output keeps owner names and mailing addresses as published but shows only the Homestead exemption.

**Revisit when:** a supported site moves its results into a format the engine cannot read (it then falls back to the visual builder), or another county's records are needed (each county publishes its own formats).

## D31. People-aware matching, measured against an answer key

- **Why:** on 2,636 synthetic contacts with a known answer key (nicknames, initials, "Last, First", typos, phone formats, Gmail variants, missing fields, same-name strangers, and household members sharing a phone and address), Safer mode merged 85% of duplicate pairs automatically, made 9 wrong merges (all household members), and filled the review queue with 535 pairs of which only 40% were duplicates.
- **What changed (policy `deterministic-1.1.0`, normalization `1.2.0`):** for `entity_type: person` (or any mapping with first/last name roles) names are compared as given names and surname; a different first name is a contradiction, both for a pair and for a whole group, so a vague record such as "S. Rodriguez" cannot join Susan and Samantha; emails are compared as the mailbox they deliver to; candidate groups use both the ZIP prefix and the city, and records without a phone or email are also paired by a surname that sounds alike (Soundex) plus a first initial their name or its nicknames allow. Companies, properties, and products keep whole-name comparison.
- **Result:** Safer mode makes no wrong merges on that set and on a held-out set (new seed, middle initials, titles, hyphenated surnames, nicknames missing from the table); it merges 91% automatically and the review queue shrank to 175 pairs. Flexible mode merges 98%. On 50,000 rows the run takes about as long as before (a plain-US-number fast path for phones offsets the extra comparisons).
- **Trade-off:** a nickname missing from the table reads as a different first name, and such a pair is kept apart without review unless the records share an email. The rule only limits automatic grouping: a reviewer's merge of a pair in the queue is never blocked by it.

**Revisit when:** real review decisions show first-name contradictions overruled often (add the nicknames, or relax the rule to review), or a non-English naming convention (patronymics, family name first) needs its own parser.


## D32. Fix values before matching, as a cleaned copy

- **What:** Clean & Combine gained a "Fix values" step between explaining columns and checking duplicates. `dataset.cleanup_scan` reads only the chosen dataset and reports, per column role, the formats it would standardize (names, phones, emails, ZIP/postal codes including leading zeros Excel dropped, US states, USPS street suffixes and unit words, URLs, IDs stored as numbers), invalid and placeholder values (N/A, 000-000-0000, noemail@…, Excel errors), junk rows (empty, exact duplicates as typed, test entries), and variant spellings grouped OpenRefine-style by fingerprint, character n-gram fingerprint, or company name without legal form.
- **Choices stay with the person:** every fix is a checkbox with before/after examples; invalid values are kept unless the person clears them, and the value kept for a group of spellings is editable. Nothing outside the dataset is consulted or added.
- **Never in place:** `dataset.cleanup_apply` writes a new dataset of kind `cleaned` that keeps the original row numbers, records `parent_dataset_id` and the plan with a change summary (migration 011), and copies the column mapping. The imported rows are unchanged, so cleanup can be redone differently.
- **Measured:** on the matching answer-key sets with typical spreadsheet damage (dropped ZIP zeros, email domain typos, N/A phones), cleaning first raised automatic merges from 86.6% to 90.3% (held-out set 81.6% to 85.3%) and shrank the review queue by about a fifth, with no wrong merges either way. Scanning or applying 50,000 rows takes about 2 seconds.

**Revisit when:** people need fixes that depend on more than one column at once (for example splitting a full address into parts), or regions other than the US need their own state and postal rules.
