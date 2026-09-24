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

## D28. Record details: every record, every level, one policy

- **Default levels.**
  - The service defaults to `standard`: value, element, and page details with no extra requests. Library callers, watches, and existing presets therefore make the same requests as before.
  - The desktop Scraping tab defaults to **Full**, which reads each record's detail page. That is where list pages keep most of their data.
  - Scrape Studio defaults to **Standard**, because its presets render rather than fetch.
- **One place per group.** So that every path produces identical records:
  - value details run in `_with_provenance`, the choke point every mode already passes through;
  - element details run in the three selector parsers, from the element's outer HTML;
  - page metadata runs in `extract_page`.

  This covers both engines, health checks, archive replays, and Studio staging.
- **Detail pages use the job's own `get`.** Following them reuses scope checks (including redirects), site signals, the politeness scheduler, the cache, and WARC capture.
  - A stop signal ends following but keeps the collected records: collection already happened lawfully, and nothing is retried around a block.
  - On the Scrapy engine, the parent follows detail pages with the httpx policy client after the crawl. The pages are few and need no frontier, and records match the httpx engine.
- **Namespaced keys, never overwrites.** Keys are `<field>.<detail>`, `item.*`, `page.*`, and `detail.*`, so exports stay traceable and a preset's own fields always win.
  - Watch diffs ignore `page.*`, `detail.page.*`, and `detail.retrieved_at`, which are page-wide and often timestamped.
  - `field_coverage` stays about the preset's fields; details get their own summary.
- **Studio sends sanitized copies.** The WebView bridge strips scripts, form controls, secret-looking values, event handlers, and security meta tags before an element or page head leaves the page. Copies are capped at 40,000 characters per record (raised from 8,000 so large retail cards keep their prices, ratings, and badges).
- **Mappings are data, generated.**
  - `schemaorg_mappings.json` had never been committed, because `Data/` in `.gitignore` also matches `data/` on Windows. It is rebuilt with 23 types, generated by `scripts/schemaorg-mappings.py`, and explicitly un-ignored.
  - Field specs gained fallback paths and joined multi-values.
  - Markup after `</html>` is now read.
- **Scrapy child imports.** When running from source, the Scrapy child process gets the worker's `src` on `PYTHONPATH`, so it no longer depends on an editable install.

## D29. Item pages are part of every preset, not a separate job

A grid of products and each product's own page are one collection, so item pages are configured on the preset (`details.follow`), not as a second job.

