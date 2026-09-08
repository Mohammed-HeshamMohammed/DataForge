# DataForge

A versatile data platform for real-estate lead lists and any other record set:
**scrape** records from the web, **match and deduplicate** them with a rule
engine or a trained model, and **enter** the survivors into another system with
a browser robot — from a web UI, a CLI, a desktop app, or a scheduled pipeline.

DataForge began as a Tkinter duplicate-remover for skip-traced CSV exports. The
matching engine at its centre still does that job, and does it roughly 20x
faster with far fewer false positives; everything else is built around it.

---

## What is in the box

| Layer | Module | What it does |
| --- | --- | --- |
| Core | `dataforge.core` | File I/O, normalisation, blocking-based deduplication |
| ML | `dataforge.ml` | Pairwise features and a learned record matcher |
| Scraping | `dataforge.scraping` | Polite spider framework with a registry |
| Automation | `dataforge.automation` | Selenium data entry from declarative YAML recipes |
| Pipelines | `dataforge.pipelines` | Compose the layers, run them on a cron schedule |
| Web | `dataforge.web` | FastAPI service with an HTMX UI and a JSON API |
| Desktop | `dataforge.desktop` | The original Tkinter client, on the new engine |

All six front doors share one matching engine, so a result never depends on
which interface produced it.

## Install

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[all]"          # or pick extras: ml, web, scraping, automation, scheduler, desktop, excel, pdf
cp .env.example .env             # optional; every setting has a default
```

Python 3.11+.

## Use it

```bash
# Deduplicate across several exports at once
dataforge dedupe "Data/ca 1.csv" "Data/ca 2.csv" -o clean.xlsx --threshold 0.85

# Web UI and JSON API on http://127.0.0.1:8000
dataforge serve

# The desktop client
dataforge desktop

# Scrape, then run a whole pipeline
dataforge scrape run tabular https://example.com/listings -o scraped.csv
dataforge pipeline run dedupe-files --params params.json

# Train the matcher (weakly labelled from the rule engine if no labels given)
dataforge ml train "Data/ca 1.csv" --output models/matcher.joblib
dataforge dedupe "Data/ca 1.csv" -o clean.csv --use-model

# Data entry: validates every record without opening a browser unless --live
dataforge entry run recipes/example-crm.yml clean.csv
dataforge entry run recipes/example-crm.yml clean.csv --live
```

Or with Docker, which also brings up a Selenium container for automation:

```bash
docker compose up --build
```

## How matching works

Naive deduplication compares every row with every other row. On the 10,000-row
exports this project was built for that is ~50 million comparisons per file, and
on 58,000 rows it is 1.7 billion. DataForge instead:

1. **Classifies columns into roles** — identifier, address, region, name, phone,
   email — by whole-word keyword match, so a file from any vendor is understood
   without configuration.
2. **Normalises per role.** Phones reduce to digits, emails keep their `@`,
   street suffixes are canonicalised so `123 Main St` and `123 Main Street` are
   the same address.
3. **Blocks** rows into groups sharing a cheap key (a phone's last 7 digits, an
   email, a house number plus street stem). Rows in no common block are never
   compared. Degenerate blocks are skipped rather than allowed to reintroduce
   the quadratic cost.
4. **Scores** each surviving pair, then **clusters** matches with union-find so
   three copies of a record collapse to one survivor rather than two.

Two rules do most of the work in keeping precision high:

- **House numbers are exact, not fuzzy.** `144 E 68th St` and `140 E 68th St`
  are 97% similar as strings and are two different houses.
- **A match needs strong evidence** — an exact phone, an exact email, or a
  near-identical address. Agreement on name and city alone is not enough, or
  every property in a county with the same municipal owner matches.

Measured on this repository's own data: 58,533 rows across two exports in 2.5
seconds; exact recall (every duplicate found) and no false positives on a
half-overlapping split. These are assertions in `tests/integration/`, not
one-off measurements.

## Configuration

Every setting is an environment variable prefixed `DATAFORGE_`, or a line in
`.env`. See `.env.example` and `dataforge/config.py`.

## Documentation

- [`docs/architecture.md`](docs/architecture.md) — how the layers fit together
- [`docs/matching.md`](docs/matching.md) — the matching engine and tuning
- [`docs/scraping.md`](docs/scraping.md) — writing a spider
- [`docs/automation.md`](docs/automation.md) — writing a data-entry recipe
- [`docs/data-handling.md`](docs/data-handling.md) — **read before committing data**

## Development

```bash
pip install -e ".[all,dev]"
pytest                          # 70 tests
pytest -m "not integration"     # unit tests only; no Data/ needed
ruff check . && ruff format --check .
```

`tests/integration/` runs the engine against the real exports in `Data/` —
vendor column naming, real address formatting, and a guard against a return to
quadratic matching. It skips automatically when `Data/` is absent.

## License

MIT.
