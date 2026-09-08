# Data handling

Read this before adding any dataset to the repository.

## The current state of `Data/`

`Data/` contains ~50 MB of **real skip-traced records**, committed to git
history: names, home addresses, phone numbers and email addresses of identifiable
private individuals across California, Texas and Arizona.

This is worth a deliberate decision, because:

- If the repository is or ever becomes public, that data is published. Deleting
  the files in a later commit does **not** remove them — they remain in history
  and in every existing clone and fork.
- Skip-traced contact data is personal data under the CCPA/CPRA (California) and
  comparable regimes. Publishing or redistributing it can carry obligations the
  repository cannot satisfy.
- It makes every clone of the repository a copy of that data, on machines and in
  CI runners that were never meant to hold it.

The files are left in place because removing them rewrites history and is the
repository owner's call, not something to do silently. **Decide explicitly.**

### Options

1. **Keep it, and keep the repository private.** Confirm that the visibility
   setting is private and that fork and collaborator access are limited.
2. **Remove it from history.** Use `git filter-repo` (or the BFG) to strip
   `Data/` from every commit, force-push, and treat the previously exposed data
   as disclosed — anyone who cloned still has it.
   ```bash
   git filter-repo --path Data/ --invert-paths
   ```
3. **Replace it with a small synthetic sample.** Keep a few hundred fabricated
   rows in `data/samples/` for development and tests; keep the real exports
   outside the repository entirely.

Option 3 is what the layout below assumes.

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

- **Never commit real personal data.** If a dataset contains a real person's
  name, address, phone number or email, it goes outside the repository.
- **Never commit credentials.** No passwords or tokens in recipes, `.env`, or
  test fixtures. `.env` is gitignored; `.env.example` holds defaults only.
- **Keep test fixtures synthetic.** Everything in `tests/` uses fabricated
  records (see `tests/conftest.py`) — no production rows.
- **Mind what you scrape.** Data collected by a spider is subject to the same
  rules as data you were given.
- **Redact before sharing.** Deduplication summaries are safe to share; the
  deduplicated rows are not.

## Retention

Skip-traced contact data has a purpose and a lifetime. Delete raw exports from
`data/raw/` once the processed output exists, and do not keep contact records
past the campaign they were acquired for.
