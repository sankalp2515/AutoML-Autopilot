"""Prompt-regression validators — the output CONTRACT of each agent's LLM call.

These encode what a GOOD response must satisfy, independent of the prompt wording.
They are the value of the harness: agent LLM output had ZERO contract coverage, so a
prompt edit could silently start producing worse decisions and nothing would notice.

Usage:
- Replay tests (CI, deterministic) run these against RECORDED golden responses, and also
  push the responses through the real agent parsing to catch code regressions.
- The record script (tools/record_golden.py, live) runs these against CURRENT-PROMPT
  responses: if a prompt change makes the model violate a contract, recording fails.

Each validator returns a list of violation strings ([] == valid).
"""
from __future__ import annotations

from typing import Any

# Vocabularies the agents actually accept (mirrors the agent code / registries).
TASK_TYPES = {
    "binary_classification", "multiclass_classification",
    "multilabel_classification", "regression",
}
METRICS = {
    "auc_roc", "pr_auc", "recall", "precision", "f1", "accuracy",
    "rmse", "mae", "r2", "f1_micro", "f1_macro", "f1_samples", "hamming_loss",
}
ENCODINGS = {"onehot", "ordinal", "target", "frequency", "text_tfidf"}
SCALINGS = {"standard", "minmax", "robust", "none"}
IMPUTERS = {"median", "mean", "most_frequent", "constant", "knn"}
IMBALANCE = {"class_weight", "smote", "smote_tomek", "none"}
PRIMITIVES = {
    "feature_ablation", "single_feature_power", "slice_performance",
    "interaction_test", "shift_stress", "target_correlation",
}


def _is_dict(x) -> bool:
    return isinstance(x, dict)


def validate_problem_framer(resp: Any, ctx: dict | None = None) -> list[str]:
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    tt = resp.get("task_type")
    if tt not in TASK_TYPES:
        v.append(f"task_type '{tt}' not in {sorted(TASK_TYPES)}")
    pm = resp.get("primary_metric")
    if pm not in METRICS:
        v.append(f"primary_metric '{pm}' not a known metric")
    # metric must suit the task family (a regression metric on a classifier is wrong)
    reg_metrics = {"rmse", "mae", "r2"}
    if tt == "regression" and pm not in reg_metrics:
        v.append(f"regression task but non-regression metric '{pm}'")
    if tt and tt != "regression" and pm in reg_metrics:
        v.append(f"classification task but regression metric '{pm}'")
    tc = resp.get("target_column")
    if not isinstance(tc, str) or not tc:
        v.append("target_column missing/empty")
    elif ctx and ctx.get("columns") and tc not in ctx["columns"]:
        v.append(f"target_column '{tc}' not in the dataset columns")
    return v


def validate_model_selector(resp: Any, ctx: dict | None = None) -> list[str]:
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    models = resp.get("selected_models")
    if not isinstance(models, list) or not models:
        return ["selected_models missing or empty"]
    if not (1 <= len(models) <= 4):
        v.append(f"selected {len(models)} models (want 2-3, hard cap 4)")
    allowed = set(ctx.get("allowed_classes", [])) if ctx else set()
    for m in models:
        if not _is_dict(m):
            v.append("a candidate is not an object")
            continue
        cls = m.get("class")
        if not isinstance(cls, str) or not cls:
            v.append("a candidate has no class string")
        elif allowed and cls not in allowed:
            v.append(f"class '{cls}' not in the allowed registry menu")
    return v


def validate_preprocessor(resp: Any, ctx: dict | None = None) -> list[str]:
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    scaling = resp.get("scaling_strategy")
    if scaling is not None and scaling not in SCALINGS:
        v.append(f"scaling_strategy '{scaling}' invalid")
    for col, enc in (resp.get("encoding_strategy") or {}).items():
        if enc not in ENCODINGS:
            v.append(f"encoding '{enc}' for '{col}' invalid")
    for col, imp in (resp.get("imputation_strategy") or {}).items():
        if imp not in IMPUTERS:
            v.append(f"imputation '{imp}' for '{col}' invalid")
    imb = resp.get("imbalance_strategy")
    if imb is not None and imb not in IMBALANCE:
        v.append(f"imbalance_strategy '{imb}' invalid")
    return v


def validate_eda(resp: Any, ctx: dict | None = None) -> list[str]:
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    issues = resp.get("prioritized_issues")
    if not isinstance(issues, list):
        v.append("prioritized_issues is not a list")
    for d in (resp.get("decisions") or []):
        if not _is_dict(d) or not d.get("decision"):
            v.append("a decision entry is malformed (needs a 'decision')")
            break
    return v


def validate_planner(resp: Any, ctx: dict | None = None) -> list[str]:
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    for k in ("skip_feature_engineer", "skip_tuner"):
        if k in resp and not isinstance(resp[k], bool):
            v.append(f"{k} must be a boolean")
    mi = resp.get("max_iterations")
    if mi is not None and (not isinstance(mi, int) or not (0 <= mi <= 3)):
        v.append(f"max_iterations {mi!r} must be int 0-3")
    return v


def validate_strategist(resp: Any, ctx: dict | None = None) -> list[str]:
    """Hypothesis-loop strategist: hypotheses must reference known primitives."""
    v: list[str] = []
    if not _is_dict(resp):
        return ["response is not a JSON object"]
    hyps = resp.get("hypotheses")
    if not isinstance(hyps, list):
        return ["hypotheses is not a list"]
    for h in hyps:
        if not _is_dict(h):
            v.append("a hypothesis is not an object")
            continue
        if h.get("primitive") not in PRIMITIVES:
            v.append(f"primitive '{h.get('primitive')}' not in the catalog")
        exp = h.get("expected")
        if not _is_dict(exp) or exp.get("direction") not in {">", "<", ">=", "<="}:
            v.append("hypothesis missing a valid expected.direction")
    return v


VALIDATORS = {
    "problem_framer": validate_problem_framer,
    "model_selector": validate_model_selector,
    "preprocessor": validate_preprocessor,
    "eda_agent": validate_eda,
    "planner": validate_planner,
    "strategist": validate_strategist,
}