- **HTTP presets:** the job's policy-checked client opens each record's link.
- **Scrape Studio:** the embedded WebView opens each item's page, one delay apart. Every page is checked with `scrape.check_url` and re-checked by the service. A bot check or login stops the loop but keeps the records already collected.
- **Custom item-page fields come first** and win over automatic values with the same key. A preset author who picks the price on the item page gets that price, not the one in the structured-data offer.
- **Studio picks on an item page** use absolute CSS paths with `<body>`-anchored XPath fallbacks. The bridge returns a sanitized whole-page copy, so structured data, specifications, and text are read the same way as over HTTP.
- **Bot checks used by large retailers** (Amazon's robot check, HUMAN/PerimeterX, Imperva) were added to the challenge markers. They stop collection like reCAPTCHA does; nothing is solved or evaded.

## D30. The Overview is computed, not stored

`project.overview` aggregates the existing tables on each call, adding no caches or counters that could drift. It uses migration `010_record_details.sql`, which adds `detail_level`, `detail_pages`, and `detail_fields` to `scrape_runs` so detail totals are cheap sums.

- **Local days:** activity is bucketed by the viewer's local day (the UI sends `getTimezoneOffset()`).
- **Records collected** counts completed full runs, because test runs are not staged.
- **Attention items** are ordered by severity and each names the tab that resolves it.
- **Chart:** one series in a chart-specific token. The app's bright dark-theme accent fails the lightness band on the panel surface, so the dark theme uses `#2fa866` and the light theme `#16a36a`; both are validated against the panel surface. The chart has thin 4px-rounded columns, labels only on the busiest and latest days, a hover and focus readout, and a table view.

## D31. Releases bump the version themselves

- **One source of truth:** `tauri.conf.json`. `scripts/bump-version.py` mirrors it into the npm, Cargo, and Python manifests, the lockfiles, and `dataforge_application.__version__`. CI checks they agree.
- **Reading the version:** the UI imports it from `tauri.conf.json` instead of a hand-written constant. The old constant said 0.2.0 while every manifest said 0.1.0, so all files were aligned to 0.2.0, the version users already saw.
- **Triggering:** the Release workflow is started from the Actions tab with a part (patch, minor, major) and channel (stable, beta), or an exact version. It bumps, commits `[skip ci]`, tags, then builds and publishes in the same run, because tags pushed with `GITHUB_TOKEN` do not start other workflows.
- **Safety:** a `concurrency` group serializes releases; a run fails if the version would not increase or the tag already exists.


## D32. Item pages are read by default, in the background

- **Default:** service jobs default to the `full` detail level, so every record's own page is read (one extra request per distinct item link). The library default stays `standard` so offline and test callers never make requests they did not ask for.
- **Studio background mode:** the WebView stays the only rendering surface and stays on the grid. Item pages are read by the service's policy-checked HTTP client, the same client every HTTP preset uses, rather than by a hidden or second WebView. A hidden browser would be a separate rendering surface outside the visible one, and it would not be faster: the network and the politeness delay dominate, and parsing takes milliseconds.
- **Progress is drawn from job events:** each item page emits one `detail_page_extracted` event with its outcome, and Studio turns them into card outlines through an allow-listed bridge action. No state is kept in the page; `markItems({clear: true})` removes every trace.
- **Pace:** the user chooses the delay, never below 250 ms (enforced by the service, not only the UI), and a robots.txt Crawl-delay still applies. Sub-millisecond per-page times are not possible for pages fetched over the network; the result reports real `fetch_ms` and `parse_ms` instead.
- **Drafts cannot broaden their base:** HTTP is added to a Studio draft only when its base preset allows it, and the service now checks unsaved drafts against the base, as it already did on save.

## D33. Grid cards are compared, not just parsed

- Fixed element details (`item.price`, `item.rating`, …) find what a card usually has; comparing every card of the page finds what this site's cards have. A structural path that carries a value on at least 15% of the cards becomes an `item.<name>` field, named from its values, then its class words, then its kind.
- Boilerplate is recognised by repetition (identical on 90% or more of the cards) and by accessibility markup (`aria-hidden` duplicates), not by site-specific rules, so the same pass works for any grid and on every parser and engine.
- Results tables show the detected values by default, ranked by coverage; values that repeat on every record, long texts, and page-level metadata stay in the record inspector, where they are still one click away.

## D34. Framed layout and a status bar

- **Three layers:** a darker frame (`--color-frame`: window edges, title bar, status bar), panels for the two sidebars (`--color-panel`, bordered and rounded), and the main pane as the lightest surface. Every panel is inset from the window edges and from each other by one `--frame-gap` (8 px, 6 px in compact density), so the edges read as a single, even gap.
- **Status bar:** a 26 px strip under the panels with icon indicators, each with a count, a tooltip naming what it means, and a click that goes to its detail:
  - left: the local-service block (green *Local*, red *Offline*; click to re-check), the project (open or copy its folder), `⊗` recent failed jobs (Overview), `⚠` datasets without a confirmed mapping (Datasets), and running jobs (toggles the job center, pulses while jobs run);
  - right: pairs waiting for review (Match), datasets and rows (Datasets), zoom when it is not 100% (reset), an available update (Settings → Updates), the two sidebar toggles, and the version (About).
- **One poll:** `project.summary` moved from the workspace to `App`, so the sidebars, pane header, and status bar share one request every 2.5 s instead of polling separately. Without a project the bar shows only the service and the version.
- The project sidebar's dataset list now fills its panel instead of stopping at a fixed height.
