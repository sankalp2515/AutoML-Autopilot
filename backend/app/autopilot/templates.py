"""Approved experiment templates — the ONLY things Autopilot can execute.

Diagnostics are read-only and return an observation bucket. Correctives build a
serving candidate (model artifact + input fix + threshold) and report the
validation metric they *expect*; the verifier recomputes everything itself.

Parameters are validated by `validate_params` before the policy gate admits an
experiment. There is no template that runs model-written code.
"""
from __future__ import annotations

import inspect
import math
from dataclasses import dataclass
from typing import Any, Callable

from app.autopilot import mlcore
from app.autopilot.profiler import PSI_CRIT, cat_psi, psi
from app.autopilot.workspace import Workspace

PROBE_OBJECTS = {
    "OneHotEncoder": "sklearn.preprocessing",
    "StandardScaler": "sklearn.preprocessing",
    "SimpleImputer": "sklearn.impute",
    "LogisticRegression": "sklearn.linear_model",
    "HistGradientBoostingClassifier": "sklearn.ensemble",
}
_IDENT = set("abcdefghijklmnopqrstuvwxyz_0123456789")


def _ident(s: Any) -> bool:
    return isinstance(s, str) and 0 < len(s) <= 40 and set(s.lower()) <= _IDENT


_REGISTRY: dict[str, type] | None = None


def probe_registry() -> dict[str, type]:
    """Every public scikit-learn estimator and transformer by class name. The probe only
    ever resolves names through this registry, so a name from a user's error log can
    never trigger an arbitrary import."""
    global _REGISTRY
    if _REGISTRY is None:
        from sklearn.utils import all_estimators
        _REGISTRY = {name: cls for name, cls in all_estimators()}
    return _REGISTRY


def is_probe_object(obj: str | None) -> bool:
    return bool(obj) and (obj in PROBE_OBJECTS or obj in probe_registry())


def _installed_params(obj: str) -> list[str]:
    import importlib
    cls = (getattr(importlib.import_module(PROBE_OBJECTS[obj]), obj) if obj in PROBE_OBJECTS
           else probe_registry()[obj])
    return [p for p in inspect.signature(cls.__init__).parameters if p != "self"]


# ── Diagnostics ───────────────────────────────────────────────────────────────
def contract_diagnostic(ws: Workspace, params: dict) -> dict:
    pack = ws.pack()
    cfg = pack.get("incumbent_pipeline") or pack["pipeline"]
    ref, cur = ws.reference(), ws.current()
    feats = cfg["numeric"] + cfg["categorical"]
    missing_cols = [c for c in feats if c not in cur.columns]
    null_jump = {c: round(float(cur[c].isna().mean() - ref[c].isna().mean()), 4)
                 for c in feats if c in cur.columns}
    bad_nulls = {c: v for c, v in null_jump.items() if v > 0.2}
    unseen = {c: sorted(set(cur[c].dropna().astype(str)) - set(ref[c].dropna().astype(str)))
              for c in cfg["categorical"] if c in cur.columns}
    unseen = {c: v for c, v in unseen.items() if v}
    unseen_share = {c: round(float(cur[c].astype(str).isin(v).mean()), 4) for c, v in unseen.items()}
    coerce = {c: round(float(pd_to_num_fail(cur[c])), 4) for c in cfg["numeric"] if c in cur.columns}
    broken = bool(missing_cols or bad_nulls or any(s > 0.05 for s in unseen_share.values())
                  or any(v > 0.01 for v in coerce.values()))
    return {"bucket": "CONTRACT_BROKEN" if broken else "CONTRACT_OK",
            "measurements": {"missing_columns": missing_cols, "null_rate_increase": null_jump,
                             "unseen_categories": unseen, "unseen_share": unseen_share,
                             "non_numeric_share": coerce}}


def pd_to_num_fail(s) -> float:
    import pandas as pd
    nn = s.notna()
    if not nn.any():
        return 0.0
    return float(pd.to_numeric(s[nn], errors="coerce").isna().mean())


