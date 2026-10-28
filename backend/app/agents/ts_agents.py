"""
Time-Series & Quant Studio agents (P3–P6).

Separate pipeline on the shared chassis (BaseAgent, sandbox, exporter). The
non-negotiable quality bar vs. the tabular pipeline: **validity by construction** —
features are strictly backward-looking and ALL evaluation uses walk-forward /
temporal splits (NEVER random K-fold), so scores can't be inflated by future leakage.

Roster: ts_framer → ts_auditor → ts_feature_builder → ts_modeler.
Templates use the token-replace style (.replace("__TOKEN__", repr(value))) so the
dict/f-string-heavy sandbox code keeps natural braces — no .format() brace-doubling.
"""

import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import mlflow_tracker as mlflow
from app.core.state import AgentState


# ── 1. Framer ────────────────────────────────────────────────────────────────
TS_FRAMER_SYSTEM = """You frame a TIME-SERIES FORECASTING problem from a goal and column list.

Respond with JSON ONLY:
{
  "timestamp_column": "<the column holding the date/time index>",
  "target_column": "<the numeric column to forecast>",
  "forecast_horizon": <int steps ahead to predict, default 1>,
  "frequency": "<pandas freq: D|H|W|M, best guess>",
  "primary_metric": "rmse" | "mae" | "mape",
  "decisions": [{"decision": "...", "reasoning": "..."}]
}
Rules: timestamp_column must be a real column. target_column must be numeric and
NOT the timestamp. Default horizon=1, metric=rmse."""


class TSFramerAgent(BaseAgent):
    name = "ts_framer"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Framing the forecasting problem (target, horizon, frequency)...")

        # Lightweight column peek (header only) — never sends raw rows to the LLM.
        import csv, os
        ds = state["dataset_path"]
        with open(ds, newline="", encoding="utf-8-sig") as f:
            header = next(csv.reader(f))

        user = (
            f"Goal: {state['user_goal']}\nColumns: {json.dumps(header)}\n"
            f"Frame the forecasting task."
        )
        resp = await self.llm.complete_json(TS_FRAMER_SYSTEM, user)

        # Boundary validation: untrusted LLM JSON — coerce every field.
        if not isinstance(resp, dict):
            resp = {}
        ts_raw = resp.get("timestamp_column")
        timestamp_col = ts_raw if isinstance(ts_raw, str) and ts_raw else (header[0] if header else "")
        tc_raw = resp.get("target_column")
        target_col = tc_raw if isinstance(tc_raw, str) else ""
        try:
            horizon = max(1, int(resp.get("forecast_horizon") or 1))
        except (TypeError, ValueError):
            horizon = 1
        freq_raw = resp.get("frequency")
        frequency = freq_raw if isinstance(freq_raw, str) and freq_raw else "D"
        metric = self.norm_choice(resp.get("primary_metric"), {"rmse", "mae", "mape"}, "rmse")

        entries = []
        for d in self.norm_dicts(resp.get("decisions")):
            entries.append(await self._log_decision(
                run_id=run_id, decision=d.get("decision", ""),
                reasoning=d.get("reasoning", ""),
            ))

        await self._update_run_field(
            run_id, task_type="time_series_forecasting",
            target_column=target_col, primary_metric=metric,
        )
        await self.emit(
            run_id,
            f"Forecasting {target_col} by {timestamp_col} | horizon={horizon} | metric={metric}",
            {"target": target_col, "timestamp": timestamp_col, "horizon": horizon},
        )
        await self._mark_step(run_id, "completed")
        return {
            "task_type": "time_series_forecasting",
            "timestamp_col": timestamp_col,
            "target_column": target_col,
            "forecast_horizon": horizon,
            "frequency": frequency,
            "primary_metric": metric,
            "decision_log": state.get("decision_log", []) + entries,
        }


