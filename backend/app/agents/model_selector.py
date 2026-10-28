import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState
from app.core import model_registry as registry
from app.core import metric_registry

# The candidate menu is RETRIEVED from app/core/model_registry.py (the source) and
# injected per-run into the user message — not hardcoded here. Add a model = add a
# registry entry; the scout below picks from whatever is registered + installed.
SYSTEM_PROMPT = """You are an expert ML model scout.
From the AVAILABLE MODELS list provided in the user message, select 2-3 candidates
best suited to the data (NOT an exhaustive search). Use ONLY the exact class strings
from that list — never invent a class.

General heuristics:
- n_samples < 5000: prefer linear (LogisticRegression / Ridge) or HistGradientBoosting
- n_samples > 5000 / wide data: prefer XGBoost or HistGradientBoosting
- interpretability required: linear models only
- high class imbalance: prefer models marked [class_weight]
- multilabel: any classifier works (wrapped in MultiOutputClassifier automatically);
  MultiOutputClassifier(LogisticRegression) is a strong baseline
- include at least one boosting model unless interpretability is required

If a RECOMMENDED (not-installed) model would clearly suit this data better, name it in
"discovery_notes" with its pip package — but DO NOT select it (it cannot train until installed).

Respond with JSON:
{
  "selected_models": [
    {"name": "...", "class": "<exact class string from the list>",
     "reason": "<why>", "initial_params": {"...": "..."}}
  ],
  "discovery_notes": "<optional: a not-installed model worth adding, with pip package>",
  "decisions": [{"decision": "...", "reasoning": "..."}]
}
"""

TRAINING_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import json
import os
import time
import warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import cross_val_score, StratifiedKFold, KFold
from sklearn.linear_model import LogisticRegression, Ridge, Lasso
from sklearn.ensemble import (RandomForestClassifier, RandomForestRegressor,
                              GradientBoostingClassifier, GradientBoostingRegressor,
                              HistGradientBoostingClassifier, HistGradientBoostingRegressor)
from sklearn.svm import SVC
from sklearn.multioutput import MultiOutputClassifier
import xgboost as xgb

# Deep-learning estimators (baked into the sandbox image). Guarded so the template
# still works before torch is added; the scout only selects these when installed.
try:
    from automl_dl import TorchMLPClassifier, TorchMLPRegressor
except Exception:
    pass
# Discovery-installed models (image rebuild via scripts/apply_discovery.ps1).
# Guarded: the scout only selects these once the installed-marker verifies them.
try:
    from catboost import CatBoostClassifier, CatBoostRegressor
except Exception:
    pass
try:
    from lightgbm import LGBMClassifier, LGBMRegressor
except Exception:
    pass

enriched_path = "{enriched_path}"
df = pd.read_csv(enriched_path)

# For multilabel, target is stored as JSON strings; parse them
task_type = "{task_type}"
if task_type == "multilabel_classification":
    import ast
    y_raw = df["__target__"].apply(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)
    y = np.array(y_raw.tolist())
else:
    y = df["__target__"].values
X = df.drop(columns=["__target__"])

primary_metric = "{primary_metric}"
class_imbalance = {class_imbalance}
label_columns = {label_columns}
label_delimiter = "{label_delimiter}"
imbalance_strategy = "{imbalance_strategy}"


def _cv_estimator(model):
    # In-fold SMOTE/SMOTE-Tomek for single-label classification: resampling happens
    # ONLY on each training fold (imblearn Pipeline), never the validation fold — no leakage.
    if (task_type in ("binary_classification", "multiclass_classification")
            and imbalance_strategy in ("smote", "smote_tomek")):
        try:
            from imblearn.over_sampling import SMOTE
            from imblearn.combine import SMOTETomek
            from imblearn.pipeline import Pipeline as ImbPipeline
            minority = int(pd.Series(y).value_counts().min())
            if minority >= 6:
                rs = (SMOTE(k_neighbors=min(5, minority - 1), random_state=42)
                      if imbalance_strategy == "smote" else SMOTETomek(random_state=42))
                return ImbPipeline([("resample", rs), ("model", model)])
        except Exception:
            pass
    return model

# Scorer resolution is EMITTED by app.core.metric_registry.scorer_prelude — the
# single source of truth. This template holds no metric vocabulary of its own
# (three hand-copied scoring_maps used to live here, in tuner and in
# feature_engineer; that duplication is the direction-bug family's shape).
{scorer_prelude}

