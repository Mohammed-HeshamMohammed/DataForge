"""The learned record-matching model.

:class:`MatchModel` satisfies the :class:`~dataforge.core.dedupe.PairScorer`
protocol, so a trained model can be dropped into :class:`Deduplicator` in place
of the deterministic ``WeightedScorer`` with no other change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from dataforge.config import get_settings
from dataforge.core.models import ColumnRoles
from dataforge.logging import get_logger
from dataforge.ml.features import FEATURE_NAMES, PairFeatureExtractor

logger = get_logger(__name__)


class ModelNotTrainedError(RuntimeError):
    """Raised when a model is asked to score before it has been fitted."""


@dataclass
class TrainingReport:
    """Metrics captured when a model is fitted."""

    n_samples: int
    n_positive: int
    metrics: dict[str, float] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {
            "n_samples": self.n_samples,
            "n_positive": self.n_positive,
            **{k: round(v, 4) for k, v in self.metrics.items()},
        }


class MatchModel:
    """A gradient-boosted classifier over pairwise record features.

    The estimator is created lazily so that importing this module — which the
    web app does at startup — does not pull in scikit-learn unless a model is
    actually trained or loaded.
    """

    def __init__(self, estimator: Any | None = None, threshold: float | None = None) -> None:
        self.estimator = estimator
        self.threshold = threshold if threshold is not None else get_settings().match_threshold
        self.feature_names = list(FEATURE_NAMES)

    @property
    def is_trained(self) -> bool:
        return self.estimator is not None

    @staticmethod
    def _build_estimator() -> Any:
        from sklearn.ensemble import HistGradientBoostingClassifier

        settings = get_settings()
        return HistGradientBoostingClassifier(
            max_iter=200,
            learning_rate=0.1,
            random_state=settings.random_seed,
        )

    def fit(self, features: np.ndarray, labels: np.ndarray) -> TrainingReport:
        """Fit the estimator and report cross-validated quality."""
        from sklearn.metrics import average_precision_score, f1_score
        from sklearn.model_selection import train_test_split

        if features.shape[0] < 4:
            raise ValueError("Need at least 4 labelled pairs to train")

        classes = np.unique(labels)
        if len(classes) < 2:
            # A single-class fit produces a model that returns one constant and
            # silently scores every pair identically, which is worse than no
            # model at all because it looks trained.
            only = "matches" if classes[0] == 1 else "non-matches"
            raise ValueError(
                f"Training labels contain only {only}. The model needs examples of "
                "both. Lower the threshold used for weak labelling, or supply a "
                "label file with both classes."
            )

        settings = get_settings()
        x_train, x_test, y_train, y_test = train_test_split(
            features, labels, test_size=0.25, random_state=settings.random_seed, stratify=labels
        )

        self.estimator = self._build_estimator()
        self.estimator.fit(x_train, y_train)

        probabilities = self.estimator.predict_proba(x_test)[:, 1]
        predictions = (probabilities >= self.threshold).astype(int)
        metrics = {
            "f1": float(f1_score(y_test, predictions, zero_division=0)),
            "average_precision": float(average_precision_score(y_test, probabilities)),
        }
        logger.info("Trained match model: %s", metrics)
        return TrainingReport(
            n_samples=int(features.shape[0]),
            n_positive=int(labels.sum()),
            metrics=metrics,
        )

    def predict_proba(self, features: np.ndarray) -> np.ndarray:
        """Return the probability that each feature row is a true match."""
        if not self.is_trained:
            raise ModelNotTrainedError("Model has not been trained or loaded")
        if features.shape[0] == 0:
            return np.zeros(0, dtype=float)
        return self.estimator.predict_proba(features)[:, 1]

    def score_pairs(
        self, frame: pd.DataFrame, roles: ColumnRoles, pairs: list[tuple[int, int]]
    ) -> list[float]:
        """PairScorer implementation, so this model can drive ``Deduplicator``."""
        features = PairFeatureExtractor(roles).transform(frame, pairs)
        return [float(p) for p in self.predict_proba(features)]

    def save(self, path: str | Path | None = None) -> Path:
        """Persist the fitted estimator and its threshold to disk."""
        import joblib

        if not self.is_trained:
            raise ModelNotTrainedError("Refusing to save an untrained model")

        settings = get_settings()
        settings.ensure_directories()
        target = Path(path) if path else settings.model_dir / f"{settings.model_name}.joblib"
        target.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "estimator": self.estimator,
                "threshold": self.threshold,
                "feature_names": self.feature_names,
            },
            target,
        )
        logger.info("Saved model to %s", target)
        return target


def load_model(path: str | Path | None = None) -> MatchModel:
    """Load a persisted model, defaulting to the configured model directory."""
    import joblib

    settings = get_settings()
    target = Path(path) if path else settings.model_dir / f"{settings.model_name}.joblib"
    if not target.exists():
        raise FileNotFoundError(f"No trained model at {target}; run 'dataforge ml train' first")

    payload = joblib.load(target)
    model = MatchModel(estimator=payload["estimator"], threshold=payload["threshold"])
    model.feature_names = payload.get("feature_names", list(FEATURE_NAMES))
    return model
