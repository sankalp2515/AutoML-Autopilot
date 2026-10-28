import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState

EDA_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns
import warnings, os
warnings.filterwarnings("ignore")

df = pd.read_csv(dataset_path)
target_col = "{target_column}"
task_type = "{task_type}"

os.makedirs(artifacts_dir, exist_ok=True)

# --- Statistical profiles (what goes to the LLM) ---
num_cols = df.select_dtypes(include=["number"]).columns.tolist()
cat_cols = df.select_dtypes(exclude=["number"]).columns.tolist()
if target_col in num_cols:
    num_cols.remove(target_col)
if target_col in cat_cols:
    cat_cols.remove(target_col)

# Skewness and kurtosis
skewness = df[num_cols].skew().round(3).to_dict() if num_cols else {{}}
kurtosis = df[num_cols].kurtosis().round(3).to_dict() if num_cols else {{}}

# Correlation with target
if task_type != "regression" and target_col in df.columns:
    try:
        target_numeric = df[target_col].astype(str).astype("category").cat.codes
        corr_with_target = df[num_cols].corrwith(target_numeric).round(3).to_dict()
    except Exception:
        corr_with_target = {{}}
else:
    corr_with_target = df[num_cols].corrwith(df[target_col]).round(3).to_dict() if num_cols else {{}}

# High mutual correlation between features (multicollinearity risk)
high_corr_pairs = []
if len(num_cols) > 1:
    corr_matrix = df[num_cols].corr().abs()
    upper = corr_matrix.where(np.triu(np.ones(corr_matrix.shape), k=1).astype(bool))
    high_corr_pairs = [
        {{"col1": c, "col2": r, "corr": round(upper.loc[r, c], 3)}}
        for c in upper.columns
        for r in upper.index
        if pd.notna(upper.loc[r, c]) and upper.loc[r, c] > 0.85
    ]

# Cardinality check
high_cardinality = {{c: int(df[c].nunique()) for c in cat_cols if df[c].nunique() > 50}}
low_cardinality_num = {{c: int(df[c].nunique()) for c in num_cols if df[c].nunique() < 10}}

# Outlier detection (IQR method on numeric cols)
outlier_pct = {{}}
for c in num_cols:
    q1, q3 = df[c].quantile(0.25), df[c].quantile(0.75)
    iqr = q3 - q1
    if iqr > 0:
        mask = (df[c] < q1 - 1.5 * iqr) | (df[c] > q3 + 1.5 * iqr)
        outlier_pct[c] = round(mask.mean() * 100, 2)

# Class imbalance
class_imbalance = None
imbalance_severity = "none"
if task_type != "regression" and target_col in df.columns:
    vc = df[target_col].value_counts(normalize=True)
    ratio = round(vc.max() / vc.min(), 2) if vc.min() > 0 else None
    minority_pct = float(vc.min() * 100) if len(vc) >= 2 else 0.0
    if minority_pct < 5.0:
        imbalance_severity = "severe"
    elif minority_pct < 20.0:
        imbalance_severity = "moderate"
    class_imbalance = {{
        "ratio": ratio,
        "distribution": vc.round(4).to_dict(),
        "imbalanced": bool(vc.max() > 0.7),
        "minority_pct": round(minority_pct, 2),
        "severity": imbalance_severity,
    }}

# Datetime columns
datetime_cols = []
for c in df.columns:
    if "date" in c.lower() or "time" in c.lower() or "dt" in c.lower():
        try:
            pd.to_datetime(df[c], errors="raise")
            datetime_cols.append(c)
        except Exception:
            pass

# Free-text columns (avg length > 30 chars) — candidates for TF-IDF features
# NOTE: doubled braces — this template goes through .format()
text_cols = []
for c in cat_cols:
    try:
        avg_len = df[c].dropna().astype(str).str.len().mean()
        if avg_len and avg_len > 30:
            text_cols.append({{"column": c, "avg_length": round(float(avg_len), 1)}})
    except Exception:
        pass

# --- Generate plots (artifacts, not sent to LLM) ---
# Target distribution plot
fig, ax = plt.subplots(figsize=(8, 4))
if task_type == "regression":
    df[target_col].hist(ax=ax, bins=30)
