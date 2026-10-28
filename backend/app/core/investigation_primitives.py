"""Bounded, deterministic test primitives for the Hypothesis Loop (Phase 2).

The LLM Strategist reasons about WHAT to test on a specific dataset and emits a
hypothesis that names one of these primitives + its parameters. Execution stays
safe, reproducible and unit-testable — no free-form generated code. Standalone
(only numpy/pandas/sklearn) so the agent can ship the source into the sandbox,
exactly like `trust_probes`.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SAMPLE_CAP = 20000
CV_FOLDS = 3


def _is_classification(task_type: str) -> bool:
    t = (task_type or "").lower()
    return ("class" in t or "label" in t) and "multilabel" not in t


def _score(y_true, y_pred, task_type: str) -> float:
    from sklearn.metrics import f1_score, r2_score
    if _is_classification(task_type):
        return float(f1_score(y_true, y_pred, average="weighted", zero_division=0))
    return float(r2_score(y_true, y_pred))


def _cv_predict(model, X, y, task_type):
    from sklearn.base import clone
    from sklearn.model_selection import cross_val_predict, StratifiedKFold, KFold
    if _is_classification(task_type):
        k = max(2, min(CV_FOLDS, int(np.min(np.unique(y, return_counts=True)[1]))))
        cv = StratifiedKFold(k, shuffle=True, random_state=0)
    else:
        cv = KFold(CV_FOLDS, shuffle=True, random_state=0)
    return cross_val_predict(clone(model), X, y, cv=cv)


def _cv_score(model, X, y, task_type) -> float:
    return _score(y, _cv_predict(model, X, y, task_type), task_type)


# ── Primitives — each returns (observed: float, evidence: dict) ───────────────
def feature_ablation(model, X, y, task_type, params, base_cv, cv_pred):
    col = params["column"]
    if col not in X.columns:
        raise ValueError(f"unknown column {col!r}")
    s = _cv_score(model, X.drop(columns=[col]), y, task_type)
    drop = base_cv - s
    return float(drop), {"base_cv": round(base_cv, 4), "without_feature": round(s, 4),
                         "score_drop": round(float(drop), 4)}


def single_feature_power(model, X, y, task_type, params, base_cv, cv_pred):
    col = params["column"]
    if col not in X.columns:
        raise ValueError(f"unknown column {col!r}")
    s = _cv_score(model, X[[col]], y, task_type)
    return float(s), {"single_feature_cv_score": round(float(s), 4), "full_model_cv": round(base_cv, 4)}


def slice_performance(model, X, y, task_type, params, base_cv, cv_pred):
    col = params["column"]
    if col not in X.columns:
        raise ValueError(f"unknown column {col!r}")
    series = pd.to_numeric(X[col], errors="coerce")
    op = params.get("op")
    val = params.get("value")
    if op and val is not None:
        cmp = {">": series > val, "<": series < val, ">=": series >= val,
               "<=": series <= val, "==": series == val}.get(op, series == val)
        mask = cmp.fillna(False).to_numpy()
    else:
        mask = (series.fillna(0).to_numpy() == 1)   # one-hot slice
    overall = _score(y, cv_pred, task_type) if cv_pred is not None else base_cv
    if mask.sum() < 20:
        return 0.0, {"slice_n": int(mask.sum()), "note": "slice too small (<20)"}
    s = _score(np.asarray(y)[mask], np.asarray(cv_pred)[mask], task_type)
    return float(s - overall), {"slice_n": int(mask.sum()), "slice_score": round(float(s), 4),
                                "overall_score": round(float(overall), 4),
                                "delta": round(float(s - overall), 4)}


def interaction_test(model, X, y, task_type, params, base_cv, cv_pred):
    a, b = params["col_a"], params["col_b"]
    for c in (a, b):
        if c not in X.columns:
            raise ValueError(f"unknown column {c!r}")
    Xi = X.copy()
    Xi["__interaction__"] = pd.to_numeric(X[a], errors="coerce").fillna(0) * pd.to_numeric(X[b], errors="coerce").fillna(0)
    s = _cv_score(model, Xi, y, task_type)
    return float(s - base_cv), {"base_cv": round(base_cv, 4), "with_interaction": round(float(s), 4),
                                "improvement": round(float(s - base_cv), 4)}


def shift_stress(model, X, y, task_type, params, base_cv, cv_pred):
    mag = float(params.get("magnitude", 0.1))
    Xn = X.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0)
    base = _score(y, model.predict(Xn), task_type)
    rng = np.random.RandomState(0)
    stds = Xn.std(axis=0).replace(0, 1.0).to_numpy()
    noisy = Xn + rng.normal(0, mag * stds, size=Xn.shape)
    s = _score(y, model.predict(noisy), task_type)
    drop = (base - s) / (abs(base) + 1e-9)
    return float(drop), {"clean_score": round(float(base), 4), "noisy_score": round(float(s), 4),
                         "relative_drop": round(float(drop), 4), "magnitude": mag}


def target_correlation(model, X, y, task_type, params, base_cv, cv_pred):
    col = params["column"]
    if col not in X.columns:
        raise ValueError(f"unknown column {col!r}")
    yv = pd.Series(np.asarray(y).ravel())
    ycode = pd.factorize(yv)[0].astype("float64") if _is_classification(task_type) else yv.astype("float64").to_numpy()
    cv = np.corrcoef(pd.to_numeric(X[col], errors="coerce").fillna(0).to_numpy(), ycode)[0, 1]
    r = abs(float(cv)) if np.isfinite(cv) else 0.0
    return r, {"abs_corr_with_target": round(r, 4)}


_REGISTRY = {
    "feature_ablation": feature_ablation,
    "single_feature_power": single_feature_power,
    "slice_performance": slice_performance,
    "interaction_test": interaction_test,
    "shift_stress": shift_stress,
    "target_correlation": target_correlation,
}


def run_primitives(enriched_path, model_path, task_type, specs):
    """Load model+data once, run each spec's primitive defensively.

    specs: [{"id": str, "primitive": str, "params": {...}}]
    returns: [{"id", "primitive", "observed", "evidence", "error"?}]
    """
    import ast
    import joblib

    df = pd.read_csv(enriched_path)
    if task_type == "multilabel_classification":
        y = np.array(df["__target__"].apply(lambda v: ast.literal_eval(v) if isinstance(v, str) else v).tolist())
    else:
        y = df["__target__"].to_numpy()
    X = df.drop(columns=["__target__"])
    if len(X) > SAMPLE_CAP:
        idx = np.random.RandomState(0).choice(len(X), SAMPLE_CAP, replace=False)
        X, y = X.iloc[idx].reset_index(drop=True), y[idx]
    model = joblib.load(model_path)

    base_cv, cv_pred = None, None
    try:
        cv_pred = _cv_predict(model, X, y, task_type)
        base_cv = _score(y, cv_pred, task_type)
    except Exception:
        base_cv = 0.0

    results = []
    for spec in specs:
        fn = _REGISTRY.get(spec.get("primitive"))
        entry = {"id": spec.get("id"), "primitive": spec.get("primitive")}
        if fn is None:
            entry.update(observed=None, evidence={}, error="unknown primitive")
        else:
            try:
                observed, evidence = fn(model, X, y, task_type, spec.get("params", {}), base_cv, cv_pred)
                entry.update(observed=observed, evidence=evidence)
            except Exception as e:
                entry.update(observed=None, evidence={}, error=f"{type(e).__name__}: {str(e)[:160]}")
        results.append(entry)
    return results
