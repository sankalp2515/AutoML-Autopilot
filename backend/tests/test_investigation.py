"""Phase 2 — hypothesis-loop scaffold + primitive behavior."""
import numpy as np
import pandas as pd
import pytest

from app.core import investigation as inv

pytest.importorskip("sklearn")
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier
from app.core import investigation_primitives as ip


# ── scaffold (no sklearn needed, but grouped here) ────────────────────────────
def test_parse_hypotheses_keeps_valid_drops_bad():
    obj = {"hypotheses": [
        {"id": "h1", "statement": "leak", "primitive": "target_correlation",
         "params": {"column": "x"}, "expected": {"direction": ">", "threshold": 0.95}, "severity": "high"},
        {"id": "h2", "primitive": "unknown_primitive", "params": {}, "expected": {"direction": ">", "threshold": 1}},
        {"id": "h3", "primitive": "feature_ablation", "params": {}, "expected": {"direction": ">", "threshold": 1}},  # missing column
        {"id": "h4", "primitive": "shift_stress", "params": {}, "expected": {"direction": "??", "threshold": 1}},  # bad dir
    ]}
    out = inv.parse_hypotheses(obj)
    assert [h["id"] for h in out] == ["h1"]


def test_parse_hypotheses_accepts_bare_list():
    out = inv.parse_hypotheses([
        {"primitive": "shift_stress", "params": {}, "expected": {"direction": ">", "threshold": 0.2}},
    ])
    assert len(out) == 1 and out[0]["id"] == "h1"


def test_select_ranks_by_severity_and_caps():
    hyps = [
        {"id": f"h{i}", "severity": s, "primitive": "shift_stress", "params": {"magnitude": i}}
        for i, s in enumerate(["low", "high", "medium", "high", "low", "medium"])
    ]
    sel = inv.select(hyps, {"max_hypotheses": 3, "max_followups": 1, "max_llm_calls": 8})
    assert len(sel) == 3 and sel[0]["severity"] == "high"


def test_select_dedupes_identical():
    hyps = [
        {"id": "a", "severity": "high", "primitive": "target_correlation", "params": {"column": "x"}},
        {"id": "b", "severity": "high", "primitive": "target_correlation", "params": {"column": "x"}},
    ]
    assert len(inv.select(hyps)) == 1


def test_verdict_confirmed_refuted_inconclusive():
    h = {"expected": {"direction": ">", "threshold": 0.9}}
    assert inv.verdict(h, 0.98)["verdict"] == "confirmed"
    assert inv.verdict(h, 0.5)["verdict"] == "refuted"
    assert inv.verdict(h, None, error="crash")["verdict"] == "inconclusive"


def test_aggregate_counts_and_summary():
    results = [
        {"verdict": "confirmed", "statement": "X leaks"},
        {"verdict": "refuted", "statement": "Y"},
        {"verdict": "inconclusive", "statement": "Z"},
    ]
    rep = inv.aggregate("r1", results, {"llm_calls": 2})
    assert rep["n_confirmed"] == 1 and rep["n_refuted"] == 1 and rep["n_inconclusive"] == 1
    assert "X leaks" in rep["summary"]


def test_catalog_text_lists_primitives():
    txt = inv.catalog_text()
    assert "feature_ablation" in txt and "target_correlation" in txt


# ── primitive behavior ────────────────────────────────────────────────────────
@pytest.fixture
def fitted():
    rng = np.random.RandomState(0)
    n = 400
    X = pd.DataFrame({
        "signal": rng.normal(size=n), "noise": rng.normal(size=n),
        "grp": (np.arange(n) < 120).astype(float),
    })
    y = (X["signal"] + rng.normal(0, 0.4, n) > 0).astype(int).to_numpy()
    model = RandomForestClassifier(n_estimators=30, random_state=0).fit(X, y)
    base = ip._cv_score(model, X, y, "binary_classification")
    cvp = ip._cv_predict(model, X, y, "binary_classification")
    return model, X, y, base, cvp


def test_target_correlation_high_for_leak(fitted):
    model, X, y, base, cvp = fitted
    X = X.copy(); X["leak"] = y.astype(float)
    model2 = LogisticRegression(max_iter=200).fit(X, y)
    obs, ev = ip.target_correlation(model2, X, y, "binary_classification", {"column": "leak"}, base, cvp)
    assert obs > 0.95


def test_single_feature_power_high_for_strong_feature(fitted):
    model, X, y, base, cvp = fitted
    obs, ev = ip.single_feature_power(model, X, y, "binary_classification", {"column": "signal"}, base, cvp)
    assert obs > 0.5  # 'signal' alone carries most of the signal


def test_feature_ablation_drop_for_important(fitted):
    model, X, y, base, cvp = fitted
    obs, ev = ip.feature_ablation(model, X, y, "binary_classification", {"column": "signal"}, base, cvp)
    assert obs > 0  # removing the real feature hurts


def test_shift_stress_returns_relative_drop(fitted):
    model, X, y, base, cvp = fitted
    obs, ev = ip.shift_stress(model, X, y, "binary_classification", {"magnitude": 0.1}, base, cvp)
    assert "relative_drop" in ev and isinstance(obs, float)


def test_unknown_column_raises(fitted):
    model, X, y, base, cvp = fitted
    with pytest.raises(ValueError):
        ip.feature_ablation(model, X, y, "binary_classification", {"column": "nope"}, base, cvp)


def test_run_primitives_end_to_end(tmp_path):
    import joblib
    rng = np.random.RandomState(0)
    n = 400
    df = pd.DataFrame({"signal": rng.normal(size=n), "noise": rng.normal(size=n)})
    y = (df["signal"] + rng.normal(0, 0.4, n) > 0).astype(int)
    df["__target__"] = y
    enriched = tmp_path / "enriched.csv"; df.to_csv(enriched, index=False)
    model = LogisticRegression(max_iter=200).fit(df[["signal", "noise"]], y)
    mp = tmp_path / "m.pkl"; joblib.dump(model, mp)

    specs = [
        {"id": "h1", "primitive": "single_feature_power", "params": {"column": "signal"}},
        {"id": "h2", "primitive": "feature_ablation", "params": {"column": "noise"}},
        {"id": "h3", "primitive": "unknown", "params": {}},          # → error, doesn't crash
        {"id": "h4", "primitive": "feature_ablation", "params": {"column": "ghost"}},  # bad col → error
    ]
    results = ip.run_primitives(str(enriched), str(mp), "binary_classification", specs)
    assert len(results) == 4
    by = {r["id"]: r for r in results}
    assert by["h1"]["observed"] is not None
    assert by["h3"]["error"] == "unknown primitive"
    assert by["h4"]["error"] is not None and by["h4"]["observed"] is None
