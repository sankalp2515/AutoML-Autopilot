from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState
from app.core import metric_registry
from app.core import model_registry as registry

TUNING_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import optuna
import os
import warnings
warnings.filterwarnings("ignore")
optuna.logging.set_verbosity(optuna.logging.WARNING)

from sklearn.model_selection import cross_val_score, StratifiedKFold, KFold
from sklearn.multioutput import MultiOutputClassifier
import xgboost as xgb
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.ensemble import (RandomForestClassifier, RandomForestRegressor,
                               GradientBoostingClassifier, GradientBoostingRegressor,
                               HistGradientBoostingClassifier, HistGradientBoostingRegressor)
# Optional estimators (torch / discovery-installed) — guarded like model_selector.
try:
    from automl_dl import TorchMLPClassifier, TorchMLPRegressor
except Exception:
    pass
try:
    from catboost import CatBoostClassifier, CatBoostRegressor
except Exception:
    pass
try:
    from lightgbm import LGBMClassifier, LGBMRegressor
except Exception:
    pass

enriched_path = __ENRICHED_PATH__
df = pd.read_csv(enriched_path)

task_type = __TASK_TYPE__
primary_metric = __PRIMARY_METRIC__
winner_class = __WINNER_CLASS__
n_trials = __N_TRIALS__
imbalance_strategy = __IMBALANCE_STRATEGY__


def _make_resampler():
    # Reconstruct the in-fold resampler for SMOTE/SMOTE-Tomek (single-label only).
    if (task_type in ("binary_classification", "multiclass_classification")
            and imbalance_strategy in ("smote", "smote_tomek")):
        try:
            from imblearn.over_sampling import SMOTE
            from imblearn.combine import SMOTETomek
            import pandas as _pd
            minority = int(_pd.Series(y).value_counts().min())
            if minority >= 6:
                return (SMOTE(k_neighbors=min(5, minority - 1), random_state=42)
                        if imbalance_strategy == "smote" else SMOTETomek(random_state=42))
        except Exception:
            pass
    return None


def _cv_estimator(model):
    rs = _make_resampler()
    if rs is not None:
        from imblearn.pipeline import Pipeline as ImbPipeline
        return ImbPipeline([("resample", rs), ("model", model)])
    return model

# Parse multilabel target (stored as JSON strings in enriched.csv)
if task_type == "multilabel_classification":
    import ast
    y_raw = df["__target__"].apply(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)
    y = np.array(y_raw.tolist())
else:
    y = df["__target__"].values
X = df.drop(columns=["__target__"])

# Scorer resolution EMITTED by metric_registry.scorer_prelude (single source
# of truth). No metric vocabulary lives in this template.
__SCORER_PRELUDE__

# `scoring` is a scorer OBJECT for the at-k metrics — guard the string op.
# Only neg_* sklearn scorers are lower-is-better; a scorer object never is.
higher_is_better = not (isinstance(scoring, str) and scoring.startswith("neg_"))

if task_type != "regression" and task_type != "multilabel_classification":
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
else:
    cv = KFold(n_splits=5, shuffle=True, random_state=42)

# GPU detection — XGBoost trials run on CUDA when the GPU is reserved
import shutil as _shutil
GPU_AVAILABLE = _shutil.which("nvidia-smi") is not None
_xgb_device = {"device": "cuda", "tree_method": "hist"} if GPU_AVAILABLE else {}


# Search space comes from the model registry (the source) — NOT hardcoded here.
search_space = __SEARCH_SPACE__


def _suggest(trial):
    """Build a params dict generically from the registry-supplied search space."""
    p = {}
    for name, spec in search_space.items():
        t = spec["type"]
        if t == "int":
            p[name] = trial.suggest_int(name, spec["low"], spec["high"])
        elif t == "float":
            p[name] = trial.suggest_float(name, spec["low"], spec["high"], log=spec.get("log", False))
        elif t == "categorical":
            p[name] = trial.suggest_categorical(name, spec["choices"])
    return p


