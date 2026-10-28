from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import metric_registry
from app.core import mlflow_tracker as mlflow
from app.core.state import AgentState

BASELINE_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import json
import warnings
warnings.filterwarnings("ignore")

from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import LabelEncoder, StandardScaler, OneHotEncoder
from sklearn.compose import ColumnTransformer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.multioutput import MultiOutputClassifier
from sklearn.model_selection import cross_val_score, StratifiedKFold, KFold
from sklearn.metrics import (roc_auc_score, f1_score, mean_squared_error,
                              r2_score, accuracy_score, hamming_loss)
from sklearn.dummy import DummyClassifier, DummyRegressor
import joblib
import os

df = pd.read_csv(dataset_path)

target_col = "{target_column}"
task_type = "{task_type}"
exclude_cols = {exclude_cols}
label_columns = {label_columns}
label_delimiter = "{label_delimiter}"

# Drop excluded and non-feature columns
drop_cols = [c for c in exclude_cols if c in df.columns]
df = df.drop(columns=drop_cols)

# Resolve target(s) per task — multilabel may have NO single target column
# (labels live in label_columns), so we must not dropna/drop on a "None" target.
if task_type == "multilabel_classification" and label_columns:
    label_cols = [c for c in label_columns if c in df.columns]
    df = df.dropna(subset=label_cols)
    X = df.drop(columns=label_cols)
    y = df[label_cols].values.astype(int)
elif task_type == "multilabel_classification" and label_delimiter:
    if target_col not in df.columns:
        raise ValueError("multilabel delimiter target column not found: " + str(target_col))
    df = df.dropna(subset=[target_col])
    X = df.drop(columns=[target_col])
    from sklearn.preprocessing import MultiLabelBinarizer
    yy = df[target_col].astype(str).apply(lambda v: v.split(label_delimiter) if v else [])
    mlb = MultiLabelBinarizer()
    y = mlb.fit_transform(yy)
else:
    # Single-label classification or regression — require a real target column
    if not target_col or target_col not in df.columns:
        raise ValueError("target column not found in dataset: " + str(target_col))
    df = df.dropna(subset=[target_col])
    X = df.drop(columns=[target_col])
    y = df[target_col]
    if task_type != "regression":
        le = LabelEncoder()
        y = le.fit_transform(y.astype(str))

# Identify numeric and categorical columns
num_cols = X.select_dtypes(include=["number"]).columns.tolist()
cat_cols = X.select_dtypes(exclude=["number"]).columns.tolist()

# Minimal preprocessing pipeline
preprocessor = ColumnTransformer([
    ("num", Pipeline([("imp", SimpleImputer(strategy="median")),
                      ("scale", StandardScaler())]), num_cols),
    ("cat", Pipeline([("imp", SimpleImputer(strategy="most_frequent")),
                      ("enc", OneHotEncoder(handle_unknown="ignore", sparse_output=False))]),
     cat_cols),
], remainder="drop")

# Baseline model
if task_type == "regression":
    model = Ridge()
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
elif task_type == "multilabel_classification":
    model = MultiOutputClassifier(LogisticRegression(max_iter=1000, random_state=42, class_weight="balanced"))
    cv = KFold(n_splits=5, shuffle=True, random_state=42)
else:
    model = LogisticRegression(max_iter=1000, random_state=42, class_weight="balanced")
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

# The baseline used to hardcode its OWN scorer per task, ignoring the run's
# primary_metric entirely — so the "performance floor" was measured on a
# different metric than every candidate that had to beat it. Now emitted from
# metric_registry like the other three templates.
{scorer_prelude}

# The sign-flip below is valid ONLY when the resolved scorer is a neg_* one.
# It used to be keyed on `task_type == "regression"` alone, which was safe only
# while the scorer was hardcoded to neg_root_mean_squared_error. Now that the
# baseline honours primary_metric, an r2 run would have reported -r2.
_NEG = isinstance(scoring, str) and scoring.startswith("neg_")