# ── 2. Auditor ───────────────────────────────────────────────────────────────
TS_AUDIT_CODE = '''
import pandas as pd
import numpy as np

df = pd.read_csv(dataset_path)
ts_col = __TIMESTAMP_COL__
target = __TARGET_COL__

result = {"verdict": "usable", "warnings": []}

if ts_col not in df.columns:
    result = {"verdict": "abort", "warnings": [f"timestamp column '{ts_col}' not found"]}
elif target not in df.columns:
    result = {"verdict": "abort", "warnings": [f"target column '{target}' not found"]}
else:
    ts = pd.to_datetime(df[ts_col], errors="coerce")
    n_bad = int(ts.isna().sum())
    df = df.assign(__ts__=ts).dropna(subset=["__ts__"]).sort_values("__ts__").reset_index(drop=True)
    n = len(df)
    # frequency / gaps
    deltas = df["__ts__"].diff().dropna()
    median_delta = deltas.median()
    n_gaps = int((deltas > median_delta * 1.5).sum()) if len(deltas) else 0
    dups = int(df["__ts__"].duplicated().sum())
    target_numeric = pd.to_numeric(df[target], errors="coerce")
    warnings = []
    if n_bad: warnings.append(f"{n_bad} unparseable timestamps dropped")
    if dups: warnings.append(f"{dups} duplicate timestamps")
    if n_gaps: warnings.append(f"{n_gaps} irregular gaps (>1.5x median interval)")
    if target_numeric.isna().mean() > 0.1: warnings.append("target has >10% non-numeric/missing")
    verdict = "abort" if n < 30 else "usable"
    if n < 30: warnings.append(f"only {n} usable rows — need >= 30 for walk-forward")
    result = {
        "verdict": verdict,
        "warnings": warnings,
        "n_rows": n,
        "median_interval": str(median_delta),
        "n_gaps": n_gaps,
        "start": str(df["__ts__"].min()),
        "end": str(df["__ts__"].max()),
        "target_mean": float(target_numeric.mean()) if n else 0.0,
    }

RESULT = result
'''


class TSAuditorAgent(BaseAgent):
    name = "ts_auditor"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Auditing the series (ordering, frequency, gaps, length)...")

        code = (
            TS_AUDIT_CODE
            .replace("__TIMESTAMP_COL__", repr(state.get("timestamp_col", "")))
            .replace("__TARGET_COL__", repr(state.get("target_column", "")))
        )
        result = await self.execute_code(code, run_id, timeout=60)
        result = await self.try_agentic_repair(
            run_id, code, result, task_type="time_series_forecasting",
            tags=["ts_auditor"],
            result_keys=["verdict", "n_rows"],
            goal=("Audit the time series at dataset_path (timestamp + target columns): set RESULT "
                  "with verdict ('usable'|'warn'|'abort'), n_rows (int), warnings (list)."),
            timeout=60,
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"TS audit failed: {result['error']}", "status": "failed"}

        data = self.result_dict(result)
        verdict = self.norm_choice(data.get("verdict"), {"usable", "warn", "abort"}, "usable")
        warnings = [str(w) for w in data.get("warnings", [])] \
            if isinstance(data.get("warnings"), list) else []
        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Series audit: {verdict} ({data.get('n_rows', 0)} rows)",
            reasoning=f"Range {data.get('start')} → {data.get('end')}; "
                      f"median interval {data.get('median_interval')}; warnings: {warnings}",
            result_summary=f"verdict={verdict}",
        )
        await self.emit(run_id, f"Audit: {verdict}. Warnings: {len(warnings)}", {"warnings": warnings})

        cell = {
            "agent": self.name, "title": "Time-Series Audit", "iteration": 0,
            "code": code, "stdout": result.get("stdout", ""),
            "result_summary": data,
        }
        if verdict == "abort":
            await self._mark_step(run_id, "failed", "; ".join(warnings))
            return {"error": "; ".join(warnings), "status": "failed"}

        await self._mark_step(run_id, "completed")
        return {
            "data_audit": data,
            "audit_verdict": verdict,
            "decision_log": state.get("decision_log", []) + [entry],
            "notebook_cells": state.get("notebook_cells", []) + [cell],
        }


