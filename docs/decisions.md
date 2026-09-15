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