def _build(params):
    """Instantiate the concrete winner class with tuned params + fixed per-family kwargs."""
    reg = task_type == "regression"
    if "XGB" in winner_class:
        base = (xgb.XGBRegressor if reg else xgb.XGBClassifier)(**params, **_xgb_device, random_state=42, verbosity=0)
    elif "HistGradientBoosting" in winner_class:
        base = (HistGradientBoostingRegressor if reg else HistGradientBoostingClassifier)(**params, random_state=42)
    elif "GradientBoosting" in winner_class:
        base = (GradientBoostingRegressor if reg else GradientBoostingClassifier)(**params, random_state=42)
    elif "RandomForest" in winner_class:
        base = (RandomForestRegressor if reg else RandomForestClassifier)(**params, random_state=42, n_jobs=1)
    elif "LogisticRegression" in winner_class:
        base = LogisticRegression(**params, solver="liblinear", max_iter=1000, random_state=42)
    elif "Ridge" in winner_class:
        base = Ridge(**params)
    else:
        base = joblib.load(__WINNER_PATH__)
    return MultiOutputClassifier(base, n_jobs=1) if task_type == "multilabel_classification" else base


def objective(trial):
    if not search_space:
        return 0.0
    model = _build(_suggest(trial))
    scores = cross_val_score(_cv_estimator(model), X, y, cv=cv, scoring=scoring, n_jobs=1)
    return float(scores.mean()) if higher_is_better else float(-scores.mean())


study = optuna.create_study(
    direction="maximize",
    sampler=optuna.samplers.TPESampler(seed=42),
)
study.optimize(objective, n_trials=n_trials, timeout=300)

best_params = study.best_params
best_value = study.best_value
if not higher_is_better:
    best_value = -best_value

# Re-train tuned model on full data — same generic builder used in the objective
# (_build already wraps multilabel in MultiOutputClassifier).
tuned_model = _build(best_params)

# Final fit: when SMOTE is active, train the deployed model on resampled FULL data
# so it learned from a balanced set. The inference pipeline still has NO resampler.
_final_rs = _make_resampler()
if _final_rs is not None:
    X_fit, y_fit = _final_rs.fit_resample(X, y)
    tuned_model.fit(X_fit, y_fit)
else:
    tuned_model.fit(X, y)

os.makedirs(artifacts_dir, exist_ok=True)
tuned_path = os.path.join(artifacts_dir, "tuned_model.pkl")
joblib.dump(tuned_model, tuned_path)

