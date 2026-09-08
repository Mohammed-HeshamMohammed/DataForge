"""Turn a pair of records into the numeric feature vector the model consumes.

Features are deliberately role-based rather than column-based so that a model
trained on one vendor's export still applies to another's.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataforge.core.compare import role_similarity
from dataforge.core.models import ColumnRoles

#: The roles compared, in the order their features appear in each vector.
FEATURE_ROLES = ("address", "region", "name", "phone", "email")

#: Per-role features: an exact-match flag, the similarity, and a presence flag.
FEATURE_SUFFIXES = ("exact", "fuzzy", "present")

FEATURE_NAMES: list[str] = [
    f"{role}_{suffix}" for role in FEATURE_ROLES for suffix in FEATURE_SUFFIXES
]


class PairFeatureExtractor:
    """Build feature matrices for candidate pairs drawn from a normalised frame."""

    def __init__(self, roles: ColumnRoles) -> None:
        self.roles = roles

    @property
    def feature_names(self) -> list[str]:
        return list(FEATURE_NAMES)

    def transform(self, frame: pd.DataFrame, pairs: list[tuple[int, int]]) -> np.ndarray:
        """Return an ``(n_pairs, n_features)`` matrix of float features."""
        if not pairs:
            return np.zeros((0, len(FEATURE_NAMES)), dtype=float)
        return np.array(
            [self.transform_one(frame, left, right) for left, right in pairs],
            dtype=float,
        )

    def transform_one(self, frame: pd.DataFrame, left: int, right: int) -> list[float]:
        """Return the feature vector for a single pair of row indices."""
        vector: list[float] = []
        for role in FEATURE_ROLES:
            similarity = role_similarity(frame, self.roles, left, right, role)
            if similarity is None:
                # No evidence either way: neutral similarity, presence flag off.
                vector.extend([0.0, 0.0, 0.0])
                continue
            # The exact flag lets the model separate "identical" from "similar",
            # which for phones and emails is the whole signal.
            vector.extend([1.0 if similarity >= 1.0 else 0.0, similarity, 1.0])
        return vector
