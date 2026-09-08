# Data entry with recipes

A recipe describes a form-filling flow in YAML, so adding a target system means
writing data rather than code. One recipe serves every row of a table.

```yaml
name: crm-lead-entry
url: https://crm.example.com/leads/new

# A record missing any of these is skipped rather than half-submitted.
required_fields:
  - "Owner 1 First Name"
  - "Owner 1 Last Name"

steps:
  - action: type
    selector: "#first_name"
    value: "{Owner 1 First Name}"
  - action: type
    selector: "#phone"
    value: "{Phone 1}"
    optional: true            # tolerate a missing element
  - action: select
    selector: "#state"
    value: "{State}"
  - action: click
    selector: "button[type=submit]"
  - action: wait_for
    selector: ".flash-success"
```

Braces are filled from the record being entered. A column that does not exist
renders as an empty string rather than raising, so one recipe survives tables
with optional columns.

## Actions

| Action | Needs a selector | What it does |
| --- | --- | --- |
| `navigate` | no | Go to `value` |
| `type` | yes | Clear the field, then type the rendered value |
| `click` | yes | Click the element |
| `select` | yes | Choose a dropdown option by visible text |
| `wait_for` | yes | Block until the element is present |
| `screenshot` | no | Save a PNG to `value`, or the artifact directory |

Locate elements with `by: css` (default), `xpath`, `id`, or `name`. Mark a step
`optional: true` to continue when its element is absent.

## Running

```bash
# Dry run — validates every record, opens no browser. This is the default.
dataforge entry run recipes/example-crm.yml clean.csv

# Actually drive a browser
dataforge entry run recipes/example-crm.yml clean.csv --live
```

**Dry run is the default deliberately.** A mistyped selector or a missing column
should surface before anything reaches a live system, not after 500 malformed
records have been submitted. Always dry-run first.

A failure on one record is recorded and the batch continues, so a single bad row
cannot abandon the remaining work. Results report `submitted`, `skipped` and
`failed` counts, with per-record errors.

## Browser configuration

```bash
DATAFORGE_SELENIUM_BROWSER=chrome        # or firefox
DATAFORGE_SELENIUM_HEADLESS=true
DATAFORGE_SELENIUM_TIMEOUT_SECONDS=30
DATAFORGE_SELENIUM_REMOTE_URL=http://selenium:4444/wd/hub   # Grid or container
```

`docker compose up` starts a Selenium container already wired to the web
service.

## Credentials

Recipes are committed to the repository, so never put a password or token in
one. Authenticate the browser session outside the recipe — a pre-authenticated
profile, a session cookie injected before the run, or a secret read from the
environment.
