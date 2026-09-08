"""Blocking-based deduplication.

The original implementation compared every row against every other row, which
is O(n^2): on the 10,000-row exports in this project that is ~50 million Python
level comparisons per file pair. This engine instead *blocks* — it groups rows
that share a cheap key (a normalised phone, an email, or a prefix of the
address) and only scores pairs inside a block. Rows that share no block are
never compared, which is what makes 40k-row files tractable.

Scoring is pluggable: the default is a deterministic weighted average of
per-role similarities, and :mod:`dataforge.ml` supplies a learned scorer with
the same interface.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Protocol

import pandas as pd

from dataforge.core.compare import has_strong_evidence, role_similarity
from dataforge.core.models import ColumnRoles, MatchCandidate
from dataforge.core.normalize import detect_roles, normalize_frame
from dataforge.logging import get_logger

logger = get_logger(__name__)

#: Relative contribution of each role to the deterministic match score.
ROLE_WEIGHTS: dict[str, float] = {
    "phone": 0.32,
    "email": 0.28,
    "address": 0.20,
    "name": 0.15,
    "region": 0.05,
}


class PairScorer(Protocol):
    """Anything that can score candidate pairs from a normalised frame."""

    def score_pairs(
        self, frame: pd.DataFrame, roles: ColumnRoles, pairs: list[tuple[int, int]]
    ) -> list[float]:
        """Return a match probability in ``[0, 1]`` for each pair."""


@dataclass
class DedupeConfig:
    """Knobs controlling candidate generation and match acceptance."""

    threshold: float = 0.85
    blocking_key_length: int = 4
    #: Roles used to build blocking keys, in priority order.
    blocking_roles: tuple[str, ...] = ("phone", "email", "address")
    #: Guard against pathological blocks (e.g. every row with a blank phone).
    max_block_size: int = 200
    keep: str = "first"


@dataclass
class DedupeResult:
    """Outcome of a deduplication run."""

    frame: pd.DataFrame
    duplicate_indices: list[int] = field(default_factory=list)
    candidates: list[MatchCandidate] = field(default_factory=list)
    input_rows: int = 0

    @property
    def duplicates_removed(self) -> int:
        return len(self.duplicate_indices)

    @property
    def output_rows(self) -> int:
        return len(self.frame)

    def summary(self) -> dict[str, int | float]:
        """A JSON-friendly summary, used by the CLI and the web UI."""
        ratio = self.duplicates_removed / self.input_rows if self.input_rows else 0.0
        return {
            "input_rows": self.input_rows,
            "output_rows": self.output_rows,
            "duplicates_removed": self.duplicates_removed,
            "candidate_pairs": len(self.candidates),
            "duplicate_ratio": round(ratio, 4),
        }


class WeightedScorer:
    """Deterministic scorer: a weighted average of per-role similarities.

    Roles for which neither record holds a value are dropped from the average
    rather than counted as agreement, so a pair with no contact details on
    either side does not score 1.0 on an empty comparison.
    """

    def __init__(
        self, weights: dict[str, float] | None = None, require_strong: bool = True
    ) -> None:
        self.weights = weights or ROLE_WEIGHTS
        self.require_strong = require_strong

    def score_pairs(
        self, frame: pd.DataFrame, roles: ColumnRoles, pairs: list[tuple[int, int]]
    ) -> list[float]:
        return [self._score_pair(frame, roles, left, right) for left, right in pairs]

    def _score_pair(self, frame: pd.DataFrame, roles: ColumnRoles, left: int, right: int) -> float:
        if self.require_strong and not has_strong_evidence(frame, roles, left, right):
            return 0.0

        total_weight = 0.0
        accumulated = 0.0
        for role, weight in self.weights.items():
            similarity = role_similarity(frame, roles, left, right, role)
            if similarity is None:
                continue
            accumulated += similarity * weight
            total_weight += weight

        return accumulated / total_weight if total_weight else 0.0


class Deduplicator:
    """Find and remove duplicate records across one or more tables."""

    def __init__(
        self,
        config: DedupeConfig | None = None,
        scorer: PairScorer | None = None,
    ) -> None:
        self.config = config or DedupeConfig()
        self.scorer = scorer or WeightedScorer()

    def run(self, frame: pd.DataFrame) -> DedupeResult:
        """Deduplicate ``frame`` and return the surviving rows plus statistics."""
        if frame.empty:
            return DedupeResult(frame=frame, input_rows=0)

        frame = frame.reset_index(drop=True)
        roles = detect_roles(list(frame.columns))
        normalized = normalize_frame(frame, roles)

        pairs = self.generate_candidates(normalized, roles)
        logger.info("Generated %d candidate pairs from %d rows", len(pairs), len(frame))

        scores = self.scorer.score_pairs(normalized, roles, pairs)
        candidates = [
            MatchCandidate(left=left, right=right, score=score)
            for (left, right), score in zip(pairs, scores, strict=True)
            if score >= self.config.threshold
        ]

        duplicates = self._resolve_duplicates(candidates)
        survivors = frame.drop(index=duplicates).reset_index(drop=True)
        return DedupeResult(
            frame=survivors,
            duplicate_indices=sorted(duplicates),
            candidates=candidates,
            input_rows=len(frame),
        )

    def generate_candidates(
        self, normalized: pd.DataFrame, roles: ColumnRoles
    ) -> list[tuple[int, int]]:
        """Build the candidate pair set by blocking on high-signal columns."""
        blocks: dict[str, set[int]] = defaultdict(set)
        for role in self.config.blocking_roles:
            for column in getattr(roles, role, []):
                for index, value in normalized[column].items():
                    key = self._blocking_key(role, value)
                    if key:
                        blocks[f"{role}:{key}"].add(index)

        pairs: set[tuple[int, int]] = set()
        for key, members in blocks.items():
            if len(members) < 2:
                continue
            if len(members) > self.config.max_block_size:
                # A block this large means the key carries almost no signal
                # (a placeholder phone, a shared PO box). Comparing it would
                # reintroduce the quadratic blow-up for no precision gain.
                logger.debug("Skipping oversized block %s (%d rows)", key, len(members))
                continue
            ordered = sorted(members)
            for i, left in enumerate(ordered):
                for right in ordered[i + 1 :]:
                    pairs.add((left, right))
        return sorted(pairs)

    def _blocking_key(self, role: str, value: object) -> str:
        """Reduce a normalised value to the key rows must share to be compared."""
        text = str(value or "")
        if not text:
            return ""
        if role == "phone":
            # Last 7 digits: robust to an area code recorded inconsistently.
            return text[-7:] if len(text) >= 7 else ""
        if role == "email":
            return text
        if role == "address":
            return self._street_key(text)
        return text[: self.config.blocking_key_length]

    def _street_key(self, text: str) -> str:
        """Signature of a street address: house number plus the street's stem.

        A bare prefix of the address is a poor key — every "123 ..." in a county
        collides — so the number and the start of the street name are combined.
        """
        words = text.split()
        if not words:
            return ""
        if len(words) == 1:
            return words[0][: self.config.blocking_key_length]
        stem = self.config.blocking_key_length
        return f"{words[0]}{words[1][:stem]}"

    @staticmethod
    def _resolve_duplicates(candidates: Iterable[MatchCandidate]) -> set[int]:
        """Collapse matched pairs into clusters, keeping the lowest index.

        Union-find groups transitive matches (a==b, b==c) into one cluster so
        that three copies of a record collapse to one survivor, not two.
        """
        parent: dict[int, int] = {}

        def find(node: int) -> int:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        def union(a: int, b: int) -> None:
            root_a, root_b = find(a), find(b)
            if root_a != root_b:
                # Point at the smaller index so the survivor is the first seen.
                parent[max(root_a, root_b)] = min(root_a, root_b)

        for candidate in candidates:
            union(candidate.left, candidate.right)

        return {node for node in parent if find(node) != node}


def deduplicate_files(
    frames: list[pd.DataFrame],
    config: DedupeConfig | None = None,
    scorer: PairScorer | None = None,
    column_filter: Callable[[list[str]], list[str]] | None = None,
) -> DedupeResult:
    """Concatenate frames and deduplicate across all of them at once."""
    if not frames:
        raise ValueError("No frames supplied")
    combined = pd.concat(frames, ignore_index=True).fillna("")
    if column_filter is not None:
        combined = combined[column_filter(list(combined.columns))]
    return Deduplicator(config=config, scorer=scorer).run(combined)