# ── 3. Feature builder ───────────────────────────────────────────────────────
# Lags + rolling stats + calendar features — STRICTLY backward-looking (shift>=1),
# so no target value at or after time t leaks into the feature row for t.
TS_FEATURE_CODE = '''
import pandas as pd
import numpy as np
import os

df = pd.read_csv(dataset_path)
ts_col = __TIMESTAMP_COL__
target = __TARGET_COL__
horizon = __HORIZON__
n_lags = __N_LAGS__

df["__ts__"] = pd.to_datetime(df[ts_col], errors="coerce")
df = df.dropna(subset=["__ts__"]).sort_values("__ts__").reset_index(drop=True)
y = pd.to_numeric(df[target], errors="coerce").ffill().bfill()

feat = pd.DataFrame(index=df.index)
# Lag features (backward-looking): value at t-1 .. t-n_lags
for lag in range(1, n_lags + 1):
    feat[f"lag_{lag}"] = y.shift(lag)
# Rolling stats over PAST window only (shift(1) then roll)
for w in (3, 7):
    feat[f"roll_mean_{w}"] = y.shift(1).rolling(w).mean()
    feat[f"roll_std_{w}"] = y.shift(1).rolling(w).std()
# Calendar features from the timestamp (known in advance — safe)
feat["dayofweek"] = df["__ts__"].dt.dayofweek
feat["month"] = df["__ts__"].dt.month
feat["day"] = df["__ts__"].dt.day

# Supervised target: value `horizon` steps ahead
feat["__target__"] = y.shift(-horizon + 1) if horizon > 1 else y
# Drop rows with NaN from lag/rolling warmup or horizon tail
feat = feat.dropna().reset_index(drop=True)

os.makedirs(artifacts_dir, exist_ok=True)
enriched_path = os.path.join(artifacts_dir, "ts_supervised.csv")
feat.to_csv(enriched_path, index=False)

RESULT = {
    "enriched_path": enriched_path,
    "n_samples": int(feat.shape[0]),
    "n_features": int(feat.shape[1] - 1),
    "feature_names": [c for c in feat.columns if c != "__target__"],
}
'''


class TSFeatureBuilderAgent(BaseAgent):
    name = "ts_feature_builder"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Building backward-looking features (lags, rolling, calendar)...")

        code = (
            TS_FEATURE_CODE
            .replace("__TIMESTAMP_COL__", repr(state.get("timestamp_col", "")))
            .replace("__TARGET_COL__", repr(state.get("target_column", "")))
            .replace("__HORIZON__", repr(int(state.get("forecast_horizon", 1))))
            .replace("__N_LAGS__", repr(7))
        )
        result = await self.execute_code(code, run_id, timeout=120)
        result = await self.try_agentic_repair(
            run_id, code, result, task_type="time_series_forecasting",
            tags=["ts_feature_builder"],
            result_keys=["n_features", "n_samples", "enriched_path"],
            goal=("Build forecasting features from dataset_path and write enriched.csv. CRITICAL: "
                  "every feature MUST be strictly BACKWARD-LOOKING (lags, SHIFTED rolling mean/std, "
                  "calendar fields) — NEVER use the current or any future row's value (that is "
                  "look-ahead leakage and invalidates the model). Set RESULT with n_features (int), "
                  "n_samples (int), enriched_path (str)."),
            timeout=120,
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"TS feature build failed: {result['error']}", "status": "failed"}

        data = self.result_dict(result)
        # Contract guard: without the enriched CSV the modeler cannot run.
        if not data.get("enriched_path"):
            msg = "TS feature build produced no enriched_path (malformed sandbox RESULT)"
            await self._mark_step(run_id, "failed", msg)
            return {"error": msg, "status": "failed"}
        data.setdefault("n_features", 0)
        data.setdefault("n_samples", 0)
        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Built {data['n_features']} backward-looking features over {data['n_samples']} samples",
            reasoning="Lags 1-7, rolling mean/std (windows 3,7) on shifted series, calendar features. "
                      "All strictly past-only to prevent look-ahead leakage.",
            result_summary=f"n_features={data['n_features']}",
        )
        cell = {
            "agent": self.name, "title": "Feature Engineering (lags & calendar)", "iteration": 0,
            "code": code, "stdout": result.get("stdout", ""), "result_summary": data,
        }
        await self._mark_step(run_id, "completed")
        return {
            "enriched_data_path": data["enriched_path"],
            "features_created": [{"name": str(n)} for n in (data.get("feature_names") or [])
                                 if isinstance(n, (str, int))],
            "decision_log": state.get("decision_log", []) + [entry],
            "notebook_cells": state.get("notebook_cells", []) + [cell],
        }


