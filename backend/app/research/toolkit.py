"""Reproducible research toolkit (Tower track — #5).

A self-contained CLI a quant researcher runs locally:

    python -m app.research.toolkit run --config params.yaml

Given a CSV + a YAML config, it builds backward-looking time-series features,
trains several model variants, evaluates them with leakage-safe CV (walk-forward
or purged/embargoed K-fold), runs a transaction-cost-aware backtest on a temporal
hold-out, and writes a JSON report. Deterministic and decoupled from the agentic
web app — it reuses app.core.backtest + app.core.cv so the same metrics back both.

Config (YAML):
    data: data/prices.csv
    target: close
    features: {lags: [1,2,3,5], rolling: [5,10]}
    cv: {method: walkforward|purged, n_splits: 5, embargo_pct: 0.01}
    models: [ridge, random_forest, gradient_boosting]
    backtest: {cost_bps: 1.0}
    metric: rmse
    report: out/report.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import TimeSeriesSplit

from app.core import backtest as bt
from app.core.cv import PurgedKFold

_MODELS = {
    "ridge": lambda: Ridge(),
    "random_forest": lambda: RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=1),
    "gradient_boosting": lambda: GradientBoostingRegressor(random_state=42),
}


def _score(y_true, y_pred, metric: str) -> float:
    if metric == "mae":
        return float(mean_absolute_error(y_true, y_pred))
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))  # rmse default


def _evaluate_variant(task):
    """CV-evaluate one model variant. TOP-LEVEL + picklable so it can run as a Ray
    task. `splits` is a materialized list of (train_idx, test_idx) arrays."""
    name, X, y, splits, metric = task
    fold = []
    for tr, te in splits:
        m = _MODELS[name]()
        m.fit(X[tr], y[tr])
        fold.append(_score(y[te], m.predict(X[te]), metric))
    return name, round(float(np.mean(fold)), 6)


def build_features(df: pd.DataFrame, target: str, lags, rolling) -> pd.DataFrame:
    """Strictly backward-looking features (no look-ahead): lagged target + SHIFTED
    rolling mean/std. Row t only sees information available at t-1."""
    out = pd.DataFrame(index=df.index)
    s = df[target].astype(float)
    for lag in (lags or [1]):
        out[f"lag_{lag}"] = s.shift(lag)
    for w in (rolling or []):
        out[f"rollmean_{w}"] = s.shift(1).rolling(w).mean()
        out[f"rollstd_{w}"] = s.shift(1).rolling(w).std()
    out["__target__"] = s
    return out.dropna()


def _splitter(cv_cfg: dict, n: int):
    method = (cv_cfg or {}).get("method", "walkforward")
    n_splits = int((cv_cfg or {}).get("n_splits", 5))
    if method == "purged":
        return PurgedKFold(n_splits=n_splits, embargo_pct=float(cv_cfg.get("embargo_pct", 0.01)))
    return TimeSeriesSplit(n_splits=n_splits)


def run_research(config: dict) -> dict:
    """Execute the research run described by `config` (a dict). Returns a report dict."""
    target = config["target"]
    metric = config.get("metric", "rmse")
    df = pd.read_csv(config["data"]) if "data" in config else config["_df"]  # _df for tests

    feats = build_features(df, target, config.get("features", {}).get("lags"),
                           config.get("features", {}).get("rolling"))
    y = feats["__target__"].to_numpy()
    X = feats.drop(columns="__target__").to_numpy()
    if len(X) < 20:
        raise ValueError(f"Not enough rows after feature engineering ({len(X)}); need >= 20.")

    splitter = _splitter(config.get("cv", {}), len(X))
    model_names = config.get("models", ["ridge", "random_forest", "gradient_boosting"])

    # Materialize CV splits once (picklable index arrays), then evaluate each model
    # variant — in parallel via Ray when enabled, else sequentially.
    from app.core.parallel import parallel_map
    splits = [(tr, te) for tr, te in splitter.split(X)]
    tasks = [(name, X, y, splits, metric) for name in model_names]
    cv_scores: dict[str, float] = dict(parallel_map(_evaluate_variant, tasks))

    winner = min(cv_scores, key=cv_scores.get)  # lower error is better

    # Temporal hold-out (last 20%) for an unbiased final score + the backtest.
    split = int(len(X) * 0.8)
    best = _MODELS[winner]()
    best.fit(X[:split], y[:split])
    holdout_pred = best.predict(X[split:])
    holdout_actual = y[split:]
    holdout_score = _score(holdout_actual, holdout_pred, metric)

    # Directional trading backtest on the hold-out (price-like series).
    prev = holdout_actual[:-1]
    denom = np.where(np.abs(prev) < 1e-9, 1e-9, np.abs(prev))
    actual_ret = (holdout_actual[1:] - prev) / denom
    signal = np.sign(holdout_pred[1:] - prev)
    cost_bps = float(config.get("backtest", {}).get("cost_bps", 1.0))
    backtest = bt.backtest_summary(actual_ret, signal, cost_bps=cost_bps) if len(actual_ret) >= 3 else {}

    report = {
        "config": {k: v for k, v in config.items() if not k.startswith("_")},
        "n_rows": int(len(X)),
        "cv_method": config.get("cv", {}).get("method", "walkforward"),
        "metric": metric,
        "cv_scores": cv_scores,
        "winner": winner,
        "holdout_score": round(holdout_score, 6),
        "backtest": backtest,
    }
    return report


def _load_yaml(path: str) -> dict:
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="research", description="Reproducible quant research toolkit")
    sub = parser.add_subparsers(dest="cmd", required=True)
    runp = sub.add_parser("run", help="run a research config")
    runp.add_argument("--config", required=True, help="path to YAML config")
    args = parser.parse_args(argv)

    if args.cmd == "run":
        config = _load_yaml(args.config)
        report = run_research(config)
        out = config.get("report")
        if out:
            Path(out).parent.mkdir(parents=True, exist_ok=True)
            Path(out).write_text(json.dumps(report, indent=2))
        print(json.dumps({"winner": report["winner"], "holdout_score": report["holdout_score"],
                          "sharpe": report["backtest"].get("sharpe"),
                          "max_drawdown": report["backtest"].get("max_drawdown")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
