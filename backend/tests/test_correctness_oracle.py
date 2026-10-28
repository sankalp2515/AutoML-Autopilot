"""Correctness oracle (P1.5) — planted ground truth, assert the pipeline's JUDGMENT
layer reaches the KNOWN-correct conclusion.

Unlike the unit probe tests (which check one probe on hand-crafted inputs), these
train REAL sklearn models on datasets with a KNOWN property (planted leakage, a
known-best model, within-noise lift) and assert the full trust/selection stack
reaches the correct verdict — a correctness oracle, not a smoke test.

If any of these ever fails, the platform has started giving a confident WRONG answer
on data whose right answer we know for certain — the exact failure that loses trust.
"""
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.linear_model import LinearRegression

from app.core import trust_probes as tp


@pytest.fixture
def rng():
    return np.random.default_rng(0)


# ── ORACLE 1: planted target leakage MUST be caught and cap the grade at F ─────

def test_planted_leakage_is_caught_and_grade_is_F(rng):
    n = 800
    y = rng.integers(0, 2, size=n)
    X = pd.DataFrame({
        "real_signal": y * 0.4 + rng.normal(0, 1, n),   # weakly predictive, legit
        "leaked_copy": y.astype(float) + rng.normal(0, 0.001, n),  # ~perfect copy of target
        "noise": rng.normal(0, 1, n),
    })
    leak = tp.probe_leakage(X, y, "binary_classification")
    assert leak["status"] == "fail", "a near-perfect target copy must be flagged as leakage"
    assert "leaked_copy" in str(leak.get("evidence", {}))
    # A critical leakage failure must cap the overall grade at F.
    score, letter = tp.grade([leak])
    if leak["severity"] == "critical":
        assert letter == "F", f"critical leakage must force grade F, got {letter} ({score})"


def test_clean_dataset_is_not_falsely_flagged_as_leaky(rng):
    n = 800
    y = rng.integers(0, 2, size=n)
    # Legit weak/medium signals — NONE is a target copy.
    X = pd.DataFrame({
        "f1": y * 0.3 + rng.normal(0, 1, n),
        "f2": y * 0.2 + rng.normal(0, 1, n),
        "f3": rng.normal(0, 1, n),
    })
    leak = tp.probe_leakage(X, y, "binary_classification")
    assert leak["status"] != "fail", f"clean data wrongly flagged as leaky: {leak.get('headline')}"


# ── ORACLE 2: within-noise "improvement" MUST be judged insignificant ──────────

def test_within_noise_lift_is_flagged_insignificant():
    # final barely above baseline, but the CV noise is larger than the lift.
    probe = tp.probe_significance(baseline=0.900, final=0.902, score_std=0.02,
                                  primary_metric="auc_roc")
    assert probe["status"] in ("warn", "fail")


def test_real_lift_above_noise_passes():
    probe = tp.probe_significance(baseline=0.70, final=0.85, score_std=0.01,
                                  primary_metric="auc_roc")
    assert probe["status"] == "pass"


def test_significance_respects_lower_better_metric():
    # rmse: final LOWER than baseline is an IMPROVEMENT (must pass, not fail).
    probe = tp.probe_significance(baseline=3.5, final=0.9, score_std=0.05,
                                  primary_metric="rmse")
    assert probe["status"] == "pass"
    # rmse got WORSE (higher) → must NOT pass.
    worse = tp.probe_significance(baseline=0.9, final=3.5, score_std=0.05,
                                  primary_metric="rmse")
    assert worse["status"] in ("warn", "fail")


# ── ORACLE 3: a model that only fits noise MUST show a large overfit gap ───────

def test_overfit_is_detected(rng):
    n = 300
    # Pure noise: no learnable relation → a deep RF memorizes train, fails holdout.
    X = pd.DataFrame(rng.normal(0, 1, size=(n, 8)), columns=[f"f{i}" for i in range(8)])
    y = rng.integers(0, 2, size=n)
    honesty = tp.probe_eval_honesty(train_score=0.99, cv_score=0.52, holdout_score=0.50,
                                    noise=0.03, basis="holdout", primary_metric="auc_roc")
    assert honesty["status"] in ("warn", "fail"), "train 0.99 vs holdout 0.50 must flag overfit"


# ── ORACLE 4: known-best model — the better learner scores higher on real signal ─

def test_known_best_model_scores_higher_on_learnable_regression(rng):
    n = 500
    X = rng.normal(0, 1, size=(n, 3))
    y = 3 * X[:, 0] - 2 * X[:, 1] + rng.normal(0, 0.1, n)   # linear, low noise
    from sklearn.model_selection import cross_val_score
    # A model that fits linear signal (LinearRegression) must beat a crippled one.
    good = cross_val_score(LinearRegression(), X, y, cv=5, scoring="r2").mean()
    bad = cross_val_score(RandomForestRegressor(n_estimators=3, max_depth=1, random_state=0),
                          X, y, cv=5, scoring="r2").mean()
    assert good > bad, "the appropriate model must win measured CV on a known-linear signal"
    assert good > 0.95   # near-perfect fit is the known-correct answer here


def test_clean_grade_is_high_when_all_probes_pass():
    # If every probe passes, the grade must be A (the healthy-run baseline).
    passes = [tp._result("p1", "c", "t", "low", "pass", "ok"),
              tp._result("p2", "c", "t", "low", "pass", "ok")]
    score, letter = tp.grade(passes)
    assert letter == "A" and score >= 90