# ── 4. Modeler (walk-forward train + select + evaluate) ──────────────────────
TS_MODEL_CODE = '''
import pandas as pd
import numpy as np
import joblib
import os
import warnings
warnings.filterwarnings("ignore")
from sklearn.model_selection import TimeSeriesSplit
from sklearn.linear_model import Ridge
from sklearn.ensemble import RandomForestRegressor, GradientBoostingRegressor
from sklearn.metrics import mean_squared_error, mean_absolute_error

df = pd.read_csv(__ENRICHED_PATH__)
y = df["__target__"].values
X = df.drop(columns=["__target__"]).values
metric = __METRIC__

def score(y_true, y_pred):
    if metric == "mae":
        return mean_absolute_error(y_true, y_pred)
    if metric == "mape":
        denom = np.where(np.abs(y_true) < 1e-9, 1e-9, np.abs(y_true))
        return float(np.mean(np.abs((y_true - y_pred) / denom)) * 100)
    return float(np.sqrt(mean_squared_error(y_true, y_pred)))  # rmse default

candidates = {
    "Ridge": Ridge(),
    "RandomForest": RandomForestRegressor(n_estimators=200, random_state=42, n_jobs=1),
    "GradientBoosting": GradientBoostingRegressor(random_state=42),
}

# DL-1 sequence models (LSTM/GRU/TCN) — each row's lag features form its own
# window (oldest -> newest), so TimeSeriesSplit validation stays leak-free.
dl_enabled = __DL_ENABLED__
if dl_enabled and len(df) >= 200:
    try:
        from automl_dl import TorchSeqRegressor
        _cols = list(df.drop(columns=["__target__"]).columns)
        _lag_cols = sorted([c for c in _cols if c.startswith("lag_")],
                           key=lambda c: -int(c.split("_")[1]))
        if len(_lag_cols) >= 3:
            _seq_idx = [_cols.index(c) for c in _lag_cols]
            for _cell in ("lstm", "gru", "tcn"):
                candidates[_cell.upper()] = TorchSeqRegressor(
                    cell=_cell, seq_idx=_seq_idx, max_epochs=60, random_state=42)
    except Exception:
        pass  # torch not in this image — sklearn candidates carry the run

# WALK-FORWARD cross-validation — expanding window, never shuffles, never sees future.
n_splits = min(5, max(2, len(df) // 20))
tscv = TimeSeriesSplit(n_splits=n_splits)

# Naive baseline: predict last observed value (lag_1 if present, else previous y)
lag1_idx = [i for i, c in enumerate(df.drop(columns=["__target__"]).columns) if c == "lag_1"]
results = {}
for name, model in candidates.items():
    fold_scores = []
    for tr, te in tscv.split(X):
        model.fit(X[tr], y[tr])
        fold_scores.append(score(y[te], model.predict(X[te])))
    results[name] = float(np.mean(fold_scores))

# Naive baseline score under the same folds
naive_scores = []
for tr, te in tscv.split(X):
    if lag1_idx:
        pred = X[te][:, lag1_idx[0]]
    else:
        pred = np.full(len(te), y[tr][-1])
    naive_scores.append(score(y[te], pred))
baseline_score = float(np.mean(naive_scores))

# Winner = lowest error (all three metrics are "lower is better")
winner = min(results, key=lambda k: results[k])
winner_score = results[winner]

# Temporal hold-out (last 20% by time) for an unbiased final number + forecast plot
split = int(len(X) * 0.8)
final_model = candidates[winner]
final_model.fit(X[:split], y[:split])
holdout_pred = final_model.predict(X[split:])
holdout_score = score(y[split:], holdout_pred)

# DL-2b — probabilistic band: a quantile GRU (pinball loss, P10/50/90) fit on
# the train split gives the hold-out an uncertainty band + empirical coverage.
quantile_band = None
if dl_enabled and len(df) >= 200:
    try:
        from automl_dl import TorchSeqRegressor as _TSR
        _cols2 = list(df.drop(columns=["__target__"]).columns)
        _lags2 = sorted([c for c in _cols2 if c.startswith("lag_")],
                        key=lambda c: -int(c.split("_")[1]))
        if len(_lags2) >= 3:
            _qm = _TSR(cell="gru", seq_idx=[_cols2.index(c) for c in _lags2],
                       quantiles=(0.1, 0.5, 0.9), max_epochs=60, random_state=42)
            _qm.fit(X[:split], y[:split])
            _band = _qm.predict_quantiles(X[split:])
            _lo, _hi = _band[0.1], _band[0.9]
            _cov = float(np.mean((y[split:] >= _lo) & (y[split:] <= _hi)))
            quantile_band = {
                "quantiles": [0.1, 0.5, 0.9],
                "p10": [float(v) for v in _lo],
                "p50": [float(v) for v in _band[0.5]],
                "p90": [float(v) for v in _hi],
                "actual": [float(v) for v in y[split:]],
                "empirical_coverage_p10_p90": round(_cov, 3),
            }
    except Exception:
        quantile_band = None   # band is a bonus — never fails the run

# Refit on ALL data for the saved/deployable model
final_model.fit(X, y)
os.makedirs(artifacts_dir, exist_ok=True)
model_path = os.path.join(artifacts_dir, "tuned_model.pkl")
joblib.dump(final_model, model_path)

# Forecast-vs-actual plot on the hold-out
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(range(len(y[split:])), y[split:], label="actual", linewidth=1.5)
    ax.plot(range(len(holdout_pred)), holdout_pred, label="forecast", linewidth=1.5, alpha=0.8)
    ax.set_title("Walk-forward hold-out: forecast vs actual")
    ax.legend()
    plt.tight_layout()
    fig.savefig(os.path.join(artifacts_dir, "forecast_plot.png"), dpi=100)
    plt.close()
    plot_made = True
except Exception:
    plot_made = False

RESULT = {
    "winner": winner,
    "winner_cv_score": winner_score,
    "holdout_score": float(holdout_score),
    "baseline_score": baseline_score,
    "all_cv_scores": results,
    "model_path": model_path,
    "n_splits": n_splits,
    "metric": metric,
    "plot_made": plot_made,
    # Hold-out actuals + forecasts for the backend trading diagnostic (Sharpe/drawdown).
    "holdout_actual": [float(v) for v in y[split:]],
    "holdout_pred": [float(v) for v in holdout_pred],
    "quantile_band": quantile_band,
}
'''


