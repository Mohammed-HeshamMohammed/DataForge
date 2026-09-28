# ADR-001: Constrained row-driven browser automation

**Status:** Accepted
**Date:** 2026-09-28
**Deciders:** DataForge maintainers

## Context

DataForge users need to apply a repeatable set of website actions to rows imported from CSV, Excel, and the other supported dataset formats. The feature must remain local-first, preserve reusable workflow definitions, coexist with Scrape Studio, and avoid turning imported data into arbitrary executable code.

## Decision

Add a first-class Automation tab. A workflow binds one project dataset to one HTTPS host and an ordered list of allow-listed actions: click, fill, wait, and read. Fill values may interpolate `{{column}}` placeholders from the current row. Definitions are stored in project SQLite; row values and credentials are not stored in workflow definitions.

Execution uses a dedicated incognito Tauri child WebView with its own host allow-list and fixed JavaScript bridge. It does not share Scrape Studio's WebView or session. The bridge rejects password and file inputs, hidden targets, form submit controls, controls inside password-bearing forms, downloads, cross-host navigation, and arbitrary JavaScript. The UI requires an authorization acknowledgement and caps a run at 500 rows.

## Options Considered

### Dedicated constrained Tauri WebView

| Dimension | Assessment |
| --- | --- |
| Complexity | Medium |
| Cost | Uses existing Tauri/WebView infrastructure |
| Scalability | Suitable for bounded, interactive desktop runs |
| Team familiarity | Reuses Scrape Studio patterns |

**Pros:** Local, visually recordable, same browser engine as the desktop app, narrow security boundary, no new runtime.

**Cons:** Runs require the desktop app to remain open; selectors can break when a website changes; this first version is limited to 500 rows.

### Playwright worker with arbitrary scripts

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Cost | New browser/runtime packaging and update burden |
| Scalability | Better for headless batch work |
| Team familiarity | Generated snippets already exist, but no managed runtime does |

**Pros:** Rich automation and headless execution.

**Cons:** Large attack surface, arbitrary code risk, credential handling pressure, larger installer, and duplicated browser infrastructure.

### OS-level macro recording

| Dimension | Assessment |
| --- | --- |
| Complexity | High |
| Cost | Platform-specific implementation |
| Scalability | Poor |
| Team familiarity | Low |

**Pros:** Can drive almost any application.

**Cons:** Fragile coordinates, broad permissions, no DOM semantics, and weak reproducibility.

## Trade-off Analysis

The constrained WebView gives up unrestricted scripting and unattended background runs in exchange for predictable, reviewable workflows and a smaller security boundary. It is the best fit for DataForge's local-first and provenance-focused design. A future headless runner can consume the same workflow schema if it implements the same restrictions.

## Consequences

- Website automation is explicit and inspectable rather than code hidden in a spreadsheet.
- Scrape Studio and Automation retain isolated sessions and host policies.
- Users must complete sign-in and MFA themselves; workflows cannot bypass access controls or CAPTCHA.
- Runs are interactive and currently return their read results in the tab; durable run history/export is a follow-up.
- Selectors and action schemas can be versioned without changing imported source datasets.

## Action Items

1. [x] Add workflow CRUD, schema migration, UI builder, target picker, template substitution, and guarded execution.
2. [x] Add separate Automation WebView state and commands.
3. [x] Add backend, desktop, and Rust tests.
4. [ ] Add durable run history and CSV export after the interaction model is validated in the native Tauri window.
5. [ ] Add resumable batching and per-site pacing before raising the 500-row cap.