pipeline = Pipeline([("prep", preprocessor), ("model", model)])
scores = cross_val_score(pipeline, X, y, cv=cv, scoring=scoring, n_jobs=1)

if _NEG:
    baseline_score = float(-scores.mean())
else:
    baseline_score = float(scores.mean())

# Dummy baseline for comparison
if task_type == "regression":
    dummy = DummyRegressor(strategy="mean")
    dummy_scores = cross_val_score(dummy, X, y, cv=cv,
                                   scoring=scoring, n_jobs=1)
    dummy_score = float(-dummy_scores.mean()) if _NEG else float(dummy_scores.mean())
elif task_type == "multilabel_classification":
    # constant=0 fails on 2-D multilabel targets; per-label most_frequent is the
    # correct naive floor.
    dummy = MultiOutputClassifier(DummyClassifier(strategy="most_frequent"))
    dummy_scores = cross_val_score(dummy, X, y, cv=cv,
                                   scoring=scoring, n_jobs=1)
    dummy_score = float(dummy_scores.mean())
else:
    dummy = DummyClassifier(strategy="most_frequent")
    dummy_scores = cross_val_score(dummy, X, y, cv=cv,
                                   scoring=scoring, n_jobs=1)
    dummy_score = float(dummy_scores.mean())

# Fit on full data and save the baseline pipeline for error analysis
pipeline.fit(X, y)

os.makedirs(artifacts_dir, exist_ok=True)
baseline_path = os.path.join(artifacts_dir, "baseline_pipeline.pkl")
joblib.dump(pipeline, baseline_path)

# Error analysis: find hard samples (where baseline is most wrong)
if task_type != "regression":
    try:
        if task_type == "multilabel_classification":
            y_pred = pipeline.predict(X)
            # For multilabel, compute subset accuracy (exact match)
            error_mask = (y_pred != y).any(axis=1)
            error_rate = float(error_mask.mean())
        else:
            y_pred_proba = pipeline.predict_proba(X)[:, 1] if task_type == "binary_classification" \
                else pipeline.predict_proba(X).max(axis=1)
            y_pred = pipeline.predict(X)
            error_mask = (y_pred != y)
            error_rate = float(error_mask.mean())
        hard_sample_profile = df[error_mask].describe().to_dict() if error_mask.any() else {{}}
    except Exception:
        hard_sample_profile = {{}}
        error_rate = 0.0
else:
    y_pred = pipeline.predict(X)
    residuals = np.abs(y - y_pred)
    error_mask = residuals > residuals.quantile(0.75)
    hard_sample_profile = df[error_mask].describe().to_dict() if error_mask.any() else {{}}
    error_rate = float(error_mask.mean())