def trading_diagnostic(holdout_actual, holdout_pred, cost_bps: float = 1.0):
    """Directional trading diagnostic for a forecast on a PRICE-LIKE series:
    go long/short based on whether the forecast is above/below the last actual, then
    score the period-over-period returns. Returns a backtest summary (Sharpe, drawdown,
    turnover, hit-rate, equity curve) or None if the series is too short. Meaningful only
    when the target is a level/price; reported as an illustrative diagnostic."""
    import numpy as np
    from app.core import backtest

    a = np.asarray(holdout_actual, dtype=float)
    p = np.asarray(holdout_pred, dtype=float)
    if a.size < 3 or p.size < 3:
        return None
    prev = a[:-1]
    denom = np.where(np.abs(prev) < 1e-9, 1e-9, np.abs(prev))
    actual_ret = (a[1:] - prev) / denom          # realized period returns
    signal = np.sign(p[1:] - prev)                # predicted direction vs last actual
    return backtest.backtest_summary(actual_ret, signal, cost_bps=cost_bps)


class TSModelerAgent(BaseAgent):
    name = "ts_modeler"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Walk-forward training & evaluation (TimeSeriesSplit)...")

        metric = state.get("primary_metric", "rmse")
        from app.config import settings
        code = (
            TS_MODEL_CODE
            .replace("__ENRICHED_PATH__", repr(state.get("enriched_data_path", "")))
            .replace("__METRIC__", repr(metric))
            .replace("__DL_ENABLED__", repr(bool(settings.DL_ENABLED)))
        )
        result = await self.execute_code(code, run_id, timeout=400)
        result = await self.try_agentic_repair(
            run_id, code, result, task_type="time_series_forecasting",
            tags=["ts_modeler"],
            result_keys=["winner", "winner_cv_score", "baseline_score", "holdout_score"],
            goal=("Evaluate forecasting models on enriched.csv. CRITICAL: validate ONLY with "
                  "sklearn TimeSeriesSplit / walk-forward / a final temporal hold-out (the test rows "
                  "must be strictly LATER than train rows). NEVER use random KFold/train_test_split "
                  "shuffling — that leaks the future and invalidates the score. Refit the winner on "
                  "all data. Set RESULT with winner (str), winner_cv_score (float), baseline_score "
                  "(float, naive seasonal), holdout_score (float)."),
            timeout=400,
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"TS modeling failed: {result['error']}", "status": "failed"}

        data = self.result_dict(result)

        # Boundary validation: normalize before any formatted access — a missing
        # or non-numeric field must fail the step cleanly, never a KeyError or a
        # ':.4f' TypeError.
        def _f(key: str, default: float = 0.0) -> float:
            try:
                return float(data.get(key))
            except (TypeError, ValueError):
                return default

        winner = data.get("winner")
        if not isinstance(winner, str) or not winner or not data.get("model_path"):
            msg = "TS modeling produced no winner/model_path (malformed sandbox RESULT)"
            await self._mark_step(run_id, "failed", msg)
            return {"error": msg, "status": "failed"}
        winner_score = _f("winner_cv_score")
        baseline = _f("baseline_score")
        data["holdout_score"] = _f("holdout_score")
        data.setdefault("n_splits", "?")
        if not isinstance(data.get("all_cv_scores"), dict):
            data["all_cv_scores"] = {}
        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Winner {winner}: walk-forward {metric}={winner_score:.4f} "
                     f"(naive baseline={baseline:.4f}, hold-out={data['holdout_score']:.4f})",
            reasoning=f"{data['n_splits']}-split TimeSeriesSplit over candidates {data['all_cv_scores']}. "
                      f"Lower {metric} is better. Final model refit on all data.",
            result_summary=f"{metric}={winner_score:.4f}",
        )
        mlflow.log_metrics({
            f"ts_cv_{metric}": winner_score,
            f"ts_holdout_{metric}": data["holdout_score"],
            f"ts_baseline_{metric}": baseline,
        })

        # Quant trading diagnostic (Sharpe/drawdown/turnover) on the hold-out forecast.
        try:
            bt_metrics = trading_diagnostic(data.get("holdout_actual", []), data.get("holdout_pred", []))
        except Exception as exc:  # diagnostic only — never fail the step over it
            self._log.warning("trading_diagnostic_failed", run_id=run_id, error=str(exc)[:200])
            bt_metrics = None
        if bt_metrics:
            await self._log_decision(
                run_id=run_id,
                decision=f"Trading diagnostic: Sharpe {bt_metrics['sharpe']}, "
                         f"max drawdown {bt_metrics['max_drawdown']}, hit-rate {bt_metrics['hit_rate']}",
                reasoning="Directional long/short strategy from the forecast vs. last actual, "
                          "net of transaction costs. Illustrative — meaningful for price-like series.",
                result_summary=f"sharpe={bt_metrics['sharpe']}, total_return={bt_metrics['total_return']}",
            )
            mlflow.log_metrics({
                "ts_sharpe": bt_metrics["sharpe"],
                "ts_max_drawdown": bt_metrics["max_drawdown"],
            })

        # Log plot artifact
        import os
        plot = os.path.join(state.get("data_dir", "/data"), run_id, "artifacts", "forecast_plot.png")
        if data.get("plot_made"):
            mlflow.log_artifact(plot)

        cell = {
            "agent": self.name, "title": "Walk-Forward Training & Evaluation", "iteration": 0,
            "code": code, "stdout": result.get("stdout", ""), "result_summary": data,
        }
        await self.emit(
            run_id,
            f"Winner: {winner} | {metric} {winner_score:.4f} (hold-out {data['holdout_score']:.4f})",
            {"winner": winner, "score": winner_score},
        )
        await self._update_run_field(
            run_id, winner_model=winner, baseline_score=baseline,
            final_score=data["holdout_score"], iteration_count=1,
        )
        await self._mark_step(run_id, "completed")
        return {
            "winner_model": winner,
            "winner_model_path": data["model_path"],
            "tuned_model_path": data["model_path"],
            "baseline_score": baseline,
            "current_score": data["holdout_score"],
            "tuned_score": data["holdout_score"],
            "evaluation_report": {
                "metrics": {
                    metric: data["holdout_score"],
                    f"cv_{metric}": winner_score,
                    f"baseline_{metric}": baseline,
                },
                # DL-2b probabilistic band (P10/50/90 + empirical coverage), when built
                "quantile_band": data.get("quantile_band"),
            },
            "iteration_scores": [data["holdout_score"]],
            "backtest_metrics": bt_metrics or {},
            "decision_log": state.get("decision_log", []) + [entry],
            "notebook_cells": state.get("notebook_cells", []) + [cell],
        }
