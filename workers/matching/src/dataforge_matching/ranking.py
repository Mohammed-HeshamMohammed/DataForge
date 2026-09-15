"""Review-queue ranking learned from human review decisions.

This model only orders the review queue. It never creates matches, and deterministic guards stay
authoritative. Features are role-based, derived from pair evidence, so they transfer across datasets
with different column names.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

from .engine import WEIGHTS

MODEL_VERSION = "logistic-1.0.0"
FEATURE_VERSION = "evidence-roles-1"
MIN_LABELS = 20


def features(evidence: list[dict], score: float) -> list[float]:
    by_field: dict[str, list[dict]] = {}
    for item in evidence:
        by_field.setdefault(item["field"], []).append(item)
    vector = [1.0, score]
    for field in WEIGHTS:
        items = by_field.get(field, [])
        vector.append(1.0 if items else 0.0)
        vector.append(max((i["similarity"] for i in items), default=0.0))
        vector.append(1.0 if any(i["strength"] == "strong" for i in items) else 0.0)
    return vector


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, value))))


def predict(weights: list[float], vector: list[float]) -> float:
    return _sigmoid(sum(w * x for w, x in zip(weights, vector)))


def _fit(samples: list[tuple[list[float], int]], epochs: int = 400, rate: float = 0.3, l2: float = 0.01) -> list[float]:
    weights = [0.0] * len(samples[0][0])
    for _ in range(epochs):
        gradient = [0.0] * len(weights)
        for vector, label in samples:
            error = predict(weights, vector) - label
            for i, x in enumerate(vector):
                gradient[i] += error * x
        weights = [w - rate * (g / len(samples) + l2 * w) for w, g in zip(weights, gradient)]
    return weights


def train(labeled: list[tuple[list[dict], float, int]]) -> dict:
    """labeled: (evidence, deterministic score, 1 for merge / 0 for keep separate). Human labels only."""
    positives = sum(label for _, _, label in labeled)
    if len(labeled) < MIN_LABELS or positives == 0 or positives == len(labeled):
        raise ValueError(f"Ranking needs at least {MIN_LABELS} reviewed pairs including both merges and keep-separate decisions (have {len(labeled)}, {positives} merges)")
    samples = [(features(evidence, score), label) for evidence, score, label in labeled]
    training_hash = hashlib.sha256(json.dumps(sorted((s[0], s[1]) for s in samples)).encode()).hexdigest()
    shuffled = samples[:]
    random.Random(training_hash).shuffle(shuffled)
    holdout_size = max(1, len(shuffled) // 4)
    holdout, fit_set = shuffled[:holdout_size], shuffled[holdout_size:]
    evaluation_weights = _fit(fit_set)
    tp = fp = fn = tn = 0
    for vector, label in holdout:
        predicted = predict(evaluation_weights, vector) >= 0.5
        tp += predicted and label == 1
        fp += predicted and label == 0
        fn += (not predicted) and label == 1
        tn += (not predicted) and label == 0
    evaluation = {
        "holdout_size": len(holdout), "accuracy": round((tp + tn) / len(holdout), 3),
        "precision": round(tp / (tp + fp), 3) if tp + fp else None, "recall": round(tp / (tp + fn), 3) if tp + fn else None,
        "labels": len(samples), "positives": positives, "note": "Measured on human review labels only; used for queue ordering, never for auto-merge.",
    }
    return {"model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION, "training_hash": training_hash, "weights": _fit(samples), "evaluation": evaluation}
