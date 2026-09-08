"""The matching engine measured against the real vendor exports in ``Data/``.

Every assertion here encodes a defect that reached production in the original
processor, so a regression fails loudly rather than quietly returning bad rows.
"""

from __future__ import annotations

import time

import pandas as pd
import pytest

from dataforge.core.dedupe import DedupeConfig, Deduplicator
from dataforge.core.normalize import detect_roles
from tests.integration.conftest import (
    LARGE_EXPORT,
    PROPERTY_EXPORT,
    SAMPLE_ROWS,
    load_export,
)

pytestmark = pytest.mark.integration


def test_property_export_columns_are_classified(property_export):
    """A vendor property export must be understood without configuration."""
    roles = detect_roles(list(property_export.columns))

    assert "Address" in roles.address
    assert "Owner Mailing Address" in roles.address
    assert {"City", "State", "Zip", "County"} <= set(roles.region)
    assert "Owner 1 First Name" in roles.name

    # The lookalikes that the original unanchored fuzzy matcher misread.
    assert "Units Count" not in roles.region
    assert "Subdivision" not in roles.identifier


def test_skiptraced_export_contact_columns_are_classified(skiptraced_export):
    """The skip-traced schema names contact columns differently; both must work."""
    roles = detect_roles(list(skiptraced_export.columns))

    assert "Phone" in roles.phone
    assert "Alt. Phone" in roles.phone
    assert "Email" in roles.email


def test_no_false_positives_within_a_single_export(property_export):
    """One export lists each property once, so nothing should be removed.

    This is the assertion that fails if address comparison ever goes back to
    treating house numbers fuzzily: neighbouring houses on one street are ~97%
    similar as strings.
    """
    result = Deduplicator().run(property_export)
    assert result.duplicates_removed == 0, (
        f"{result.duplicates_removed} rows removed from a clean export; "
        f"first pairs: {result.candidates[:3]}"
    )


def test_self_concatenation_recovers_every_duplicate(property_export):
    """An export concatenated with itself must collapse back to its own size."""
    doubled = pd.concat([property_export, property_export], ignore_index=True)
    result = Deduplicator().run(doubled)

    assert result.duplicates_removed == len(property_export)
    assert result.output_rows == len(property_export)


def test_half_overlap_removes_exactly_the_shared_rows(property_export):
    """The real use case: two exports sharing part of their rows."""
    overlap = 1000
    first = property_export.iloc[: SAMPLE_ROWS - overlap]
    second = property_export.iloc[SAMPLE_ROWS - 2 * overlap :]
    combined = pd.concat([first, second], ignore_index=True)

    result = Deduplicator().run(combined)
    assert result.duplicates_removed == overlap
    assert result.output_rows == len(property_export)


def test_near_duplicate_export_files_deduplicate():
    """Data/ holds one export saved twice, near-identical but not byte-identical.

    The two files share an identical prefix and then diverge by a single row
    each. Reading a slice inside the shared prefix gives an exact expectation:
    every row of one file is present in the other, so combining them must
    collapse back to one file's worth of rows.
    """
    stem = (
        "2024-11-06_0am_Results_for_SKIPTRACED-PropwireExport-10000Properties"
        "-Sep420243_66d8935348470_66e8d5449dc81_66eb552fc47e2_66f2f490d4565_66f362d9cc951"
    )
    # The files are identical through line 2991; stay well inside that.
    shared_rows = 2500
    original = load_export(f"{stem}.csv", shared_rows)
    copy = load_export(f"{stem} (1).csv", shared_rows)
    assert original.equals(copy), "slice is expected to fall inside the shared prefix"

    combined = pd.concat([original, copy], ignore_index=True)
    result = Deduplicator().run(combined)

    assert result.duplicates_removed == len(original)
    assert result.output_rows == len(original)


def test_a_record_with_no_shared_identifier_is_left_alone(skiptraced_export):
    """A record whose contact fields and address are blank cannot be matched.

    This is a property of blocking, not a defect: with nothing to block on there
    is no candidate pair to score. The test pins the behaviour so it is a
    deliberate limitation rather than a surprise.
    """
    blank = dict.fromkeys(skiptraced_export.columns, "")
    frame = pd.concat(
        [skiptraced_export.iloc[:200], pd.DataFrame([blank, blank])], ignore_index=True
    )
    result = Deduplicator().run(frame)

    # The two identical blank rows are not merged, and nothing else breaks.
    assert result.output_rows == len(frame)


def test_a_looser_threshold_never_removes_fewer_rows(property_export):
    """Lowering the threshold must be monotonic, or tuning advice is meaningless."""
    strict = Deduplicator(config=DedupeConfig(threshold=0.95)).run(property_export)
    loose = Deduplicator(config=DedupeConfig(threshold=0.75)).run(property_export)
    assert loose.duplicates_removed >= strict.duplicates_removed


@pytest.mark.slow
def test_large_join_stays_far_below_quadratic_cost():
    """Guard against a return to row-by-row comparison.

    Comparing every pair of these ~58k rows is ~1.7 billion comparisons — hours
    of work. The budget is deliberately loose: it is here to catch an
    algorithmic regression, not to measure the machine.
    """
    frames = [load_export(LARGE_EXPORT), load_export(PROPERTY_EXPORT)]
    combined = pd.concat(frames, ignore_index=True).fillna("")
    assert len(combined) > 50_000

    started = time.perf_counter()
    result = Deduplicator().run(combined)
    elapsed = time.perf_counter() - started

    assert elapsed < 120, f"Deduplicating {len(combined)} rows took {elapsed:.1f}s"
    assert result.input_rows == len(combined)