if task_type != "regression" and task_type != "multilabel_classification":
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
else:
    cv = KFold(n_splits=5, shuffle=True, random_state=42)

# ── Cost-sensitive sample weights (Phase 3.5) ────────────────────────────────
# Mirrors app.core.business_objective.sample_weights_from_objective; the sandbox
# is a separate container and cannot import it. Weighting each row by the COST of
# getting it wrong makes any weight-aware learner cost-sensitive without touching
# its loss function. Normalized to mean 1.0 so regularization strengths tuned on
# an unweighted run stay in the same ballpark.
BUSINESS_OBJECTIVE = {business_objective}
COST_WEIGHTS = None
if BUSINESS_OBJECTIVE and task_type == "binary_classification":
    _wp = float(BUSINESS_OBJECTIVE.get("value_tp", 0)) + float(BUSINESS_OBJECTIVE.get("cost_fn", 0))
    _wn = float(BUSINESS_OBJECTIVE.get("cost_fp", 0)) + float(BUSINESS_OBJECTIVE.get("value_tn", 0))
    if _wp > 0 or _wn > 0:
        _yb = (np.asarray(y).ravel() > 0).astype(int)
        _w = np.where(_yb == 1, max(_wp, 1e-9), max(_wn, 1e-9)).astype(float)
        _m = _w.mean()
        COST_WEIGHTS = (_w / _m) if _m > 0 else None

models_config = {models_config}
# When False (parallel scouting mode) this call ONLY cross-validates the given
# candidate(s) — the winner is decided and fitted in a separate, final call.
fit_winner = {fit_winner}

# GPU detection — XGBoost 2.x uses device="cuda" (pip wheels ship CUDA support).
# nvidia-smi is mounted into the container by the NVIDIA runtime when a GPU
# is reserved in docker-compose. sklearn models are CPU-only regardless.
import shutil as _shutil
GPU_AVAILABLE = _shutil.which("nvidia-smi") is not None

results = {{}}
for config in models_config:
    name = config["name"]
    params = config.get("initial_params", {{}})

    # COST-SENSITIVE LEARNING (Phase 3.5) takes precedence over class_weight.
    # class_weight="balanced" encodes "the rare class matters in proportion to its
    # rarity" — a statistical statement. A business objective encodes what the
    # mistakes actually COST, which is the thing we are optimizing. Applying both
    # would double-count the minority class, so it is one or the other.
    if COST_WEIGHTS is not None and task_type == "binary_classification":
        pass                        # weights are passed to fit() via _fit_kwargs below
    elif task_type != "regression" and task_type != "multilabel_classification" and class_imbalance:
        if "class_weight" in __import__("inspect").signature(
            eval(config["class"])
        ).parameters:
            params["class_weight"] = "balanced"

    # Route XGBoost to GPU when available
    if "XGB" in config["class"] and GPU_AVAILABLE:
        params["device"] = "cuda"
        params["tree_method"] = "hist"

    # Entity embeddings (DL-2a) need the ordinal-coded column indices — a
    # MECHANIC derived from the processed feature names, not an LLM decision.
    if "TorchEmbed" in config["class"]:
        params["cat_idx"] = [i for i, c in enumerate(X.columns)
                             if str(c).startswith("cat_ordinal__")]
        if not params["cat_idx"]:
            results[name] = {{"error": "no ordinal-encoded categorical columns for embeddings",
                              "cv_score": -999}}
            continue

    try:
        base_model = eval(config["class"])(**params)
        
        # Wrap in MultiOutputClassifier for multilabel
        if task_type == "multilabel_classification":
            model = MultiOutputClassifier(base_model, n_jobs=1)
        else:
            model = base_model
            
        t0 = time.time()
        # sample_weight must reach the ESTIMATOR inside any pipeline wrapper.
        _fit_params = {{}}
        if COST_WEIGHTS is not None:
            _est = _cv_estimator(model)
            _key = "model__sample_weight" if hasattr(_est, "steps") else "sample_weight"
            _fit_params = {{_key: COST_WEIGHTS}}
        try:
            scores = cross_val_score(_cv_estimator(model), X, y, cv=cv, scoring=scoring,
                                     n_jobs=1, params=_fit_params or None)
        except TypeError:
            # sklearn < 1.4 spells it fit_params; older still ignores it entirely.
            try:
                scores = cross_val_score(_cv_estimator(model), X, y, cv=cv, scoring=scoring,
                                         n_jobs=1, fit_params=_fit_params or None)
            except TypeError:
                scores = cross_val_score(_cv_estimator(model), X, y, cv=cv, scoring=scoring, n_jobs=1)
        elapsed = time.time() - t0

        if task_type == "regression" and isinstance(scoring, str) and scoring.startswith("neg_"):
            score = float(-scores.mean())
        else:
            score = float(scores.mean())

        # NaN means the scorer is incompatible with this task — sklearn
        # swallows the scorer error and returns error_score=nan per fold.
        if np.isnan(score):
            raise ValueError(
                f"scoring '{{scoring}}' produced NaN for all folds — "
                f"metric incompatible with task '{{task_type}}'"
            )

        results[name] = {{
            "cv_score": score,
            "cv_std": float(scores.std()),
            "training_time_s": round(elapsed, 2),
            "class": config["class"],
            "params": params,
        }}
    except Exception as e:
        results[name] = {{"error": str(e), "cv_score": -999}}

