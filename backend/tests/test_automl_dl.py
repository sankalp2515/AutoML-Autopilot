"""DL-0 — sklearn-compatible PyTorch estimators (automl_dl).

Verifies the estimators behave like any sklearn model: fit/predict/proba, work
under cross-validation (clone), and survive a joblib round-trip — the property
that lets the existing inference pipeline serve a torch model with no changes.

The package lives in the sandbox image, so we add it to sys.path here. Skips if
torch isn't installed locally.
"""

import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("torch")
_SANDBOX = Path(__file__).resolve().parents[2] / "sandbox"
if _SANDBOX.exists() and str(_SANDBOX) not in sys.path:
    sys.path.insert(0, str(_SANDBOX))

automl_dl = pytest.importorskip("automl_dl")
from automl_dl import TorchMLPClassifier, TorchMLPRegressor  # noqa: E402


def _clf_data(n=200):
    rng = np.random.default_rng(0)
    X = rng.normal(size=(n, 6)).astype("float32")
    y = (X[:, 0] + X[:, 1] > 0).astype(int)          # learnable boundary
    return X, y


def _reg_data(n=200):
    rng = np.random.default_rng(1)
    X = rng.normal(size=(n, 5)).astype("float32")
    y = (X @ np.array([1.0, -2.0, 0.5, 0.0, 3.0])).astype("float32")
    return X, y


def test_classifier_fits_and_predicts():
    X, y = _clf_data()
    clf = TorchMLPClassifier(hidden_dims=(32,), max_epochs=40, patience=5).fit(X, y)
    proba = clf.predict_proba(X)
    assert proba.shape == (len(X), 2)
    assert np.allclose(proba.sum(axis=1), 1.0, atol=1e-4)
    assert (clf.predict(X) == y).mean() > 0.8           # learns the boundary


def test_regressor_fits_and_predicts():
    X, y = _reg_data()
    reg = TorchMLPRegressor(hidden_dims=(32,), max_epochs=200, patience=20).fit(X, y)
    pred = reg.predict(X)
    assert pred.shape == (len(X),)
    # clearly beats predicting the mean
    ss_res = ((y - pred) ** 2).sum()
    ss_tot = ((y - y.mean()) ** 2).sum()
    assert 1 - ss_res / ss_tot > 0.5


def test_sklearn_clone_and_params():
    from sklearn.base import clone
    clf = TorchMLPClassifier(hidden_dims=(16, 8), lr=5e-3)
    c2 = clone(clf)                                     # requires correct get_params
    assert c2.get_params()["hidden_dims"] == (16, 8)
    assert c2.get_params()["lr"] == 5e-3


def test_works_under_cross_val_score():
    from sklearn.model_selection import cross_val_score
    X, y = _clf_data(150)
    scores = cross_val_score(TorchMLPClassifier(hidden_dims=(16,), max_epochs=60, patience=8),
                             X, y, cv=3)
    assert len(scores) == 3 and scores.mean() > 0.6


def test_joblib_round_trip_matches():
    import io
    import joblib
    X, y = _clf_data()
    clf = TorchMLPClassifier(hidden_dims=(16,), max_epochs=30, patience=5).fit(X, y)
    before = clf.predict_proba(X)
    buf = io.BytesIO()
    joblib.dump(clf, buf)                              # what the inference pipeline does
    buf.seek(0)
    loaded = joblib.load(buf)
    after = loaded.predict_proba(X)
    assert np.allclose(before, after, atol=1e-5)       # serves identically post-pickle
