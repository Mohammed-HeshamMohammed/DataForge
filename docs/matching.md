# Matching and deduplication

## The pipeline

```
load → detect roles → normalise → block → score → cluster → drop
```

### 1. Role detection

Columns are classified into `identifier`, `address`, `region`, `name`, `phone`,
`email`, or `other` by matching keywords against **whole words** of the column
name. Whole-word matching is what stops `Subdivision` reading as an `id` column
and `Units Count` reading as `County`. A column matching no keyword falls back
to a strict fuzzy comparison (default 92).

`address` is street-level only; `region` holds city, state, ZIP and county. The
split matters: a city makes a useless blocking key, but a street address is
selective.

Longer keywords win ties, which sends `Owner Mailing Address` to `address`
rather than `name`.

### 2. Normalisation

| Role | Treatment |
| --- | --- |
| phone | digits only, US country prefix dropped |
| email | lower-cased and trimmed; malformed values discarded |
| address | punctuation stripped, street suffixes and directionals canonicalised (`Street`→`st`, `West`→`w`) |
| everything else | lower-cased, punctuation stripped, whitespace collapsed |

The original processor stripped punctuation from every column, which destroyed
emails (`@` removed) and phone formatting before comparison.

### 3. Blocking

Rows are grouped by a cheap key and only compared inside a group:

| Role | Key |
| --- | --- |
| phone | last 7 digits (robust to an inconsistent area code) |
| email | the whole address |
| address | house number + first 4 characters of the street name |

A bare address prefix would be a poor key — every `1234 …` in a county collides.
Blocks larger than `max_block_size` (default 200) are **skipped**: a key shared
by hundreds of rows carries no signal, and comparing it reintroduces the
quadratic cost for no precision gain.

### 4. Scoring

Roles are compared in one of two ways, and conflating them is the classic source
of false positives:

- **Interchangeable** (phone, email): compared as sets, so a record with its
  primary and alternate phone swapped still matches.
- **Positional** (address, region, name): compared column to column. A property
  address and an owner's mailing address are both `address` columns; bagging
  them together makes two unrelated properties look identical whenever they
  share a managing agent.

A role blank on both sides returns *no evidence* and is dropped from the average
rather than counted as agreement.

Two guards carry most of the precision:

- **House numbers are exact.** `144 E 68th St` vs `140 E 68th St` scores 0.97 as
  a fuzzy string and is two different houses, so where both values open with a
  house number the numbers must agree before the street is compared at all.
- **Strong evidence is required.** A pair must share an exact phone, an exact
  email, or a ≥0.9 address similarity. Without this, agreement on owner name and
  city alone matches every property in a county held by one municipal owner.

Default weights: phone 0.32, email 0.28, address 0.20, name 0.15, region 0.05.

### 5. Clustering

Matched pairs are unioned with union-find, so a chain (a≈b, b≈c) collapses to a
single survivor — the lowest original index — rather than leaving two rows.

## Using the ML scorer

```bash
dataforge ml train "Data/ca 1.csv" --output models/matcher.joblib
dataforge dedupe "Data/ca 1.csv" -o clean.csv --use-model
```

Without a label file, pairs are weakly labelled by the rule scorer so a first
model can be trained before any hand-labelling exists. To supply real labels,
pass a CSV with `left,right,label` columns.

Features are role-based (exact flag, similarity, presence — per role), never
column-based, so a model trained on one vendor's export still applies to
another's.

## Tuning

| Symptom | Try |
| --- | --- |
| Real duplicates missed | Lower `--threshold`; check both files use the same phone/email column naming |
| Distinct records merged | Raise `--threshold`; confirm addresses carry house numbers |
| Run is slow | Lower `max_block_size`; make sure phone/email columns are being detected |
| Nothing matches at all | Run `detect_roles` on your columns — the roles may all be landing in `other` |

## Measured behaviour

Each row below is asserted in `tests/integration/test_real_exports.py`,
running against the real exports in `Data/`:

| Case | Result |
| --- | --- |
| 58,533 rows, two exports | 2.5 s |
| One export deduplicated against itself | every row matched, exactly |
| Half-overlapping split (1,000 shared rows) | exactly 1,000 removed |
| Within a single clean export | 0 false positives |
| Two near-identical exports combined | collapses to one file's worth of rows |
| A record with no phone, email or address | left alone — nothing to block on |