def train_serve_parity(ws: Workspace, params: dict) -> dict:
    pack = ws.pack()
    cfg = pack.get("incumbent_pipeline") or pack["pipeline"]
    ref, cur = ws.reference(), ws.current()
    per = {}
    scale = None
    for c in cfg["numeric"]:
        r = ref[c].dropna()
        k = cur[c].dropna()
        if len(r) < 20 or len(k) < 20 or r.median() == 0 or k.median() == 0:
            continue
        ratio = float(k.median() / r.median())
        lg = math.log10(abs(ratio))
        p_raw = psi(r, k)
        entry = {"median_ratio": round(ratio, 5), "psi": round(p_raw, 4)}
        near = round(lg)
        if near != 0 and abs(lg - near) < 0.15:
            factor = 10.0 ** (-near)
            p_fixed = psi(r, k * factor)
            entry.update(rescale_factor=factor, psi_after_rescale=round(p_fixed, 4))
            if p_fixed < 0.1 and (scale is None or p_raw > per[scale]["psi"]):
                scale = c
        per[c] = entry
    cat = {c: round(cat_psi(ref[c], cur[c]), 4) for c in cfg["categorical"]}
    max_psi = max([v["psi"] for c, v in per.items() if c != scale] + list(cat.values()) + [0.0])
    if scale:
        bucket = "SCALE_MISMATCH"
    elif max_psi > PSI_CRIT:
        bucket = "SHAPE_SHIFT"
    else:
        bucket = "PARITY_OK"
    out = {"bucket": bucket, "measurements": {"numeric": per, "categorical_psi": cat,
                                              "max_psi_excluding_rescaled": round(max_psi, 4)}}
    if scale:
        out["measurements"]["suggested_fix"] = {"rescale": {scale: per[scale]["rescale_factor"]}}
    return out


def shift_and_label_check(ws: Workspace, params: dict) -> dict:
    pack = ws.pack()
    target = pack["target"]
    cfg = pack.get("incumbent_pipeline") or pack["pipeline"]
    ref, cur = ws.reference(), ws.current()
    n_lab = int(cur[target].notna().sum())
    shifts = {c: round(psi(ref[c], cur[c]), 4) for c in cfg["numeric"]}
    shifts.update({c: round(cat_psi(ref[c], cur[c]), 4) for c in cfg["categorical"]})
    max_psi = max(shifts.values())
    meas = {"labelled_rows": n_lab, "psi": shifts, "max_psi": max_psi}
    if n_lab < 50:
        return {"bucket": "UNLABELED", "measurements": meas}
    d = float(cur[target].dropna().mean() - ref[target].mean())
    meas["prevalence_delta"] = round(d, 4)
    if abs(d) > 0.06:
        bucket = "PRIOR_SHIFT"
    elif max_psi > PSI_CRIT:
        bucket = "COVARIATE_SHIFT"
    else:
        bucket = "STABLE"
    return {"bucket": bucket, "measurements": meas}


def threshold_study(ws: Workspace, params: dict) -> dict:
    pack = ws.pack()
    target, metric = pack["target"], pack["primary_metric"]
    inc = ws.incumbent()
    sel, rv = ws.rows("selection_validation"), ws.rows("ref_validation")
    if len(sel) < 50 or sel[target].nunique() < 2:
        return {"bucket": "INSUFFICIENT_LABELS", "measurements": {"selection_rows": len(sel)}}
    ps, pr = mlcore.predict_proba(inc, sel), mlcore.predict_proba(inc, rv)
    auc_cur = mlcore.score(sel[target], ps, inc.threshold)["roc_auc"]
    auc_ref = mlcore.score(rv[target], pr, inc.threshold)["roc_auc"]
    best = mlcore.best_threshold(sel[target].to_numpy(), ps, metric)
    at_serv = mlcore.score(sel[target], ps, inc.threshold)[metric]
    at_best = mlcore.score(sel[target], ps, best)[metric]
    meas = {"auc_reference": auc_ref, "auc_current": auc_cur, "auc_drop": round(auc_ref - auc_cur, 4),
            "serving_threshold": inc.threshold, "best_validation_threshold": best,
            f"{metric}_at_serving": at_serv, f"{metric}_at_best": at_best,
            "gain_from_threshold": round(at_best - at_serv, 4)}
    if auc_ref - auc_cur > 0.05:
        bucket = "RANKING_DEGRADED"
    elif at_best - at_serv >= 0.03:
        bucket = "RANKING_INTACT_THRESHOLD_OFF"
    else:
        bucket = "OPERATING_POINT_OK"
    return {"bucket": bucket, "measurements": meas}


