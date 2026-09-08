# Data handling

Read this before adding any dataset to the repository.

## `Data/` — decision on record

`Data/` holds ~53 MB of **real skip-traced records** across 18 vendor exports:
names, home addresses, phone numbers and email addresses of identifiable private
individuals in California, Texas and Arizona.

**The decision is to keep these files, as test data.** They are the exports the
matching engine was built for, and `tests/integration/` now measures the engine
against them — real vendor column naming from two different schemas, real
address formatting, and enough volume to catch a performance regression. No
synthetic fixture covers that ground.

Two consequences follow, and they are not optional:

- **The repository must stay private.** The data is in git history. Making the
  repository public publishes it, and deleting the files in a later commit does
  not undo that — history, existing clones and any fork keep the data. Treat
  "make public" as irreversible here.
- **Access is distribution.** Every collaborator, fork and CI runner with access
  to the repository holds a copy of this data. Add people deliberately.

If the repository ever needs to be published, the exports must be removed from
history first (`git filter-repo --path Data/ --invert-paths`), the integration
tests will skip automatically once `Data/` is absent, and any data already
exposed should be treated as disclosed.

## Where data belongs

```
data/
  raw/        # untouched source exports — gitignored
  interim/    # intermediate working files — gitignored
  samples/    # small, synthetic, safe to commit
artifacts/    # pipeline outputs and screenshots — gitignored
models/       # trained models — *.joblib gitignored
```

`.gitignore` already excludes `data/raw/`, `data/interim/`, `artifacts/` and
`models/*.joblib`.

## Rules

- **Never add new personal data.** The exports in `Data/` are a recorded
  exception, made knowingly and documented above. New datasets containing real
  people's names, addresses, phone numbers or emails belong in `data/raw/`,
  which is gitignored.
- **Never commit credentials.** No passwords or tokens in recipes, `.env`, or
  test fixtures. `.env` is gitignored; `.env.example` holds defaults only.
- **Unit tests stay synthetic.** Everything in `tests/` outside
  `tests/integration/` uses fabricated records (see `tests/conftest.py`), so the
  suite is meaningful in a checkout without `Data/`.
- **Integration tests assert on counts, not contents.** They check how many rows
  matched, never what a record says, so a CI log never prints personal data.
- **Mind what you scrape.** Data collected by a spider is subject to the same
  rules as data you were given.
- **Redact before sharing.** Deduplication summaries are safe to share; the
  deduplicated rows are not.

## Retention

Skip-traced contact data has a purpose and a lifetime. The `Data/` exports are
retained as a test corpus; working copies do not get the same latitude. Delete
raw exports from `data/raw/` once the processed output exists, and do not keep
contact records past the campaign they were acquired for.
