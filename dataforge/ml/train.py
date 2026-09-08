"""Build a labelled training set from candidate pairs and fit a match model.

Labels come from one of two places:

* a CSV with ``left,right,label`` columns produced by manual review, or
* :func:`weak_labels`, which bootstraps labels from the deterministic scorer so
  that a first model can be trained before any hand-labelling exists.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from dataforge.core.dedupe import DedupeConfig, Deduplicator, WeightedScorer
from dataforge.core.normalize import detect_roles, normalize_frame
from dataforge.logging import get_logger
from dataforge.ml.features import PairFeatureExtractor
from dataforge.ml.model import MatchModel, TrainingReport

logger = get_logger(__name__)


def build_training_set(
    frame: pd.DataFrame,
    labels: pd.DataFrame | None = None,
    config: DedupeConfig | None = None,
) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
    """Return ``(features, labels, pairs)`` for the candidate pairs in ``frame``.

    When ``labels`` is ``None`` the pairs are weakly labelled by the
    deterministic scorer, which gives a usable cold-start training set.
    """
    config = config or DedupeConfig()
    frame = frame.reset_index(drop=True)
    roles = detect_roles(list(frame.columns))
    normalized = normalize_frame(frame, roles)

    pairs = Deduplicator(config=config).generate_candidates(normalized, roles)
    if not pairs:
        raise ValueError("No candidate pairs generated; nothing to train on")

    features = PairFeatureExtractor(roles).transform(normalized, pairs)
    if labels is None:
        y = weak_labels(normalized, roles, pairs, config.threshold)
    else:
        y = _labels_from_frame(labels, pairs)
    return features, y, pairs


def weak_labels(
    normalized: pd.DataFrame,
    roles,
    pairs: list[tuple[int, int]],
    threshold: float,
) -> np.ndarray:
    """Label pairs using the deterministic scorer as a noisy teacher."""
    scores = WeightedScorer().score_pairs(normalized, roles, pairs)
    return np.array([1 if s >= threshold else 0 for s in scores], dtype=int)


def _labels_from_frame(labels: pd.DataFrame, pairs: list[tuple[int, int]]) -> np.ndarray:
    """Align a ``left,right,label`` table onto the generated pair order."""
    required = {"left", "right", "label"}
    missing = required - set(labels.columns)
    if missing:
        raise ValueError(f"Label file is missing columns: {sorted(missing)}")

    lookup = {(int(row.left), int(row.right)): int(row.label) for row in labels.itertuples()}
    return np.array([lookup.get(pair, 0) for pair in pairs], dtype=int)


def train_from_frame(
    frame: pd.DataFrame,
    labels: pd.DataFrame | None = None,
    config: DedupeConfig | None = None,
    output_path: str | Path | None = None,
) -> tuple[MatchModel, TrainingReport, Path]:
    """Train a model on ``frame`` and persist it; returns model, report and path."""
    features, y, pairs = build_training_set(frame, labels=labels, config=config)
    logger.info("Training on %d pairs (%d positive)", len(pairs), int(y.sum()))

    model = MatchModel()
    report = model.fit(features, y)
    path = model.save(output_path)
    return model, report, path
