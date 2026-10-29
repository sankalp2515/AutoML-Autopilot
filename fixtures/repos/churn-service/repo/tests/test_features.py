import numpy as np

from churn.features import FEATURE_ORDER, build_features
from tests.helpers import accounts


def test_feature_shape_and_order():
    frame = build_features(accounts().head(12))
    assert frame.shape == (12, len(FEATURE_ORDER))
    assert list(frame.columns) == FEATURE_ORDER


def test_features_are_finite_numeric():
    assert np.isfinite(build_features(accounts()).to_numpy()).all()


def test_features_are_deterministic():
    sample = accounts().head(5)
    assert build_features(sample).equals(build_features(sample.copy()))