# Select winner
valid = {{k: v for k, v in results.items() if "error" not in v}}
if not valid:
    error_details = "; ".join(f"{{k}}: {{v.get('error', '?')[:120]}}" for k, v in results.items())
    raise RuntimeError(f"All model candidates failed — {{error_details}}")
# Winner direction MUST mirror how the score was STORED above: the value is
# flipped to a positive error ONLY for regression + a neg_* scorer, so that is
# the one and only "lower is better" case. Assuming "regression ⇒ lower better"
# silently picked the WORST model whenever the metric was r2 (2026-07-04 fix).
lower_is_better = (task_type == "regression"
                   and isinstance(scoring, str) and scoring.startswith("neg_"))
if lower_is_better:
    winner = min(valid, key=lambda k: valid[k]["cv_score"])
else:
    winner = max(valid, key=lambda k: valid[k]["cv_score"])

if not fit_winner:
    # Parallel scouting mode: report CV results only; no full-data fit.
    RESULT = {{
        "models_results": results,
        "winner": winner,
        "winner_score": valid[winner]["cv_score"],
        "winner_path": "",
        "winner_class": valid[winner]["class"],
        "winner_params": valid[winner]["params"],
        "gpu_used": GPU_AVAILABLE,
    }}
else:
    # Train winner on full data and save
    winner_config = next(c for c in models_config if c["name"] == winner)
    winner_params = valid[winner]["params"]
    if task_type != "regression" and task_type != "multilabel_classification" and class_imbalance:
        winner_params["class_weight"] = "balanced"

    base_model = eval(winner_config["class"])(**winner_params)
    if task_type == "multilabel_classification":
        winner_model = MultiOutputClassifier(base_model, n_jobs=1)
    else:
        winner_model = base_model
    winner_model.fit(X, y)

    os.makedirs(artifacts_dir, exist_ok=True)
    winner_path = os.path.join(artifacts_dir, "winner_model.pkl")
    joblib.dump(winner_model, winner_path)

    RESULT = {{
        "models_results": results,
        "winner": winner,
        "winner_score": valid[winner]["cv_score"],
        "winner_path": winner_path,
        "winner_class": winner_config["class"],
        "winner_params": winner_params,
        "gpu_used": GPU_AVAILABLE,
    }}