def segment_failure_audit(ws: Workspace, params: dict) -> dict:
    pack = ws.pack()
    target, seg = pack["target"], pack.get("critical_segment")
    if not seg:
        return {"bucket": "NOT_APPLICABLE", "measurements": {"reason": "no segment column declared"}}
    inc = ws.incumbent()
    sel, rv = ws.rows("selection_validation"), ws.rows("ref_validation")
    if len(sel) < 50:
        return {"bucket": "INSUFFICIENT_LABELS", "measurements": {"selection_rows": len(sel)}}
    ps, pr = mlcore.predict_proba(inc, sel), mlcore.predict_proba(inc, rv)
    drops = {}
    for v in sorted(sel[seg["column"]].dropna().astype(str).unique()):
        ms = (sel[seg["column"]].astype(str) == v).to_numpy()
        mr = (rv[seg["column"]].astype(str) == v).to_numpy()
        if ms.sum() < 30 or mr.sum() < 30:
            continue
        drops[v] = round(mlcore.score(rv[target][mr], pr[mr], inc.threshold)[seg["metric"]]
                         - mlcore.score(sel[target][ms], ps[ms], inc.threshold)[seg["metric"]], 4)
    vals = sorted(drops.values())
    concentrated = len(vals) > 1 and vals[-1] > 0.15 and vals[0] < 0.05
    return {"bucket": "CONCENTRATED" if concentrated else "UNIFORM",
            "measurements": {"metric": seg["metric"], "drop_by_segment": drops}}


def dependency_api_probe(ws: Workspace, params: dict) -> dict:
    obj, kw = params["object"], params["keyword"]
    names = _installed_params(obj)
    import sklearn
    return {"bucket": "API_COMPATIBLE" if kw in names else "PARAM_REMOVED",
            "measurements": {"object": obj, "keyword": kw, "installed_sklearn": sklearn.__version__,
                             "installed_parameters": names}}


# ── Correctives ───────────────────────────────────────────────────────────────
def _validation(ws: Workspace, cand: mlcore.Candidate) -> dict:
    pack = ws.pack()
    sel = ws.rows("selection_validation")
    if len(sel) < 20 or sel[pack["target"]].nunique() < 2:
        return {}
    return mlcore.score(sel[pack["target"]], mlcore.predict_proba(cand, sel), cand.threshold)


def preprocessing_ablation(ws: Workspace, params: dict, exp_id: str) -> dict:
    inc = ws.incumbent()
    cand = mlcore.Candidate(inc.model_path, inc.threshold, input_fix={"rescale": params["rescale"]},
                            label="incumbent + input rescale")
    return {"bucket": "CANDIDATE_BUILT", "candidate": cand.to_dict(),
            "measurements": {"self_reported_validation": _validation(ws, cand)}}


def threshold_candidate(ws: Workspace, params: dict, exp_id: str) -> dict:
    pack = ws.pack()
    inc = ws.incumbent()
    sel = ws.rows("selection_validation")
    best = mlcore.best_threshold(sel[pack["target"]].to_numpy(), mlcore.predict_proba(inc, sel),
                                 pack["primary_metric"])
    cand = mlcore.Candidate(inc.model_path, best, label=f"incumbent @ threshold {best}")
    return {"bucket": "CANDIDATE_BUILT", "candidate": cand.to_dict(),
            "measurements": {"chosen_on": "selection_validation", "threshold": best,
                             "self_reported_validation": _validation(ws, cand)}}


def _retrain(ws: Workspace, cfg: dict, exp_id: str, label: str, retune_threshold: bool = True) -> dict:
    import joblib
    pack = ws.pack()
    train, sel = ws.rows("train"), ws.rows("selection_validation")
    feats = cfg["numeric"] + cfg["categorical"]
    model = mlcore.build_pipeline(cfg)
    model.fit(train[feats], train[pack["target"]].astype(int))
    path = ws.model_path(exp_id)
    joblib.dump(model, path)
    thr = float(cfg["threshold"]) if retune_threshold else ws.incumbent().threshold
    if retune_threshold and len(sel) >= 50 and sel[pack["target"]].nunique() > 1:
        thr = mlcore.best_threshold(sel[pack["target"]].to_numpy(), model.predict_proba(sel[feats])[:, 1],
                                    pack["primary_metric"])
    cand = mlcore.Candidate(path, thr, label=label)
    return {"bucket": "CANDIDATE_BUILT", "candidate": cand.to_dict(),
            "measurements": {"artifact_sha256": mlcore.sha256_file(path), "threshold": thr,
                             "self_reported_validation": _validation(ws, cand)}}


