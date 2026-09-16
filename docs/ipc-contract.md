# IPC Contract (schema_version 1)

The desktop host sends one JSON object per line to the application service's stdin and reads one line back. The browser dev bridge accepts the same object at `POST /rpc`.

```json
{ "id": 7, "schema_version": 1, "command": "match.create_job", "payload": { "dataset_id": "...", "run_mode": "preview" } }
{ "id": 7, "schema_version": 1, "ok": true, "result": { "job_id": "..." } }
{ "id": 8, "schema_version": 1, "ok": false, "error": { "code": "invalid_request", "message": "..." } }
```

Command names match `^[a-z_]+\.[a-z_]+$` (enforced by the host). Unknown additive fields must be tolerated. Error codes: `invalid_request`, `unknown_command`, `unsupported_schema`, `no_project`, `jobs_active`, `not_found`, `review_conflict`, `internal_error`.

## Commands

| Command | Payload | Result |
| --- | --- | --- |
| `health.check` | — | `{schema_version, service, status, checked_at}` |
| `project.create` / `project.open` | `path` (absolute), `name?` | project + `recovered_jobs` |
| `project.current` / `project.recent` | — | project or null / list |
| `dataset.import` | `path`, `name?` | `{job_id}` (job result: `dataset_id`, hash, counts) |
| `dataset.list` | — | datasets with latest `mapping_version` |
| `dataset.rows` | `dataset_id`, `offset`, `limit` ≤ 500 | `[{id, row_number, raw}]` |
| `dataset.profile` | `dataset_id` | columns with null rate, distinct count, samples, proposed role, confidence |
| `dataset.mapping` / `dataset.confirm_mapping` | `dataset_id`, `mapping`, `entity_type` | latest mapping / new immutable version |
| `dataset.delete` | `dataset_id` | `{deleted}` |
| `job.list` / `job.get` | `limit` / `job_id`, `after_event_id?` | jobs / job with ordered events |
| `job.pause` / `job.resume` / `job.cancel` / `job.retry` | `job_id` | `{job_id}` |
| `job.start_fixture` | `steps?`, `step_seconds?` | `{job_id}` |
| `preset.list` / `preset.validate` / `preset.save_custom` | — / `preset` / `preset` | presets with `errors` / `{errors}` / `{id, version}` |
| `scrape.create_job` | `preset_id`, `preset_version`, `start_url`, `run_mode` (`test`\|`full`), `policy_acknowledgement`, `max_records?`, `max_pages?`, `dataset_name?` | `{job_id}` |
| `match.create_job` | `dataset_id`, `run_mode` (`preview`\|`full`), `settings` `{strictness, max_block_size, preview_size, default_region}` | `{job_id}` |
| `match.results` | `job_id` | counts, metrics, pending review, samples, exports |
| `match.review_queue` | `job_id`, `offset`, `limit` ≤ 100 | `{total, items, sensitive_columns}` |
| `match.submit_review` | `job_id`, `decision_id`, `action` (`merge`\|`keep_separate`), `expected_version` | `{review_action_id, review_version}` |
| `match.undo_review` / `match.review_history` | `job_id`, `review_action_id` / `job_id` | — |
| `export.create` | `job_id`, `include_provenance`, `allow_unresolved` | `{directory, is_final, files}` |
| `project.summary` | — | per-dataset review progress, totals, active and recently failed jobs |
| `match.clusters` | `job_id`, `offset`, `limit` | multi-member groups with members, survivor, lock state |
| `match.split_cluster` / `match.lock_cluster` | `job_id`, `cluster_id`, `row_ids` (split) | `{cluster_action_id}` |
| `match.undo_cluster_action` | `job_id`, `cluster_action_id` | `{reversed}` |
| `match.train_ranking` | `job_id` | model metadata and holdout evaluation |
| `match.review_queue` | adds `order` (`score`\|`model`) | adds `model_score`, `ranking_model` |
| `preset.health_check` | `preset_id?` | per-preset `{status, fixtures, failures}` |
| `preset.packages` / `preset.install_package` / `preset.rollback_package` | — / `path` / `name` | list / installed package / `{removed, active}` |
| `preset.export_custom` / `preset.import_custom` | `preset_id`, `preset_version` / `document` | export document / `{id, version}` |
| `scrape.check_url` | `preset` or `preset_id`+`preset_version`, `url`, `scope_url` | `{allowed, reason}` |
| `scrape.stage_rendered` | `preset`, `pages: [{url, records, retrieved_at}]`, `run_mode`, `policy_acknowledgement`, `dataset_name?` | `{job_id}` |
| `match.create_job` | adds `compare_dataset_id?`; `settings.source_trust` (dataset ids, most trusted first) | `{job_id}` |
| `match.submit_review` | adds `values?` `{column: row_id}` (merge only) | `{review_action_id, review_version}` |
| `match.set_canonical_value` / `match.undo_canonical_value` | `job_id`, `cluster_id`, `column`, `row_id` / `job_id`, `override_id` | `{override_id}` / `{undone}` |
| `match.flag_mapping` / `dataset.mapping_flags` | `job_id`, `column`, `note`, `decision_id?` / `dataset_id` | `{flag_id, dataset_id}` / open reports |
| `dataset.confirm_mapping` | adds `export_exclude?` (columns) | new mapping version |