'''


# S2 — out-of-core candidate: chunked partial_fit over the UNCAPPED train file.
# Every row is learned from in bounded memory (linear model; the trade for scale).
# v2 scorer parity: a held-out VALIDATION portion of the stream is scored with the
# SAME primary-metric sklearn scorer the in-memory candidates use (via sklearn's
# get_scorer), so the streaming score is directly comparable and CAN win.
STREAMING_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import os
import warnings
warnings.filterwarnings("ignore")
from sklearn.linear_model import SGDClassifier, SGDRegressor
from sklearn.metrics import get_scorer

train_full_path = __TRAIN_FULL_PATH__
preprocessor_path = __PREPROCESSOR_PATH__
target_col = __TARGET_COL__
task_type = __TASK_TYPE__
target_classes = __TARGET_CLASSES__
scorer_name = __SCORER_NAME__     # same scorer id as the CV candidates
__SCORER_PRELUDE__               # defines `scoring` (str, or a make_scorer object)
val_frac = 0.15

pre = joblib.load(preprocessor_path)
is_reg = task_type == "regression"
# NB: class_weight="balanced" is NOT supported by SGD.partial_fit (sklearn raises).
# Imbalance in the streaming path is handled per-chunk via sample_weight below.
model = (SGDRegressor(random_state=42) if is_reg
         else SGDClassifier(loss="log_loss", random_state=42))

# Class universe for partial_fit — derived from the ACTUAL target column via a
# cheap one-column scan, robust whether the target is string-labeled OR already
# numeric-encoded. (The preprocessor leaves target_classes EMPTY for an already
# 0/1 numeric target, which previously mapped every row to NaN and dropped the
# whole chunk — the never-run streaming path's real bug.)
if not is_reg:
    _tcol = pd.read_csv(train_full_path, usecols=[target_col])[target_col].dropna()
    if target_classes:
        cls_index = {str(c): i for i, c in enumerate(target_classes)}
        def _map_y(s):
            return s.astype(str).map(cls_index)
    else:
        _uniq = sorted(_tcol.unique().tolist(), key=lambda v: str(v))
        cls_index = {v: i for i, v in enumerate(_uniq)}
        def _map_y(s):
            return s.map(cls_index)
    classes = np.arange(len(cls_index))
else:
    classes = None
    def _map_y(s):
        return pd.to_numeric(s, errors="coerce")

def _prep(chunk):
    chunk = chunk[chunk[target_col].notna()]
    if not len(chunk):
        return None, None
    yi = _map_y(chunk[target_col])
    keep = yi.notna()
    chunk, yi = chunk[keep], yi[keep]
    if not is_reg:
        yi = yi.astype(int)
    X = chunk.drop(columns=[target_col])
    for c in X.columns:
        if X[c].dtype == object:
            X[c] = X[c].fillna("")
    return pre.transform(X), yi.values

# Reserve a validation slice up front (a random fraction of each chunk) so the
# score is measured on rows the streaming model NEVER trained on — parity with CV.
rng = np.random.RandomState(42)
n_seen = 0
Xv_parts, yv_parts = [], []
for chunk in pd.read_csv(train_full_path, chunksize=100_000):
    Xt, y = _prep(chunk)
    if Xt is None:
        continue
    mask = rng.rand(len(y)) < val_frac
    if mask.any():
        Xv_parts.append(Xt[mask]); yv_parts.append(y[mask])
    Xt_tr, y_tr = Xt[~mask], y[~mask]
    if len(y_tr) == 0:
        continue
    if is_reg:
        model.partial_fit(Xt_tr, y_tr)
    else:
        # Per-chunk balanced sample weights replace the unsupported
        # class_weight="balanced" (partial_fit rejects it): weight each sample
        # inversely to its class frequency IN THIS CHUNK.
        _cls, _cnt = np.unique(y_tr, return_counts=True)
        _w = {c: len(y_tr) / (len(_cls) * n) for c, n in zip(_cls, _cnt)}
        sw = np.array([_w[v] for v in y_tr])
        model.partial_fit(Xt_tr, y_tr, classes=classes, sample_weight=sw)
    n_seen += len(y_tr)

from scipy import sparse as _sp
Xv = _sp.vstack(Xv_parts) if (Xv_parts and _sp.issparse(Xv_parts[0])) else np.vstack(Xv_parts)
yv = np.concatenate(yv_parts)

# Score with the SAME sklearn scorer the CV candidates used (higher = better in
# sklearn scorer convention; neg_* handled by get_scorer). This is the parity fix.
try:
    # `scoring` is a scorer OBJECT for the at-k metrics and a string otherwise;
    # get_scorer() would raise on the at-k sentinel ids, and the old fallback
    # silently scored f1_weighted instead — a different metric than the CV
    # candidates were ranked by, which would let a streaming model be crowned
    # on a number that is not comparable. Never fall back to another metric.
    scorer = get_scorer(scoring) if isinstance(scoring, str) else scoring
    score = float(scorer(model, Xv, yv))
except Exception as _se:
    score = None
    SCORER_ERROR = str(_se)[:300]

os.makedirs(artifacts_dir, exist_ok=True)
path = os.path.join(artifacts_dir, "streaming_model.pkl")
joblib.dump(model, path)
RESULT = {"score": score, "scorer": scorer_name, "n_rows_seen": int(n_seen),
          "n_val": int(len(yv)), "model_path": path}
'''


