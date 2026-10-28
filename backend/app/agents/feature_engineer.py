import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState
from app.core import metric_registry

SYSTEM_PROMPT = """You are an expert feature engineer.
You design new features based on EDA insights and baseline error analysis.
Every feature you propose MUST have a hypothesis — a reason why it will help the model.
Do NOT propose exhaustive polynomial features.

Respond with JSON:
{
  "proposed_features": [
    {
      "name": "<feature_name>",
      "formula": "<pandas expression using existing columns>",
      "hypothesis": "<why this will help the model>",
      "source_columns": ["<col1>", "<col2>"]
    }
  ],
  "decisions": [{"decision": "...", "reasoning": "..."}]
}

STRICT Rules:
- Maximum 6 proposed features
- Every feature MUST produce a numeric (float/int) output — NO string labels
- Use np.log1p() NOT np.log() for log transforms (avoids infinity on zero values)
- For age/value binning: use integer codes — pd.cut(..., labels=False) — NOT string labels
- For boolean flags: use (condition).astype(int) — NOT True/False
- Reference only the ORIGINAL raw column names that exist in source_columns
- Prefer ratio and interaction features (numeric / numeric)
- Do NOT propose features that require columns that may have been dropped
"""

FEATURE_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import json
import os
import warnings
warnings.filterwarnings("ignore")

from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.model_selection import cross_val_score, StratifiedKFold, KFold
from sklearn.multioutput import MultiOutputClassifier

# Load processed data
processed_path = "{processed_path}"
df = pd.read_csv(processed_path)
target_col = "{target_column}"
task_type = "{task_type}"
primary_metric = "{primary_metric}"
imbalance_strategy = "{imbalance_strategy}"


def _cv_estimator(model):
    # In-fold SMOTE/SMOTE-Tomek for single-label classification lift measurement.
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

# Multilabel targets are stored as JSON lists — parse to a 2-D label matrix
# (mirror of the parse block in model_selector/tuner/evaluator).
if task_type == "multilabel_classification":
    import ast
    y_raw = df["__target__"].apply(lambda v: ast.literal_eval(v) if isinstance(v, str) else v)
    y = np.array(y_raw.tolist())
else:
    y = df["__target__"].values
X_base = df.drop(columns=["__target__"])

# Also load raw CSV for original columns (for feature engineering)
df_raw = pd.read_csv(dataset_path)

# Scalability: feature LIFT is a RELATIVE comparison, so evaluate it on a capped
# sample — otherwise a multi-million-row dataset makes per-feature CV run for
# hours. Kept features are still applied to the FULL dataset below, so what gets
# trained downstream is unchanged. (Aligned sampling preserves the X/df_raw pairing.)
_FE_CV_CAP = 50000
if len(X_base) > _FE_CV_CAP and len(df_raw) == len(X_base):
    _ridx = np.random.RandomState(42).choice(len(X_base), _FE_CV_CAP, replace=False)
    X_eval = X_base.iloc[_ridx].reset_index(drop=True)
    y_eval = y[_ridx]
    df_raw_eval = df_raw.iloc[_ridx].reset_index(drop=True)
else:
    X_eval, y_eval, df_raw_eval = X_base, y, df_raw

# Scoring map mirrors other agents — remap to sklearn names
# Scorer resolution EMITTED by metric_registry.scorer_prelude (single source
# of truth). No metric vocabulary lives in this template.
{scorer_prelude}
cv_scoring = scoring

results = []
proposed = {proposed_features}