`match.results` also returns `compare_dataset_id`, `review_turnaround`, and `mapping_flags`. Its metrics include `stage_seconds`, `rows_per_second`, `cluster_size_distribution`, `decisions_by_scope`, and `survivor_sources`. `match.clusters` items include `canonical_values`, `field_provenance`, and `conflicts`. Machine-checked JSON schemas for these responses live in `packages/contracts/`.

Studio bridge actions also include `scrollStep` and `links(css)` for infinite scroll and detail links.

`scrape.create_job` and `job.retry` accept `credential_ref`. Only the desktop host may add `credential_secret`; the host rejects UI payloads that contain it, and the service keeps it in memory only.

## Host commands (Tauri `invoke`)

| Command | Arguments | Notes |
| --- | --- | --- |
| `service_call` | `command`, `payload` | The only path to the application service. |
| `credential_save` / `credential_delete` / `credential_list` | `name`, `secret` / `name` / — | OS credential store; values are never returned. |
| `studio_open` | `url`, `allowedHosts`, `bounds` | Creates the incognito child WebView over `bounds` (CSS px). |
| `studio_set_bounds` / `studio_navigate` / `studio_control` / `studio_close` | `bounds` / `url` / `reload\|stop\|back` / — | Navigation is re-checked against allowed hosts. |
| `studio_call` | `action` (`setMode`, `takePicks`, `pageInfo`, `count`, `extract`, `scrollStep`, `links`), `args` array | Allow-listed bridge calls with JSON-encoded arguments. |
| `update_check` / `update_install` | `repository` (`owner/name`), `channel` / — | GitHub-pinned endpoint; install verifies the signature, needs approval, and is refused during active jobs. |

Events: `studio-event` with `{type: "page_load", event: "started"|"finished", url}` or `{type: "navigation_blocked", url}`. URLs are origin and path only.

## Job events

`job_events` rows are the source of progress. `job.state_changed` has `{from_state, to_state, reason?}`. `job.stage_changed` has `{stage, ...counts}`. Stage names are `loading`, `normalizing`, `finding_candidates`, `evaluating_evidence`, `building_groups`, `creating_review_queue`, `reading_file`, `fetching`, and `page_extracted`. There are also `review.decision_recorded`, `review.decision_reversed`, and `export.created`.

## Worker contracts

- Matching: `dataforge_matching.engine.run(request)` with `{schema_version: 1, entity_type, mapping, rows: [{id, row_number, raw}], settings, constraints}`, returning `{schema_version, policy_version, normalization_version, decisions, clusters, canonical, metrics}`. Evidence explanations contain no row values.
- Scraping: `dataforge_scraping.extraction.extract_html_pages(url, preset, max_records, max_pages, should_stop, on_page)` returns a `ScrapeResult` with records, counts, warnings, `stop_reason`, and `strategy_rationale`.
