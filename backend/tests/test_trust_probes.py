"""Trust-probe unit tests — synthetic data engineered to trip each probe.

Skips where sklearn isn't installed (matches the repo's importorskip pattern),
so it runs in dev/CI-with-ML and is a no-op in the minimal backend image.
"""
import numpy as np
import pandas as pd
import pytest

pytest.importorskip("sklearn")
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier

from app.core import trust_probes as tp


@pytest.fixture
def clean_data():
    rng = np.random.RandomState(0)
    n = 400
    X = pd.DataFrame({
        "f1": rng.normal(size=n), "f2": rng.normal(size=n), "f3": rng.normal(size=n),
    })
    y = (X["f1"] + 0.5 * X["f2"] + rng.normal(0, 0.5, n) > 0).astype(int).to_numpy()
    return X, y


def test_leakage_fires_on_leaky_feature(clean_data):
    X, y = clean_data
    X = X.copy()
    X["leaky"] = y.astype(float) + np.random.RandomState(1).normal(0, 0.001, len(y))  # ~= target
    r = tp.probe_leakage(X, y, "binary_classification")
    assert r["status"] == "fail" and r["severity"] == "critical"
    assert any(s["feature"] == "leaky" for s in r["evidence"]["suspicious_features"])


def test_leakage_passes_when_clean(clean_data):
    X, y = clean_data
    assert tp.probe_leakage(X, y, "binary_classification")["status"] == "pass"


def test_data_integrity_flags_target_nans(clean_data):
    X, y = clean_data
    y = y.astype(float).copy()
    y[:5] = np.nan
    r = tp.probe_data_integrity(X, y, "binary_classification")
    assert r["status"] == "fail" and r["evidence"]["target_nans"] == 5


def test_data_integrity_flags_constant_feature(clean_data):
    X, y = clean_data
    X = X.copy()
    X["const"] = 1.0
    r = tp.probe_data_integrity(X, y, "binary_classification")
    assert r["status"] == "warn" and r["evidence"]["constant_features"] == 1


def test_significance_fails_within_noise():
    r = tp.probe_significance(baseline=0.80, final=0.805, score_std=0.02, primary_metric="f1")
    assert r["status"] == "fail"  # lift 0.005 < noise 0.02


def test_significance_passes_real_lift():
    r = tp.probe_significance(baseline=0.70, final=0.85, score_std=0.01, primary_metric="f1")
    assert r["status"] == "pass"


def test_significance_lower_better_metric():
    # rmse: lower is better, so final < baseline is an improvement
    r = tp.probe_significance(baseline=10.0, final=5.0, score_std=0.5, primary_metric="rmse")
    assert r["status"] == "pass" and r["evidence"]["lift"] == 5.0


def test_eval_honesty_flags_overfit():
    r = tp.probe_eval_honesty(train_score=0.99, cv_score=0.70, holdout_score=0.71,
                              noise=0.01, basis="holdout", primary_metric="f1")
    assert r["status"] == "fail"  # 0.29 train-cv gap


def test_eval_honesty_warns_in_sample_basis():
    r = tp.probe_eval_honesty(train_score=0.85, cv_score=0.83, holdout_score=0.84,
                              noise=0.02, basis="in_sample_split", primary_metric="f1")
    assert r["status"] == "warn"


def test_feature_stability_flags_single_feature_reliance():
    # y depends ONLY on `dominant`; the other columns are pure noise, so a tree
    # model should concentrate nearly all importance on the one real feature.
    rng = np.random.RandomState(2)
    n = 400
    dominant = rng.normal(size=n)
    X = pd.DataFrame({
        "dominant": dominant,
        "noise1": rng.normal(size=n), "noise2": rng.normal(size=n), "noise3": rng.normal(size=n),
    })
    y = (dominant > 0).astype(int)
    model = RandomForestClassifier(n_estimators=40, random_state=0).fit(X, y)
    r = tp.probe_feature_stability(model, X, y, "binary_classification")
    assert r["status"] == "warn"  # one feature dominates importance
    assert r["evidence"]["top_feature"] == "dominant"


def test_worst_slice_finds_bad_cohort(clean_data):
    X, y = clean_data
    X = X.copy()
    # a one-hot-style slice where the label is pure noise → model fails there
    grp = np.zeros(len(y), dtype=int); grp[:120] = 1
    X["grp"] = grp.astype(float)
    yy = y.copy()
    yy[:120] = np.random.RandomState(3).randint(0, 2, 120)  # noise inside the slice
    cv_pred = yy.copy(); cv_pred[:120] = 1 - yy[:120]       # model wrong on the slice
    r = tp.probe_worst_slice(X, yy, cv_pred, "binary_classification")
    assert r["status"] == "warn"
    assert any(s["slice"] == "grp" for s in r["evidence"]["worst_slices"])


def test_calibration_skips_without_proba(clean_data):
    _, y = clean_data
    assert tp.probe_calibration(y, None, "binary_classification")["status"] == "skip"
    assert tp.probe_calibration(y, None, "regression")["status"] == "skip"


def test_grade_caps_on_critical_fail():
    probes = [
        tp._result("a", "X", "t", "critical", "fail", "leak"),
        tp._result("b", "X", "t", "low", "pass", "ok"),
    ]
    score, letter = tp.grade(probes)
    assert score <= 35.0 and letter == "F"


def test_grade_all_pass_is_A():
    probes = [tp._result(str(i), "X", "t", "low", "pass", "ok") for i in range(6)]
    score, letter = tp.grade(probes)
    assert score == 100.0 and letter == "A"


def test_gate_decision_flags_leakage():
    from app.agents.trust_auditor import gate_decision
    report = {
        "grade": "F",
        "probes": [{
            "id": "leakage", "status": "fail",
            "evidence": {"suspicious_features": [
                {"feature": "leak1", "abs_corr_with_target": 0.999},
                {"feature": "weak", "abs_corr_with_target": 0.91},
            ]},
        }],
    }
    d = gate_decision(report)
    assert d["untrustworthy"] is True
    assert d["leaked_columns"] == ["leak1"]  # only >= 0.98 are auto-excluded


def test_gate_decision_clean_run():
    from app.agents.trust_auditor import gate_decision
    report = {"grade": "A", "probes": [{"id": "leakage", "status": "pass", "evidence": {}}]}
    d = gate_decision(report)
    assert d["untrustworthy"] is False and d["leaked_columns"] == []


def test_run_all_end_to_end(tmp_path):
    """Exercise the exact path the sandbox runs: load enriched.csv + model → report."""
    import joblib
    rng = np.random.RandomState(0)
    n = 500
    df = pd.DataFrame({
        "f1": rng.normal(size=n), "f2": rng.normal(size=n),
    })
    y = (df["f1"] + rng.normal(0, 0.5, n) > 0).astype(int)
    df["__target__"] = y
    enriched = tmp_path / "enriched.csv"
    df.to_csv(enriched, index=False)
    model = LogisticRegression().fit(df[["f1", "f2"]], y)
    model_path = tmp_path / "model.pkl"
    joblib.dump(model, model_path)

    report = tp.run_all(str(enriched), str(model_path), "binary_classification", "f1",
                        baseline_score=0.5, final_score=0.82, score_std=0.02, evaluation_basis="holdout")
    assert report["schema_version"] == tp.SCHEMA_VERSION
    assert report["grade"] in ("A", "B", "C", "D", "F")
    ids = {p["id"] for p in report["probes"]}
    assert {"data_integrity", "significance", "eval_honesty", "leakage", "calibration"} <= ids
    assert report["n_pass"] + report["n_warn"] + report["n_fail"] + report["n_skip"] == len(report["probes"])