def class_weight_challenger(ws: Workspace, params: dict, exp_id: str) -> dict:
    pack = ws.pack()
    cw = params["class_weight"]
    cfg = dict(pack["pipeline"],          # the deployed retrain configuration
               class_weight=cw if cw == "balanced" else {0: 1.0, 1: float(cw)})
    return _retrain(ws, cfg, exp_id, f"class_weight={cw}")


def model_family_challenger(ws: Workspace, params: dict, exp_id: str) -> dict:
    pack = ws.pack()
    cfg = dict(pack["pipeline"], model=params["model"])   # the deployed retrain configuration
    return _retrain(ws, cfg, exp_id, f"retrain {params['model']}")


def dependency_compat_fix(ws: Workspace, params: dict, exp_id: str) -> dict:
    pack = ws.pack()
    obj, old, new = params["object"], params["from"], params["to"]
    if new not in _installed_params(obj):
        raise ValueError(f"{new!r} is not a parameter of installed {obj}")
    cfg = dict(pack["pipeline"])
    oh = dict(cfg.get("onehot", {}))
    if obj == "OneHotEncoder" and old in oh:
        oh[new] = oh.pop(old)
    cfg["onehot"] = oh
    # Change only what the cited source justifies: same data, same model, same serving
    # threshold. Re-tuning the threshold here would mix a second change into the fix.
    out = _retrain(ws, cfg, exp_id, f"retrain with {obj}({new}=…)", retune_threshold=False)
    out["measurements"]["config_change"] = {"object": obj, "from": old, "to": new}
    return out


def challenger_review(ws: Workspace, params: dict, exp_id: str) -> dict:
    pack = ws.pack()
    ch = pack["challenger"]
    thr = float(pack["pipeline"]["threshold"])
    cand = mlcore.Candidate(ws.model_path("challenger"), thr, features=ch["features"], label=ch["name"])
    return {"bucket": "CANDIDATE_BUILT", "candidate": cand.to_dict(),
            "measurements": {"self_reported_validation": _validation(ws, cand),
                             "declared_split": ch["split"], "claimed_metric": ch["claimed_metric"]}}


# ── Registry ──────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class Template:
    template_id: str
    kind: str                      # diagnostic | corrective
    cost_units: float              # relative CPU-equivalent estimate (planning only)
    buckets: tuple[str, ...]
    requires_labels: bool
    description: str
    fn: Callable[..., dict]
    needs_source: bool = False


def _p_none(params, ctx):
    return (True, "", {}) if not params else (False, "template takes no parameters", None)


def _p_probe(params, ctx):
    obj, kw = params.get("object"), params.get("keyword")
    if not is_probe_object(obj) or not _ident(kw):
        return False, "object must be an allow-listed sklearn class and keyword an identifier", None
    return True, "", {"object": obj, "keyword": kw}


def _p_rescale(params, ctx):
    rs = params.get("rescale")
    if not isinstance(rs, dict) or len(rs) != 1:
        return False, "rescale must map exactly one column to a factor", None
    (col, factor), = rs.items()
    if col not in ctx["numeric"]:
        return False, f"{col!r} is not a numeric contract feature", None
    try:
        f = float(factor)
        k = math.log10(f)
    except (TypeError, ValueError):
        return False, "factor must be a number", None
    if abs(k - round(k)) > 1e-9 or not 1 <= abs(round(k)) <= 6:
        return False, "factor must be a power of ten between 1e-6 and 1e6 (unit fix only)", None
    return True, "", {"rescale": {col: f}}


def _p_threshold(params, ctx):
    return (True, "", {}) if not params else (False, "threshold is chosen on validation, not supplied", None)


def _p_cw(params, ctx):
    cw = params.get("class_weight")
    if cw == "balanced" or cw in (2, 3, 4, 2.0, 3.0, 4.0):
        return True, "", {"class_weight": cw}
    return False, "class_weight must be 'balanced' or 2/3/4", None


def _p_family(params, ctx):
    m = params.get("model")
    if m in ("hist_gradient_boosting", "logistic_regression"):
        return True, "", {"model": m}
    return False, "model must be hist_gradient_boosting or logistic_regression", None


def _p_compat(params, ctx):
    obj, old, new = params.get("object"), params.get("from"), params.get("to")
    if not is_probe_object(obj) or not (_ident(old) and _ident(new)) or old == new:
        return False, "object/from/to must be an allow-listed class and two distinct identifiers", None
    return True, "", {"object": obj, "from": old, "to": new}