RESULT = {{
    "baseline_score": baseline_score,
    "dummy_score": dummy_score,
    "baseline_model": "LogisticRegression" if task_type != "regression" else "Ridge",
    "score_std": float(scores.std()),
    "n_features": X.shape[1],
    "n_samples": X.shape[0],
    "baseline_path": baseline_path,
    "error_rate_on_train": error_rate,
    "metric_used": "{scoring}",
}}
'''


class BaselineBuilderAgent(BaseAgent):
    name = "baseline_builder"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Training baseline model first — this establishes our performance floor...")

        # Scorer resolution comes from metric_registry (single source of truth) —
        # it applies the multiclass remap so binary-only scorers don't NaN.
        scoring = metric_registry.sklearn_scorer(
            state.get("primary_metric", ""), state["task_type"]
        )

        code = BASELINE_CODE_TEMPLATE.format(
            target_column=state["target_column"],
            task_type=state["task_type"],
            exclude_cols=repr(state.get("exclude_columns", [])),
            scoring=scoring,
            scorer_prelude=metric_registry.scorer_prelude(
                state.get("primary_metric", ""), state["task_type"],
                k=float(state.get("rare_event_k") or metric_registry.DEFAULT_K),
            ),
            label_columns=repr(state.get("label_columns", [])),
            label_delimiter=state.get("label_delimiter", ""),
        )

        result = await self.execute_code(code, run_id, timeout=180)
        result = await self.try_agentic_repair(
            run_id, code, result,
            task_type=state.get("task_type", "unknown"),
            result_keys=["baseline_score", "dummy_score", "baseline_model", "metric_used", "n_samples", "n_features"],
            goal=("Train a simple baseline (LogisticRegression/Ridge) on dataset_path with minimal "
                  "preprocessing and 5-fold CV. Set RESULT with baseline_score (float), dummy_score "
                  "(float, naive baseline), baseline_model (str), metric_used (str), n_samples (int)."),
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"Baseline training failed: {result['error']}", "status": "failed"}

        data = result["result"] or {}
        # Contract guard: a missing essential key must fail this step cleanly (the
        # graph handles that), never crash the whole pipeline with a KeyError that
        # the sandbox self-repair can't see.
        if data.get("baseline_score") is None:
            await self._mark_step(run_id, "failed", "baseline produced no score (RESULT incomplete)")
            return {"error": "Baseline training produced no usable RESULT", "status": "failed"}
        baseline_score = data["baseline_score"]
        dummy_score = data.get("dummy_score", 0.0) or 0.0
        baseline_model = data.get("baseline_model", "baseline")
        metric_used = data.get("metric_used", scoring)
        n_samples = data.get("n_samples", 0)
        n_features = data.get("n_features", 0)
        # Directional: positive = the baseline genuinely beats the naive model.
        # (A bare subtraction reported a strong rmse baseline as "-97% lift".)
        from app.core import metric_registry as _mreg
        _gain = _mreg.directional_gain(baseline_score, dummy_score, metric_used)
        lift = (_gain / abs(dummy_score)) * 100 if dummy_score else 0

        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Baseline established: {baseline_model} achieves {baseline_score:.4f} {metric_used}",
            reasoning=(
                f"Trained simplest possible model ({baseline_model}) with minimal preprocessing "
                f"to establish a performance floor. Dummy score: {dummy_score:.4f}. "
                f"Baseline lifts {lift:.1f}% over naive baseline. "
                f"Every subsequent step is measured against {baseline_score:.4f}."
            ),
            code_executed=code[:300],
            result_summary=f"baseline={baseline_score:.4f}, dummy={dummy_score:.4f}, n_samples={n_samples}",
        )

        mlflow.log_metric("baseline_score", baseline_score)
        mlflow.log_metric("dummy_score", dummy_score)
        mlflow.log_params({"baseline_model": baseline_model, "n_features": n_features})

        await self._update_run_field(run_id, baseline_score=baseline_score)

        await self.emit(
            run_id,
            f"Baseline: {baseline_score:.4f} ({metric_used}). Dummy: {dummy_score:.4f}. Lift: +{lift:.1f}%",
            {"baseline_score": baseline_score, "dummy_score": dummy_score},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        new_cell = {
            "agent": self.name,
            "title": "Baseline Model Benchmark",
            "iteration": 0,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "baseline_model": baseline_model,
                "baseline_score": round(baseline_score, 4),
                "dummy_score": round(dummy_score, 4),
                "lift_over_dummy_pct": round(lift, 1),
                "metric_used": metric_used,
                "n_samples": n_samples,
                "n_features": n_features,
                "error_rate_on_train": round(data.get("error_rate_on_train", 0) or 0, 4),
            },
        }
        return {
            "baseline_score": baseline_score,
            "baseline_model": baseline_model,
            "baseline_errors": {
                "error_rate_on_train": data.get("error_rate_on_train"),
                "baseline_path": data.get("baseline_path"),
            },
            "current_score": baseline_score,
            "prev_score": 0.0,
            "iteration": 0,
            "max_iterations": state.get("max_iterations", 3),
            "iteration_scores": [baseline_score],
            "decision_log": existing_log + [entry],
            "notebook_cells": existing_cells + [new_cell],
        }