for feat in proposed:
    name = feat["name"]
    formula = feat["formula"]
    try:
        # Evaluate formula with column names as variables + pd/np available
        # (on the capped eval sample — see _FE_CV_CAP above).
        eval_ctx = {{col: df_raw_eval[col] for col in df_raw_eval.columns}}
        eval_ctx.update({{"pd": pd, "np": np, "df": df_raw_eval}})
        new_col = eval(formula, {{"__builtins__": {{}}}}, eval_ctx)
        if not hasattr(new_col, "__len__"):
            new_col = pd.Series([new_col] * len(df_raw_eval))
        new_col = pd.Series(new_col).reset_index(drop=True)

        # Force numeric — drop if non-numeric (e.g. string labels from pd.cut)
        new_col = pd.to_numeric(new_col, errors="coerce")
        # Replace infinity (e.g. log(0))
        new_col = new_col.replace([np.inf, -np.inf], np.nan)

        if new_col.isnull().mean() > 0.5:
            results.append({{"name": name, "kept": False, "reason": "non-numeric or too many nulls after coercion"}})
            continue

        # Fill remaining nulls with column median (always numeric)
        fill_val = float(new_col.median()) if not new_col.isna().all() else 0.0
        new_col = new_col.fillna(fill_val)

        # Measure cross-val lift vs base — on the capped eval sample.
        X_aug = pd.concat([X_eval.reset_index(drop=True),
                           new_col.reset_index(drop=True).rename(name)], axis=1)

        if task_type == "regression":
            cv = KFold(n_splits=3, shuffle=True, random_state=42)
            model = Ridge()
            scoring = "neg_root_mean_squared_error"
            base_score = float(-cross_val_score(model, X_eval, y_eval, cv=cv, scoring=scoring).mean())
            aug_score = float(-cross_val_score(model, X_aug, y_eval, cv=cv, scoring=scoring).mean())
            lift = base_score - aug_score  # lower RMSE is better
        elif task_type == "multilabel_classification":
            # Multilabel: KFold (Stratified unsupported for 2-D y), MultiOutputClassifier,
            # and a multilabel-safe scorer (f1_samples) regardless of primary_metric.
            cv = KFold(n_splits=3, shuffle=True, random_state=42)
            model = MultiOutputClassifier(LogisticRegression(max_iter=500, random_state=42), n_jobs=1)
            scoring = cv_scoring if cv_scoring in ("f1_micro", "f1_macro", "f1_samples", "hamming_loss") else "f1_samples"
            base_score = float(cross_val_score(model, X_eval, y_eval, cv=cv, scoring=scoring).mean())
            aug_score = float(cross_val_score(model, X_aug, y_eval, cv=cv, scoring=scoring).mean())
            lift = aug_score - base_score
        else:
            cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=42)
            model = LogisticRegression(max_iter=500, random_state=42)
            scoring = cv_scoring
            base_score = float(cross_val_score(_cv_estimator(model), X_eval, y_eval, cv=cv, scoring=scoring).mean())
            aug_score = float(cross_val_score(_cv_estimator(model), X_aug, y_eval, cv=cv, scoring=scoring).mean())
            lift = aug_score - base_score

        kept = lift > 0.002  # keep only if meaningful lift
        results.append({{
            "name": name,
            "kept": kept,
            "cv_lift": round(lift, 5),
            "reason": "positive lift" if kept else f"insufficient lift ({{lift:.5f}})",
            "formula": formula,
            "fill_value": fill_val,  # training-time median — reused at inference
        }})
    except Exception as e:
        results.append({{"name": name, "kept": False, "reason": str(e)}})

# Build enriched dataset with kept features
kept_features = [r for r in results if r["kept"]]
for feat in kept_features:
    try:
        eval_ctx2 = {{col: df_raw[col] for col in df_raw.columns}}
        eval_ctx2.update({{"pd": pd, "np": np, "df": df_raw}})
        col = eval(feat["formula"], {{"__builtins__": {{}}}}, eval_ctx2)
        col = pd.Series(col).reset_index(drop=True)
        col = pd.to_numeric(col, errors="coerce")
        col = col.replace([np.inf, -np.inf], np.nan)
        fill_val = float(col.median()) if not col.isna().all() else 0.0
        col = col.fillna(fill_val)
        df = pd.concat([df, col.reset_index(drop=True).rename(feat["name"])], axis=1)
    except Exception:
        pass

# Save enriched dataset
enriched_path = os.path.join(artifacts_dir, "enriched.csv")
df.to_csv(enriched_path, index=False)