RESULT = {
    "best_params": best_params,
    "tuned_score": best_value,
    "n_trials_completed": len(study.trials),
    "tuned_path": tuned_path,
}
'''


class TunerAgent(BaseAgent):
    name = "tuner"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")

        winner = state.get("winner_model", "XGBoost")

        # Streaming winner (S2): the out-of-core SGD is already fit on ALL rows and
        # isn't Optuna-tunable in this template — pass it straight to evaluation as
        # the tuned model (no tuning step), keeping its all-rows score.
        if state.get("streaming_winner"):
            await self.emit(run_id, "Winner is the out-of-core streaming model — skipping "
                                    "hyperparameter tuning (already fit on all rows).")
            entry = await self._log_decision(
                run_id=run_id,
                decision="Skipped tuning: streaming winner is not Optuna-tunable here",
                reasoning="The SGD candidate was trained on every row via partial_fit and saved; "
                          "there is no separate CV-tunable estimator to search over.",
                result_summary="tuning=skipped(streaming)",
            )
            await self._mark_step(run_id, "completed")
            return {
                "tuned_model_path": state.get("winner_model_path", ""),
                "best_hyperparams": {},
                "hpo_trials": 0,
                "current_score": state.get("current_score"),
                "prev_score": state.get("current_score", state.get("baseline_score", 0.0)),
                "decision_log": state.get("decision_log", []) + [entry],
            }

        await self.emit(run_id, f"Tuning {winner} with Optuna Bayesian search...")

        n_samples = state.get("data_audit", {}).get("shape", [0])[0]
        n_trials = 30 if n_samples < 50000 else 20

        # Token replacement (NOT .format) — this template is dict/f-string heavy,
        # so it uses natural single braces; values are injected via repr() like exporter.
        code = (
            TUNING_CODE_TEMPLATE
            .replace("__ENRICHED_PATH__", repr(state.get("enriched_data_path", "")))
            .replace("__TASK_TYPE__", repr(state["task_type"]))
            .replace("__PRIMARY_METRIC__", repr(state["primary_metric"]))
            .replace("__SCORER_PRELUDE__", metric_registry.scorer_prelude(
                state.get("primary_metric", ""), state["task_type"],
                k=float(state.get("rare_event_k") or metric_registry.DEFAULT_K)))
            .replace("__WINNER_CLASS__", repr(state.get("winner_model_class", "xgb.XGBClassifier")))
            .replace("__WINNER_PATH__", repr(state.get("winner_model_path", "")))
            .replace("__N_TRIALS__", repr(n_trials))
            .replace("__IMBALANCE_STRATEGY__", repr(state.get("imbalance_strategy", "none")))
            .replace("__SEARCH_SPACE__", repr(
                registry.search_space_for(state.get("winner_model_class", ""))))
        )

        result = await self.execute_code(code, run_id, timeout=400)
        result = await self.try_agentic_repair(
            run_id, code, result,
            task_type=state.get("task_type", "unknown"),
            result_keys=["tuned_score", "best_params", "n_trials_completed", "tuned_path"],
            goal=(f"Tune {winner} (class {state.get('winner_model_class','')}) on enriched.csv "
                  "(has a '__target__' column) via a small hyperparameter search, refit the best "
                  "model on all training data, save it to artifacts_dir/tuned_model.pkl. Set RESULT "
                  "with tuned_score (float), best_params (dict), n_trials_completed (int), tuned_path (str)."),
            timeout=400,
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"Tuning failed: {result['error']}", "status": "failed"}

        data = result["result"]
        tuned_score = data["tuned_score"]
        best_params = data["best_params"]

        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Hyperparameter tuning: {winner} tuned score = {tuned_score:.4f}",
            reasoning=(
                f"Optuna Bayesian search with {data['n_trials_completed']} trials. "
                f"Best params: {best_params}. "
                f"Improvement vs pre-tuning: {tuned_score - state.get('current_score', 0):.4f}"
            ),
            result_summary=f"tuned_score={tuned_score:.4f}",
        )

        from app.core import mlflow_tracker as mlflow
        iteration = state.get("iteration", 0)
        mlflow.log_metric("post_tuning_score", tuned_score, step=iteration)
        # Prefix with iteration number — MLflow forbids changing a param's value
        mlflow.log_params({f"i{iteration}_tuned_{k}": str(v) for k, v in best_params.items()})

        await self.emit(
            run_id,
            f"Tuning complete: {tuned_score:.4f} {state['primary_metric']} after {data['n_trials_completed']} trials",
            {"tuned_score": tuned_score, "best_params": best_params},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        iteration = state.get("iteration", 0)
        new_cell = {
            "agent": self.name,
            "title": f"Hyperparameter Tuning — Iteration {iteration + 1}",
            "iteration": iteration + 1,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "iteration": iteration + 1,
                "model_tuned": winner,
                "n_trials": data["n_trials_completed"],
                "tuned_score": round(tuned_score, 4),
                "pre_tuning_score": round(
                    state.get("current_score", state.get("baseline_score", 0.0)), 4
                ),
                "improvement": round(
                    tuned_score - state.get("current_score", state.get("baseline_score", 0.0)), 4
                ),
                "best_params": best_params,
            },
        }
        return {
            "tuned_score": tuned_score,
            "best_hyperparams": best_params,
            "hpo_trials": int(data.get("n_trials_completed") or 0),
            "tuned_model_path": data["tuned_path"],
            "current_score": tuned_score,
            "prev_score": state.get("current_score", state.get("baseline_score", 0.0)),
            "decision_log": existing_log + [entry],
            "notebook_cells": existing_cells + [new_cell],
        }