else:
    df[target_col].value_counts().plot(kind="bar", ax=ax)
ax.set_title(f"Target Distribution: {{target_col}}")
plt.tight_layout()
fig.savefig(os.path.join(artifacts_dir, "target_distribution.png"), dpi=100)
plt.close()

# Correlation heatmap (top 15 features by target correlation)
if len(num_cols) > 1:
    top_corr_cols = sorted(corr_with_target.items(), key=lambda x: abs(x[1]), reverse=True)
    top_cols = [c for c, _ in top_corr_cols[:15]] + [target_col]
    top_cols = [c for c in top_cols if c in df.columns]
    # Target may be a string class label (e.g. 'Iris-setosa') — encode it
    # numerically or .corr() crashes with "could not convert string to float"
    df_corr = df[top_cols].copy()
    for c in df_corr.columns:
        if not pd.api.types.is_numeric_dtype(df_corr[c]):
            df_corr[c] = df_corr[c].astype(str).astype("category").cat.codes
    fig, ax = plt.subplots(figsize=(10, 8))
    sns.heatmap(df_corr.corr().round(2), annot=True, fmt=".2f",
                cmap="coolwarm", ax=ax, cbar=True)
    ax.set_title("Feature Correlation Heatmap (top features)")
    plt.tight_layout()
    fig.savefig(os.path.join(artifacts_dir, "correlation_heatmap.png"), dpi=100)
    plt.close()

