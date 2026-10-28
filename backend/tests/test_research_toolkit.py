"""Research toolkit (Tower #5) — features, CV, and the report contract."""

import numpy as np
import pandas as pd
import pytest

from app.research import toolkit as rt


def _trending_df(n=200):
    # A noisy upward trend so features carry signal and models train.
    rng = np.random.default_rng(0)
    series = np.cumsum(rng.normal(0.5, 1.0, n)) + 100
    return pd.DataFrame({"close": series})


def test_features_are_backward_looking_no_nan_leak():
    df = _trending_df(50)
    feats = rt.build_features(df, "close", lags=[1, 2], rolling=[3])
    # lag_1 at row t must equal the target at row t-1 (strictly past).
    assert feats["lag_1"].iloc[0] == pytest.approx(df["close"].iloc[len(df) - len(feats) - 1 + 0])
    assert not feats.isnull().any().any()       # lagging rows dropped, no NaN leak


def test_run_research_walkforward_report():
    cfg = {
        "target": "close", "metric": "rmse",
        "features": {"lags": [1, 2, 3], "rolling": [5]},
        "cv": {"method": "walkforward", "n_splits": 4},
        "models": ["ridge", "random_forest"],
        "backtest": {"cost_bps": 1.0},
        "_df": _trending_df(200),
    }
    rep = rt.run_research(cfg)
    assert rep["winner"] in ("ridge", "random_forest")
    assert set(rep["cv_scores"]) == {"ridge", "random_forest"}
    assert rep["holdout_score"] > 0
    assert "sharpe" in rep["backtest"] and "max_drawdown" in rep["backtest"]


def test_run_research_purged_cv():
    cfg = {
        "target": "close", "metric": "mae",
        "features": {"lags": [1, 2]},
        "cv": {"method": "purged", "n_splits": 4, "embargo_pct": 0.02},
        "models": ["ridge"],
        "_df": _trending_df(200),
    }
    rep = rt.run_research(cfg)
    assert rep["cv_method"] == "purged"
    assert rep["winner"] == "ridge"


def test_too_few_rows_raises():
    cfg = {"target": "close", "features": {"lags": [1]}, "models": ["ridge"],
           "_df": _trending_df(15)}
    with pytest.raises(ValueError):
        rt.run_research(cfg)


def test_cli_parses_run_subcommand(monkeypatch, tmp_path):
    # CLI wires YAML → run_research → report file; stub the heavy run.
    import json
    cfg_file = tmp_path / "params.yaml"
    cfg_file.write_text("target: close\nmodels: [ridge]\nreport: " + str(tmp_path / "r.json"))
    monkeypatch.setattr(rt, "run_research", lambda c: {"winner": "ridge", "holdout_score": 1.0, "backtest": {}})
    assert rt.main(["run", "--config", str(cfg_file)]) == 0
    assert json.loads((tmp_path / "r.json").read_text())["winner"] == "ridge"
