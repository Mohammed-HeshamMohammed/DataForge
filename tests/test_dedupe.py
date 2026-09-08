"""The blocking deduplication engine."""

from __future__ import annotations

import pandas as pd

from dataforge.core.dedupe import DedupeConfig, Deduplicator, WeightedScorer
from dataforge.core.models import MatchCandidate
from dataforge.core.normalize import detect_roles, normalize_frame


def test_empty_frame_is_handled():
    result = Deduplicator().run(pd.DataFrame())
    assert result.input_rows == 0
    assert result.duplicates_removed == 0


def test_blocking_generates_far_fewer_pairs_than_the_full_cross_product():
    # 200 rows sharing no blocking key must not produce 19,900 comparisons.
    frame = pd.DataFrame(
        [
            {"Address": f"{i} Unique St", "Phone 1": f"52055{i:05d}", "Email 1": f"u{i}@x.com"}
            for i in range(200)
        ]
    )
    roles = detect_roles(list(frame.columns))
    pairs = Deduplicator().generate_candidates(normalize_frame(frame, roles), roles)
    assert len(pairs) < 200 * 199 / 2


def test_oversized_blocks_are_skipped():
    # Every row shares one address prefix, so the address block is degenerate.
    frame = pd.DataFrame([{"Address": "PO Box 1", "Phone 1": ""} for _ in range(300)])
    roles = detect_roles(list(frame.columns))
    config = DedupeConfig(max_block_size=50)
    pairs = Deduplicator(config=config).generate_candidates(normalize_frame(frame, roles), roles)
    assert pairs == []


def test_duplicates_are_detected_across_formatting_and_swapped_fields(leads_frame):
    result = Deduplicator(config=DedupeConfig(threshold=0.8)).run(leads_frame)

    # Rows 0, 1 and 2 are one person; rows 3 and 4 are another; row 5 is alone.
    assert result.output_rows == 3
    assert result.duplicates_removed == 3
    assert result.input_rows == 6


def test_transitive_matches_collapse_to_a_single_survivor():
    # a matches b, b matches c, but a and c share no blocking key directly.
    duplicates = Deduplicator._resolve_duplicates(
        [MatchCandidate(0, 1, 0.9), MatchCandidate(1, 2, 0.9)]
    )
    assert duplicates == {1, 2}


def test_unique_records_survive():
    frame = pd.DataFrame(
        [
            {"Address": "1 A St", "Phone 1": "5205550001", "Email 1": "a@x.com"},
            {"Address": "2 B St", "Phone 1": "5205550002", "Email 1": "b@x.com"},
        ]
    )
    result = Deduplicator().run(frame)
    assert result.duplicates_removed == 0
    assert result.output_rows == 2


def test_records_with_no_shared_evidence_do_not_score_as_a_match(leads_frame):
    roles = detect_roles(list(leads_frame.columns))
    normalized = normalize_frame(leads_frame, roles)
    blank = normalized.copy()
    for column in roles.phone + roles.email:
        blank[column] = ""

    # With every contact field blank, the score must come only from the
    # remaining roles rather than counting the blanks as agreement.
    score = WeightedScorer().score_pairs(blank, roles, [(0, 5)])[0]
    assert score < 0.8


def test_summary_reports_consistent_counts(leads_frame):
    summary = Deduplicator(config=DedupeConfig(threshold=0.8)).run(leads_frame).summary()
    assert summary["input_rows"] == summary["output_rows"] + summary["duplicates_removed"]
    assert 0.0 <= summary["duplicate_ratio"] <= 1.0
