"""Record-level diffs between two collections of the same watch, keyed by the preset's unique_by fields."""

from __future__ import annotations

_PROVENANCE = {"source_retrieved_at", "source_url", "preset_version", "strategy_used", "extraction_mode", "structured_syntax"}


def record_key(record: dict, unique_by: list[str]) -> tuple | None:
    key = tuple(record.get(field) for field in unique_by)
    return key if unique_by and all(value not in (None, "") for value in key) else None


def diff_records(previous: list[dict], current: list[dict], unique_by: list[str], ignore: set[str] | None = None, sample: int = 50) -> dict:
    ignore = _PROVENANCE | (ignore or set())
    if not unique_by:
        raise ValueError("A diff needs validation.unique_by on the preset")
    before = {k: r for r in previous if (k := record_key(r, unique_by)) is not None}
    after = {k: r for r in current if (k := record_key(r, unique_by)) is not None}
    added = [after[k] for k in after.keys() - before.keys()]
    removed = [before[k] for k in before.keys() - after.keys()]
    changed = []
    for key in before.keys() & after.keys():
        fields = sorted(f for f in set(before[key]) | set(after[key]) if f not in ignore and before[key].get(f) != after[key].get(f))
        if fields:
            changed.append({"key": dict(zip(unique_by, key)), "changes": {f: {"before": before[key].get(f), "after": after[key].get(f)} for f in fields}})
    unkeyed = sum(1 for r in previous + current if record_key(r, unique_by) is None)
    return {
        "counts": {"added": len(added), "removed": len(removed), "changed": len(changed), "unchanged": len(before.keys() & after.keys()) - len(changed), "unkeyed": unkeyed},
        "added": [{f: r.get(f) for f in unique_by} for r in added[:sample]],
        "removed": [{f: r.get(f) for f in unique_by} for r in removed[:sample]],
        "changed": sorted(changed, key=lambda c: str(c["key"]))[:sample],
    }