TEMPLATES: dict[str, Template] = {t.template_id: t for t in [
    Template("CONTRACT_DIAGNOSTIC", "diagnostic", 1.0, ("CONTRACT_OK", "CONTRACT_BROKEN"), False,
             "Schema, null surge, unseen categories, type coercion — read-only.", contract_diagnostic),
    Template("TRAIN_SERVE_PARITY", "diagnostic", 2.0, ("PARITY_OK", "SCALE_MISMATCH", "SHAPE_SHIFT"), False,
             "Compares serving inputs to training inputs; detects unit/scale contract breaks.", train_serve_parity),
    Template("SHIFT_AND_LABEL_CHECK", "diagnostic", 2.0, ("STABLE", "COVARIATE_SHIFT", "PRIOR_SHIFT", "UNLABELED"),
             False, "Separates covariate shift from label-prior shift; needs labels for quality.",
             shift_and_label_check),
    Template("THRESHOLD_STUDY", "diagnostic", 2.0,
             ("RANKING_INTACT_THRESHOLD_OFF", "RANKING_DEGRADED", "OPERATING_POINT_OK", "INSUFFICIENT_LABELS"),
             True, "Is ranking (AUC) intact while the operating point is wrong?", threshold_study),
    Template("SEGMENT_FAILURE_AUDIT", "diagnostic", 2.0, ("UNIFORM", "CONCENTRATED", "INSUFFICIENT_LABELS", "NOT_APPLICABLE"),
             True, "Does the failure concentrate in one segment?", segment_failure_audit),
    Template("DEPENDENCY_API_PROBE", "diagnostic", 0.5, ("API_COMPATIBLE", "PARAM_REMOVED"), False,
             "Inspects the INSTALLED library signature for a parameter.", dependency_api_probe),
    Template("PREPROCESSING_ABLATION", "corrective", 3.0, ("CANDIDATE_BUILT",), True,
             "Incumbent model with a power-of-ten unit fix on one input.", preprocessing_ablation),
    Template("THRESHOLD_CANDIDATE", "corrective", 2.0, ("CANDIDATE_BUILT",), True,
             "Incumbent model with a validation-chosen threshold.", threshold_candidate),
    Template("CLASS_WEIGHT_CHALLENGER", "corrective", 6.0, ("CANDIDATE_BUILT",), True,
             "Retrain with bounded class weights.", class_weight_challenger),
    Template("MODEL_FAMILY_CHALLENGER", "corrective", 10.0, ("CANDIDATE_BUILT",), True,
             "Full retrain with another approved estimator.", model_family_challenger),
    Template("DEPENDENCY_COMPAT_FIX", "corrective", 6.0, ("CANDIDATE_BUILT",), True,
             "Retrain with a renamed library parameter; requires a cited source.", dependency_compat_fix,
             needs_source=True),
    Template("CHALLENGER_REVIEW", "corrective", 1.0, ("CANDIDATE_BUILT",), True,
             "Score a submitted challenger under the frozen protocol.", challenger_review),
]}

PARAM_VALIDATORS = {
    "CONTRACT_DIAGNOSTIC": _p_none, "TRAIN_SERVE_PARITY": _p_none, "SHIFT_AND_LABEL_CHECK": _p_none,
    "THRESHOLD_STUDY": _p_none, "SEGMENT_FAILURE_AUDIT": _p_none, "DEPENDENCY_API_PROBE": _p_probe,
    "PREPROCESSING_ABLATION": _p_rescale, "THRESHOLD_CANDIDATE": _p_threshold,
    "CLASS_WEIGHT_CHALLENGER": _p_cw, "MODEL_FAMILY_CHALLENGER": _p_family,
    "DEPENDENCY_COMPAT_FIX": _p_compat, "CHALLENGER_REVIEW": _p_none,
}


def validate_params(template_id: str, params: dict, ctx: dict) -> tuple[bool, str, dict | None]:
    if not isinstance(params, dict):
        return False, "parameters must be an object", None
    return PARAM_VALIDATORS[template_id](params, ctx)


def execute(template_id: str, ws_root: str, params: dict, exp_id: str) -> dict:
    """Entry point used by the runner (inline or in a child process)."""
    t = TEMPLATES[template_id]
    ws = Workspace(ws_root)
    res = t.fn(ws, params) if t.kind == "diagnostic" else t.fn(ws, params, exp_id)
    if res["bucket"] not in t.buckets:
        raise RuntimeError(f"template returned unknown bucket {res['bucket']!r}")
    return res

