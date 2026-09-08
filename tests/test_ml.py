"""Pairwise features and the learned matcher."""

from __future__ import annotations

import numpy as np
import pytest

from dataforge.core.normalize import detect_roles, normalize_frame
from dataforge.ml.features import FEATURE_NAMES, PairFeatureExtractor
from dataforge.ml.model import MatchModel, ModelNotTrainedError

sklearn = pytest.importorskip("sklearn")


def test_feature_vector_has_the_declared_shape(leads_frame):
    roles = detect_roles(list(leads_frame.columns))
    normalized = normalize_frame(leads_frame, roles)
    features = PairFeatureExtractor(roles).transform(normalized, [(0, 1), (0, 5)])
    assert features.shape == (2, len(FEATURE_NAMES))


def test_a_matching_pair_scores_higher_than_an_unrelated_one(leads_frame):
    roles = detect_roles(list(leads_frame.columns))
    normalized = normalize_frame(leads_frame, roles)
    extractor = PairFeatureExtractor(roles)
    assert sum(extractor.transform_one(normalized, 0, 1)) > sum(
        extractor.transform_one(normalized, 0, 5)
    )


def test_empty_pair_list_yields_an_empty_matrix(leads_frame):
    roles = detect_roles(list(leads_frame.columns))
    assert PairFeatureExtractor(roles).transform(leads_frame, []).shape == (0, len(FEATURE_NAMES))


def test_scoring_before_training_raises():
    with pytest.raises(ModelNotTrainedError):
        MatchModel().predict_proba(np.zeros((1, len(FEATURE_NAMES))))


def test_saving_before_training_raises():
    with pytest.raises(ModelNotTrainedError):
        MatchModel().save()


def test_train_and_round_trip_a_model(tmp_path):
    rng = np.random.default_rng(0)
    positives = rng.uniform(0.7, 1.0, size=(40, len(FEATURE_NAMES)))
    negatives = rng.uniform(0.0, 0.3, size=(40, len(FEATURE_NAMES)))
    features = np.vstack([positives, negatives])
    labels = np.array([1] * 40 + [0] * 40)

    model = MatchModel()
    report = model.fit(features, labels)
    assert report.n_samples == 80
    assert report.metrics["f1"] > 0.8

    path = model.save(tmp_path / "m.joblib")

    from dataforge.ml.model import load_model

    reloaded = load_model(path)
    assert reloaded.is_trained
    np.testing.assert_allclose(
        reloaded.predict_proba(features[:5]), model.predict_proba(features[:5])
    )


def test_training_on_a_single_class_is_refused():
    features = np.random.default_rng(0).uniform(0, 1, size=(20, len(FEATURE_NAMES)))
    labels = np.zeros(20, dtype=int)
    with pytest.raises(ValueError, match="only non-matches"):
        MatchModel().fit(features, labels)