class ModelSelectorAgent(BaseAgent):
    name = "model_selector"

    async def _streaming_candidate(self, run_id: str, state) -> dict | None:
        """S2 — train the out-of-core candidate on EVERY row (chunked partial_fit),
        scored with the SAME primary-metric scorer as the CV candidates (v2 parity)
        so it can be crowned when it wins. Returns a models_results-style entry, or
        None (failure = skip, in-memory candidates carry the run)."""
        full = state.get("train_full_path")
        if not full:
            return None
        task_type = state.get("task_type", "")
        scorer_name = metric_registry.sklearn_scorer(state.get("primary_metric", ""), task_type)
        code = (
            STREAMING_CODE_TEMPLATE
            .replace("__TRAIN_FULL_PATH__", repr(full))
            .replace("__PREPROCESSOR_PATH__", repr(state.get("preprocessor_path", "")))
            .replace("__TARGET_COL__", repr(state.get("target_column", "")))
            .replace("__TASK_TYPE__", repr(task_type))
            .replace("__TARGET_CLASSES__", repr(state.get("target_classes") or []))
            .replace("__SCORER_NAME__", repr(scorer_name))
            .replace("__SCORER_PRELUDE__", metric_registry.scorer_prelude(
                state.get("primary_metric", ""), task_type,
                k=float(state.get("rare_event_k") or metric_registry.DEFAULT_K)))
        )
        # Sandbox hard-caps per-exec timeout at 600s (returns 400 above it) — the
        # never-run streaming path originally requested 900 and 400'd. Chunked
        # partial_fit over all rows is fast (linear model), so 600 is ample.
        result = await self.execute_code(code, run_id, timeout=600)
        data = self.result_dict(result)
        if not result.get("success") or not isinstance(data.get("score"), (int, float)):
            self._log.warning("streaming_candidate_failed", run_id=run_id,
                              error=str(result.get("error", ""))[:200])
            return None
        # sklearn scorers are higher-is-better (neg_* for errors); store the RAW
        # metric so it displays like the CV scores (undo the neg_ sign).
        raw = float(data["score"])
        display = -raw if scorer_name.startswith("neg_") else raw
        return {"cv_score": round(display, 4), "cv_std": 0.0,
                "training_time_s": 0.0, "class": "SGD(streaming)",
                "params": {}, "streaming": True,
                "scorer_value": raw,   # higher-is-better basis for winner comparison
                "n_rows_seen": data.get("n_rows_seen"),
                "n_val": data.get("n_val"),
                "model_path": data.get("model_path")}

    async def _parallel_candidate_cv(self, run_id: str, render, selected_models: list[dict],
                                     state: AgentState | None = None):
        """P2 #9 — cross-validate each candidate in its own concurrent sandbox call,
        then fit ONLY the winner on full data in a final call. Returns a result dict
        shaped like the serial path's, or None to fall back to the serial path."""
        import asyncio
        try:
            calls = [
                self.execute_code(render({"models_config": [m]}, fit_winner=False),
                                  run_id, timeout=600)
                for m in selected_models
            ]
            outs = await asyncio.gather(*calls, return_exceptions=True)

            merged: dict[str, dict] = {}
            for m, out in zip(selected_models, outs):
                name = m.get("name", m.get("class", "?"))
                if isinstance(out, Exception):
                    merged[name] = {"error": str(out)[:200], "cv_score": -999}
                    continue
                data = self.result_dict(out)
                if out.get("success") and isinstance(data.get("models_results"), dict):
                    merged.update(data["models_results"])
                else:
                    merged[name] = {"error": str(out.get("error", "no result"))[:200],
                                    "cv_score": -999}

            valid = {k: v for k, v in merged.items()
                     if isinstance(v, dict) and "error" not in v
                     and isinstance(v.get("cv_score"), (int, float))}
            if not valid:
                self._log.warning("parallel_cv_no_valid_fallback_serial", run_id=run_id)
                return None

            # Winner rule mirrors the template EXACTLY: the score is stored as a
            # positive error only for regression + a neg_* scorer, so that is the
            # only lower-is-better case. (The old class-string sniffing treated
            # ALL regression as lower-better and picked the WORST model on r2.)
            from app.core import metric_registry as _mreg
            _st = state or {}
            lower_is_better = _mreg.stored_score_lower_is_better(
                _st.get("primary_metric", ""), _st.get("task_type", ""))
            pick = min if lower_is_better else max
            winner = pick(valid, key=lambda k: valid[k]["cv_score"])
            winner_cfg = next((m for m in selected_models if m.get("name") == winner), None)
            if winner_cfg is None:
                return None

            fit = await self.execute_code(
                render({"models_config": [winner_cfg]}, fit_winner=True), run_id, timeout=600)
            fit_data = self.result_dict(fit)
            if not fit.get("success") or not fit_data.get("winner_path"):
                self._log.warning("parallel_winner_fit_failed_fallback_serial", run_id=run_id)
                return None

            fit_data["models_results"] = merged        # full comparison for the UI
            fit_data["winner"] = winner
            fit_data["winner_score"] = valid[winner]["cv_score"]
            return {"success": True, "result": fit_data,
                    "final_params": {"models_config": selected_models},
                    "stdout": str(fit.get("stdout", ""))}
        except Exception as e:
            self._log.warning("parallel_cv_error_fallback_serial", run_id=run_id, error=str(e)[:200])
            return None

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Selecting 2-3 model candidates based on data characteristics...")

        audit = state.get("data_audit", {})
        eda = state.get("eda_insights", {})
        n_samples = audit.get("shape", [0])[0]
        class_imbalance = eda.get("class_imbalance", {})
        task_type = state["task_type"]

        # RETRIEVE the candidate menu from the registry (the source) for this task.
        menu = registry.menu_text(task_type)
        recs = registry.recommendations_text(task_type)
        allowed_classes = {
            registry.class_for(s, task_type)
            for s in registry.catalog_for(task_type, installed_only=True)
        }

        user_message = f"""
Task: {task_type}
Primary metric: {state['primary_metric']}
n_samples: {n_samples}
n_features (after preprocessing): {audit.get('shape', [0, 0])[1]}
Class imbalance: {json.dumps(class_imbalance)}
Interpretability required: {state.get('interpretability_required', False)}
Current baseline score: {state.get('current_score')} ({state.get('primary_metric')})

AVAILABLE MODELS (choose 2-3; use the exact class string):
{menu}
{("RECOMMENDED (not installed — advisory only, do NOT select):" + chr(10) + recs) if recs else ""}

Select 2-3 model candidates with initial parameters.
"""
        # Phase 3 — cross-run memory: prior winners on this dataset are evidence
        # (the pick is still validated by measured CV, so a stale hint can't win
        # without earning it).
        try:
            from app.core import run_memory
            mem = run_memory.load_memory(
                run_memory.signature_for_run(run_id, state.get("dataset_path", "")))
            wins = mem.get("winning_models") or {}
            if wins:
                top = sorted(wins.items(), key=lambda kv: -kv[1])[:5]
                user_message += ("\nPrior runs on this dataset — models that won CV before: "
                                 + ", ".join(f"{m} (x{n})" for m, n in top)
                                 + ". Consider including the repeat winner among the candidates.\n")
        except Exception:
            pass
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)
        selected_models = self.norm_dicts(response.get("selected_models") if isinstance(response, dict) else None)

        # Validate against the registry — drop any class the LLM invented (eval-safety),
        # and fall back to the registry's default candidates if nothing valid remains.
        selected_models = [m for m in selected_models if m.get("class") in allowed_classes]
        if not selected_models:
            fallback = registry.catalog_for(task_type, installed_only=True)[:2]
            selected_models = [
                {"name": s["name"], "class": registry.class_for(s, task_type),
                 "reason": "registry default", "initial_params": {}}
                for s in fallback
            ]

        discovery_notes = response.get("discovery_notes", "")
        if discovery_notes:
            await self.emit(run_id, f"Scout note: {discovery_notes[:160]}", {"discovery": True})
            # Discovery Agent (ask-mode): turn the note into a vetted, pending
            # proposal the user can approve — never an automatic install.
            try:
                from app.core import discovery
                proposal = discovery.record_proposal(str(discovery_notes), run_id,
                                                     state.get("task_type", ""))
                if proposal and proposal.get("status") == "pending":
                    await self.emit(
                        run_id,
                        f"Discovery proposal staged: {proposal['pinned']} — approve in "
                        f"Settings → Discovery (or POST /api/v1/discovery/{proposal['package']}/approve).",
                        {"discovery_proposal": proposal["package"]},
                    )
            except Exception:
                pass

        def render(p: dict, fit_winner: bool = True) -> str:
            return TRAINING_CODE_TEMPLATE.format(
                enriched_path=state.get("enriched_data_path", ""),
                task_type=state["task_type"],
                primary_metric=state["primary_metric"],
                class_imbalance=repr(bool(class_imbalance and class_imbalance.get("imbalanced"))),
                models_config=repr(p["models_config"]),
                fit_winner=repr(fit_winner),
                label_columns=repr(state.get("label_columns", [])),
                label_delimiter=state.get("label_delimiter", ""),
                imbalance_strategy=state.get("imbalance_strategy", "none"),
                business_objective=repr(state.get("business_objective") or {}),
                scorer_prelude=metric_registry.scorer_prelude(
                    state.get("primary_metric", ""), state["task_type"],
                    k=float(state.get("rare_event_k") or metric_registry.DEFAULT_K),
                ),
            )

        # P2 #9 — parallel candidate scouting: with PARALLEL_CANDIDATES on and 2+
        # candidates, cross-validate each candidate in its OWN sandbox call
        # concurrently (the sandbox spawns one process per request), then fit only
        # the winner. This is asyncio concurrency, NOT Ray. Any failure falls back
        # to the proven serial path below.
        result: dict | None = None
        from app.config import settings as _settings
        # Back-compat: honor a legacy USE_RAY=false as disabling candidate parallelism.
        _parallel_on = getattr(_settings, "PARALLEL_CANDIDATES", True) and _settings.USE_RAY
        if _parallel_on and len(selected_models) > 1:
            result = await self._parallel_candidate_cv(run_id, render, selected_models, state)
            if result is not None:
                await self.emit(run_id, f"Scouted {len(selected_models)} candidates in parallel sandbox calls.")

        if result is None:
            result = await self.execute_code_with_repair(
                run_id, render, {"models_config": selected_models},
                repair_goal=(
                    "Train & cross-validate candidate models. models_config is a list of "
                    '{"name", "class", "initial_params"} where class is one of: '
                    "xgb.XGBClassifier, RandomForestClassifier, GradientBoostingClassifier, "
                    "LogisticRegression (or the *Regressor / Ridge variants). Fix errors by "
                    "removing a model that errored, correcting invalid initial_params, or "
                    "swapping to a compatible model for this task."
                ),
                timeout=600,
            )
        if not result["success"]:
            # Tier-1 (param revision) exhausted → let the agent WRITE its own fix.
            result = await self.try_agentic_repair(
                run_id, render({"models_config": selected_models}), result,
                task_type=state.get("task_type", "unknown"),
                result_keys=["winner", "winner_score", "models_results", "winner_path", "winner_class", "winner_params"],
                goal=("From enriched.csv (has a '__target__' column), train & cross-validate the "
                      "candidate models, pick the best by CV score, save it to "
                      "artifacts_dir/winner_model.pkl. Set RESULT with winner (str), winner_score "
                      "(float), models_results ({name: {cv_score, cv_std, training_time_s}}), "
                      "winner_path (str), winner_class (str)."),
                timeout=600,
            )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"Model training failed: {result['error']}", "status": "failed"}

        # Reflect any repaired candidate list in the decisions we log
        selected_models = result.get("final_params", {}).get("models_config", selected_models)
        code = render({"models_config": selected_models})  # exact code that ran (post-repair)
        data = result["result"]
        winner = data["winner"]
        winner_score = data["winner_score"]
        # NaN serializes to null over the sandbox HTTP boundary — guard so a
        # bad score fails this agent cleanly instead of crashing the pipeline.
        if winner_score is None or winner_score != winner_score:
            error_msg = f"Model selection produced invalid score for '{winner}' (metric incompatible with task?)"
            await self._mark_step(run_id, "failed", error_msg)
            return {"error": error_msg, "status": "failed"}

        # S2 — streaming candidate: when the scale plan says "streaming", train an
        # out-of-core learner on EVERY row and enter it into winner selection with
        # a SCORER-PARITY score (v2). If it beats the sampled winner on the same
        # metric, it is CROWNED — its saved model (fit on all rows) becomes the
        # winner. This is the honest scale path: seeing every row can now win.
        if state.get("scale_strategy") == "streaming":
            stream = await self._streaming_candidate(run_id, state)
            if stream:
                data.setdefault("models_results", {})["SGD_Streaming_AllRows"] = stream
                hib = metric_registry.higher_is_better(state.get("primary_metric", ""))
                # Common higher-is-better axis for a fair comparison.
                stream_axis = stream["scorer_value"]              # already higher-better (neg_* for errors)
                winner_axis = winner_score if hib else -winner_score
                # Compatibility gate: the streaming SGD trained on the PREPROCESSED
                # feature space only. If the run engineered extra features (which
                # the in-memory candidates trained on and the evaluator reproduces),
                # crowning it would mismatch feature counts downstream — keep it a
                # benchmark in that case. No engineered features → safe to crown.
                has_engineered = bool(state.get("features_created"))
                promoted = (stream_axis > winner_axis) and not has_engineered
                blocked_by_features = (stream_axis > winner_axis) and has_engineered
                await self._log_decision(
                    run_id=run_id,
                    decision=(f"Streaming candidate: SGD saw ALL {stream.get('n_rows_seen'):,} rows "
                              f"(chunked partial_fit), {state['primary_metric']}={stream['cv_score']:.4f} "
                              f"on {stream.get('n_val'):,} held-out rows"
                              + (" — WINS, crowned as the model" if promoted else
                                 " — WINS on score but the run has engineered features it doesn't reproduce; kept as benchmark"
                                 if blocked_by_features else " — did not beat the sampled winner")),
                    reasoning="Out-of-core training learns from every row in bounded memory. Scored "
                              "with the SAME primary-metric scorer as the sampled candidates on rows it "
                              "never trained on, so the comparison is apples-to-apples (v2 parity).",
                    result_summary=f"streaming_score={stream['cv_score']:.4f}, promoted={promoted}",
                )
                if promoted:
                    winner = "SGD_Streaming_AllRows"
                    winner_score = stream["cv_score"]
                    data["winner"] = winner
                    data["winner_score"] = winner_score
                    data["winner_path"] = stream["model_path"]
                    data["winner_class"] = "SGD(streaming)"
                    data["winner_params"] = {}
                    state["_streaming_winner"] = True   # tuner skips (SGD not Optuna-tunable here)

        decision_log_entries = []
        for d in self.norm_dicts(response.get("decisions") if isinstance(response, dict) else None):
            entry = await self._log_decision(
                run_id=run_id,
                decision=str(d.get("decision", "")),
                reasoning=str(d.get("reasoning", "")),
                result_summary=f"winner={winner}, score={winner_score:.4f}",
            )
            decision_log_entries.append(entry)

        for model_name, model_data in data["models_results"].items():
            if "error" not in model_data:
                entry = await self._log_decision(
                    run_id=run_id,
                    decision=f"Trained {model_name}: cv_score={model_data['cv_score']:.4f}",
                    reasoning=f"Cross-validation with 5 folds. Std: {model_data['cv_std']:.4f}",
                    result_summary=f"time={model_data['training_time_s']}s",
                )
                decision_log_entries.append(entry)

        from app.core import mlflow_tracker as mlflow
        iteration = state.get("iteration", 0)
        # Use iteration-prefixed keys — MLflow forbids changing param values
        mlflow.log_params({f"i{iteration}_winner_model": winner})
        mlflow.log_metric("post_model_selection_score", winner_score, step=iteration)
        for m_name, m_data in data["models_results"].items():
            if "error" not in m_data:
                mlflow.log_metric(f"cv_score_{m_name}", m_data.get("cv_score", 0), step=iteration)

        await self.emit(
            run_id,
            f"Winner: {winner} with {winner_score:.4f} {state['primary_metric']}",
            {"winner": winner, "score": winner_score, "all_results": {
                k: v.get("cv_score") for k, v in data["models_results"].items()
            }},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        iteration = state.get("iteration", 0)
        new_cell = {
            "agent": self.name,
            "title": f"Model Selection — Iteration {iteration + 1}",
            "iteration": iteration + 1,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "iteration": iteration + 1,
                "models_compared": {
                    name: {
                        "cv_score": round(m.get("cv_score", -999), 4),
                        "cv_std": round(m.get("cv_std", 0), 4),
                        "training_time_s": m.get("training_time_s", 0),
                        "class": m.get("class", ""),
                    }
                    for name, m in data["models_results"].items()
                    if "error" not in m
                },
                "winner": winner,
                "winner_score": round(winner_score, 4),
                "winner_class": data.get("winner_class", ""),
                "winner_params": data.get("winner_params", {}),
                "selection_reasons": [
                    m.get("reason", "") for m in selected_models
                ],
            },
        }
        return {
            "models_evaluated": data["models_results"],
            # P3.14 — registry install-hints (CatBoost/LightGBM…) surfaced to the UI
            "model_recommendations": recs or "",
            "winner_model": winner,
            "winner_model_path": data["winner_path"],
            "winner_model_class": data.get("winner_class", ""),
            "best_hyperparams": data.get("winner_params", {}),
            # Streaming winner is already fit on all rows and isn't Optuna-tunable
            # here → the tuner skips it, passing the model straight to evaluation.
            "streaming_winner": bool(state.get("_streaming_winner")),
            "current_score": winner_score,
            "prev_score": state.get("current_score", state.get("baseline_score", 0.0)),
            "decision_log": existing_log + decision_log_entries,
            "notebook_cells": existing_cells + [new_cell],
        }