RESULT = {{
    "features_evaluated": results,
    "features_kept": [r for r in results if r["kept"]],
    "features_dropped": [r for r in results if not r["kept"]],
    "enriched_path": enriched_path,
    "n_features_total": df.shape[1] - 1,
}}
'''


class FeatureEngineerAgent(BaseAgent):
    name = "feature_engineer"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Engineering hypothesis-driven features — each one tested for actual lift...")

        eda = state.get("eda_insights", {})
        audit = state.get("data_audit", {})
        issues = state.get("prioritized_issues", [])
        iteration = state.get("iteration", 0)

        shap_context = ""
        if iteration > 0 and state.get("shap_top_features"):
            shap_context = f"\nSHAP top features from previous iteration: {state['shap_top_features']}"

        user_message = f"""
Task: {state['task_type']} | Target: {state['target_column']}
Baseline score: {state.get('baseline_score')} | Current score: {state.get('current_score')}
Iteration: {iteration + 1}

Original columns: {json.dumps(list(audit.get('dtypes', {}).keys())[:30])}
Numeric columns: {json.dumps(eda.get('num_cols', [])[:20])}
Categorical columns: {json.dumps(eda.get('cat_cols', [])[:10])}
Datetime columns: {json.dumps(eda.get('datetime_cols', []))}
High cardinality categoricals: {json.dumps(eda.get('high_cardinality_cols', {}))}

Top correlations with target: {json.dumps(dict(sorted(eda.get('corr_with_target', {}).items(), key=lambda x: -abs(x[1]))[:8]))}
High correlation pairs (multicollinearity risk): {json.dumps(eda.get('high_corr_pairs', [])[:5])}{shap_context}

Top EDA issues:
{chr(10).join(f'- {i}' for i in issues[:5])}

Propose new features with business hypotheses.
"""
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)
        # Boundary validation: the LLM is untrusted — keep ONLY well-formed feature
        # specs (dicts with a name + formula). A malformed entry (e.g. a bare string)
        # is dropped here instead of detonating a `feat["name"]` TypeError downstream.
        raw_proposed = response.get("proposed_features", [])
        proposed = [p for p in raw_proposed if isinstance(p, dict) and p.get("name") and p.get("formula")]

        if not proposed:
            await self.emit(run_id, "No new features proposed — moving forward with existing features")
            await self._mark_step(run_id, "completed")
            processed_path = state.get("processed_data_path", "")
            existing_cells = state.get("notebook_cells", [])
            no_feat_cell = {
                "agent": self.name,
                "title": f"Feature Engineering — Iteration {iteration + 1}",
                "iteration": iteration + 1,
                "code": "# LLM determined no hypothesis-driven features were applicable\n# for this iteration. Proceeding with existing engineered features.",
                "stdout": "",
                "result_summary": {
                    "iteration": iteration + 1,
                    "n_proposed": 0,
                    "n_kept": 0,
                    "reason": "LLM found no actionable feature hypotheses given current data and EDA insights",
                },
            }
            return {
                "features_created": [],
                "features_dropped": [],
                "enriched_data_path": processed_path,
                "notebook_cells": existing_cells + [no_feat_cell],
            }

        code = FEATURE_CODE_TEMPLATE.format(
            processed_path=state.get("processed_data_path", ""),
            target_column=state["target_column"],
            task_type=state["task_type"],
            primary_metric=state.get("primary_metric", "auc_roc"),
            scorer_prelude=metric_registry.scorer_prelude(
                state.get("primary_metric", ""), state.get("task_type", ""),
                k=float(state.get("rare_event_k") or metric_registry.DEFAULT_K)),
            proposed_features=repr(proposed),
            imbalance_strategy=state.get("imbalance_strategy", "none"),
        )

        result = await self.execute_code(code, run_id, timeout=300)
        if not result["success"]:
            # E3: agent writes its own fix when the template fails.
            agentic = await self.execute_code_agentic(
                run_id, code, result["error"], agent_role="feature_engineer",
                task_type=state.get("task_type", "unknown"),
                tags=["feature_engineer"] + (["multilabel"] if state.get("task_type") == "multilabel_classification" else []),
                goal=("Read processed_path (a CSV that already has ALL preprocessed feature columns "
                      "plus a '__target__' column). CRITICAL CONTRACT: enriched.csv MUST contain EVERY "
                      "column from processed_path UNCHANGED, plus '__target__', plus any new engineered "
                      "columns you ADD. Never drop existing columns, never rename '__target__', never "
                      "read the raw dataset for the base — start from the processed dataframe and only "
                      "APPEND columns. Keep engineered features only if they improve CV score."),
                result_keys=["features_evaluated", "features_kept", "features_dropped",
                             "enriched_path", "n_features_total"],
                timeout=300,
            )
            if not agentic["success"]:
                await self._mark_step(run_id, "failed", agentic["error"])
                return {"error": f"Feature engineering failed: {agentic['error']}", "status": "failed"}
            result = agentic

        # Boundary validation: trust nothing from the sandbox RESULT — coerce the
        # feature lists to dicts so every downstream f["name"] is safe by construction.
        data = result["result"] if isinstance(result.get("result"), dict) else {}
        _dicts = lambda seq: [e for e in (seq or []) if isinstance(e, dict)]
        kept = _dicts(data.get("features_kept"))
        dropped = _dicts(data.get("features_dropped"))

        # ── CONTRACT GUARD (2026 bank+marketing fix) ─────────────────────────────
        # Feature engineering is ADDITIVE and best-effort: enriched.csv MUST be a
        # superset of the processed data (all its columns + '__target__'). The
        # agentic-repair path can violate this — on bank+marketing it wrote a
        # 3-column file ('duration','y','duration_sq'), losing __target__ and 50
        # features, which then crashed model_selector. If the output is not a valid
        # superset, DISCARD it and fall back to the processed data (no features
        # engineered, but the pipeline continues on valid data — never corrupted).
        processed_path = state.get("processed_data_path", "")
        enriched_path = data.get("enriched_path", "")
        contract_ok = False
        try:
            import pandas as _pd
            proc_cols = set(_pd.read_csv(processed_path, nrows=0).columns) if processed_path else set()
            enr_cols = set(_pd.read_csv(enriched_path, nrows=0).columns) if enriched_path else set()
            contract_ok = bool(enriched_path) and "__target__" in enr_cols and proc_cols.issubset(enr_cols)
        except Exception as _cg_err:
            self._log.warning("feature_contract_check_failed", run_id=run_id, error=str(_cg_err)[:150])
            contract_ok = False
        if not contract_ok:
            self._log.warning("feature_engineer_output_rejected", run_id=run_id,
                              reason="enriched.csv is not a valid superset of processed.csv")
            await self.emit(run_id, "Feature engineering output was malformed — falling back to the "
                                    "validated preprocessed data (no engineered features this round).")
            data = {**data, "enriched_path": processed_path,
                    "features_kept": [], "features_dropped": [], "features_evaluated": [],
                    "n_features_total": None}
            kept, dropped = [], []

        decision_log_entries = []
        for feat in _dicts(data.get("features_evaluated")):
            fname = feat.get("name", "?")
            hypothesis = next((p.get("hypothesis", "") for p in proposed if p.get("name") == fname), "")
            entry = await self._log_decision(
                run_id=run_id,
                decision=f"Feature '{fname}': {'KEPT' if feat.get('kept') else 'DROPPED'}",
                reasoning=f"Hypothesis: {hypothesis}. CV lift: {feat.get('cv_lift', 0):.5f}. Reason: {feat.get('reason')}",
                result_summary=f"lift={feat.get('cv_lift', 0):.5f}",
            )
            decision_log_entries.append(entry)

        from app.core import mlflow_tracker as mlflow
        # Prefix with iteration — MLflow forbids changing a param's value
        mlflow.log_params({
            f"i{iteration}_feature_kept_{i}": f.get("name", "")
            for i, f in enumerate(kept[:5])
        })
        mlflow.log_metric("n_features_engineered_kept", len(kept), step=iteration)

        n_features_total = data.get("n_features_total", len(kept))
        await self.emit(
            run_id,
            f"Feature engineering: {len(kept)} kept, {len(dropped)} dropped. Total features: {n_features_total}",
            {"kept": [f.get("name", "") for f in kept], "dropped": [f.get("name", "") for f in dropped]},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        new_cell = {
            "agent": self.name,
            "title": f"Feature Engineering — Iteration {iteration + 1}",
            "iteration": iteration + 1,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "iteration": iteration + 1,
                "n_proposed": len(proposed),
                "n_kept": len(kept),
                "n_dropped": len(dropped),
                "n_features_total": n_features_total,
                "kept_features": [
                    {"name": f.get("name", ""), "cv_lift": round(f.get("cv_lift", 0), 5),
                     "formula": f.get("formula", "")}
                    for f in kept
                ],
                "dropped_features": [
                    {"name": f.get("name", ""), "reason": f.get("reason", "")}
                    for f in dropped
                ],
                "feature_hypotheses": {
                    p.get("name", ""): p.get("hypothesis", "")
                    for p in proposed
                },
            },
        }
        return {
            "features_created": kept,
            "features_dropped": dropped,
            "enriched_data_path": data.get("enriched_path", state.get("processed_data_path")),
            "decision_log": existing_log + decision_log_entries,
            "notebook_cells": existing_cells + [new_cell],
        }