RESULT = {{
    "num_cols": num_cols,
    "cat_cols": cat_cols,
    "skewness": skewness,
    "kurtosis": kurtosis,
    "corr_with_target": corr_with_target,
    "high_corr_pairs": high_corr_pairs,
    "high_cardinality_cols": high_cardinality,
    "low_cardinality_num_cols": low_cardinality_num,
    "outlier_pct": outlier_pct,
    "class_imbalance": class_imbalance,
    "imbalance_severity": imbalance_severity,
    "datetime_cols": datetime_cols,
    "text_cols": text_cols,
    "plots_generated": ["target_distribution.png", "correlation_heatmap.png"],
}}
'''

SYSTEM_PROMPT = """You are an expert data scientist performing targeted EDA.
You have already seen the baseline model performance. Your EDA should focus on
understanding WHY the baseline fails and WHAT can improve it.

Respond with JSON:
{
  "prioritized_issues": [
    "Issue 1 (most impactful, with action)",
    "Issue 2",
    ...
  ],
  "decisions": [{"decision": "...", "reasoning": "..."}]
}

Rules:
- Maximum 8 prioritized issues
- Each issue must be actionable: state the problem AND what to do about it
- Order by expected impact on model performance
- Reference specific column names, percentages, and correlation values from the profile
"""


class EDAAgent(BaseAgent):
    name = "eda_agent"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Running targeted EDA — focusing on what the baseline model got wrong...")

        code = EDA_CODE_TEMPLATE.format(
            target_column=state["target_column"],
            task_type=state["task_type"],
        )

        result = await self.execute_code(code, run_id, timeout=180)
        result = await self.try_agentic_repair(
            run_id, code, result,
            task_type=state.get("task_type", "unknown"),
            result_keys=["num_cols", "cat_cols", "skewness", "high_corr_pairs",
                         "high_cardinality_cols", "outlier_pct", "datetime_cols",
                         "corr_with_target"],
            goal=("Profile dataset_path (which has a '__target__' column or target_column) for EDA: "
                  "set RESULT with num_cols, cat_cols (lists), skewness ({col: float}), "
                  "high_corr_pairs (list), high_cardinality_cols (dict), outlier_pct ({col: pct}), "
                  "datetime_cols (list), corr_with_target ({col: corr})."),
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"EDA failed: {result['error']}", "status": "failed"}

        # Boundary validation: normalize the sandbox profile once — every key the
        # backend iterates gets the right TYPE, so malformed entries can't detonate
        # the f-string prompt build or the notebook cell below.
        raw = self.result_dict(result)
        profile = dict(raw)
        for k in ("num_cols", "cat_cols", "datetime_cols", "text_cols", "high_corr_pairs", "plots_generated"):
            profile[k] = raw.get(k) if isinstance(raw.get(k), list) else []
        for k in ("skewness", "outlier_pct", "corr_with_target", "high_cardinality_cols"):
            v = raw.get(k)
            profile[k] = {sk: sv for sk, sv in v.items()
                          if isinstance(sk, str) and isinstance(sv, (int, float))} if isinstance(v, dict) else {}

        audit = state.get("data_audit", {})
        user_message = f"""
Baseline score: {state.get('baseline_score', 'N/A')} ({state.get('primary_metric')})
Baseline error rate on train: {state.get('baseline_errors', {}).get('error_rate_on_train', 'N/A')}

EDA findings:
- Numeric columns ({len(profile['num_cols'])}): {profile['num_cols'][:10]}
- Categorical columns ({len(profile['cat_cols'])}): {profile['cat_cols'][:10]}
- Highly skewed columns (|skew|>1): {json.dumps({k:v for k,v in profile['skewness'].items() if abs(v)>1})}
- High correlation pairs (>0.85): {json.dumps(profile['high_corr_pairs'][:5])}
- High cardinality categoricals: {json.dumps(profile['high_cardinality_cols'])}
- Outlier percentages (top 5): {json.dumps(dict(sorted(profile['outlier_pct'].items(), key=lambda x:-x[1])[:5]))}
- Class imbalance: {json.dumps(profile.get('class_imbalance'))}
- Datetime columns detected: {profile['datetime_cols']}
- Free-text columns detected (TF-IDF candidates): {json.dumps(profile.get('text_cols', []))}
- Top correlations with target: {json.dumps(dict(sorted(profile['corr_with_target'].items(), key=lambda x:-abs(x[1]))[:8]))}
- Null % (from audit): {json.dumps(dict(sorted(audit.get('null_pct', {}).items(), key=lambda x:-x[1])[:8]))}

What are the top issues to address for maximum model improvement?
"""
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)

        if not isinstance(response, dict):
            response = {}
        prioritized_issues = [str(i) for i in response.get("prioritized_issues", [])
                              if isinstance(i, (str, int, float))] \
            if isinstance(response.get("prioritized_issues"), list) else []

        decision_log_entries = []
        for d in self.norm_dicts(response.get("decisions")):
            entry = await self._log_decision(
                run_id=run_id,
                decision=str(d.get("decision", "")),
                reasoning=str(d.get("reasoning", "")),
                result_summary=f"{len(prioritized_issues)} issues prioritized",
            )
            decision_log_entries.append(entry)

        from app.core import mlflow_tracker as mlflow
        mlflow.log_dict({"prioritized_issues": prioritized_issues}, "eda_issues.json")
        mlflow.log_dict({"corr_with_target": profile["corr_with_target"]}, "feature_correlations.json")

        import os
        artifact_dir = os.path.join(state.get("data_dir", "/data"), run_id, "artifacts")
        for plot in profile.get("plots_generated", []):
            if isinstance(plot, str) and plot:
                mlflow.log_artifact(os.path.join(artifact_dir, plot))

        await self.emit(
            run_id,
            f"EDA complete. {len(prioritized_issues)} actionable issues identified.",
            {"issues": prioritized_issues[:3]},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        new_cell = {
            "agent": self.name,
            "title": "Exploratory Data Analysis",
            "iteration": 0,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "num_cols": profile["num_cols"][:15],
                "cat_cols": profile["cat_cols"][:10],
                "top_correlations_with_target": dict(
                    sorted(profile["corr_with_target"].items(), key=lambda x: -abs(x[1]))[:8]
                ),
                "highly_skewed_cols": {
                    k: v for k, v in profile["skewness"].items() if abs(v) > 1
                },
                "outlier_pct_top5": dict(
                    sorted(profile["outlier_pct"].items(), key=lambda x: -x[1])[:5]
                ),
                "high_corr_pairs": profile["high_corr_pairs"][:5],
                "class_imbalance": profile.get("class_imbalance"),
                "datetime_cols": profile["datetime_cols"],
                "high_cardinality_cols": profile["high_cardinality_cols"],
                "prioritized_issues": prioritized_issues,
            },
        }
        return {
            "eda_insights": profile,
            "prioritized_issues": prioritized_issues,
            "decision_log": existing_log + decision_log_entries,
            "notebook_cells": existing_cells + [new_cell],
        }
