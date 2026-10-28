"""The Skeptic's arsenal — deterministic trust/falsification probes.

This module is STANDALONE on purpose: it imports only pandas/numpy/sklearn (all
present in the sandbox image) and nothing from `app`, because the TrustAuditor
ships its *source* into the sandbox to run against the real model + data. The
same functions are imported directly by unit tests (which `importorskip` sklearn).

Each probe is a pure function over in-memory objects and returns a structured
verdict via `_result(...)`. `run_all(...)` does the file I/O, computes shared
artifacts (CV predictions) once, calls every applicable probe defensively (a
crashing probe becomes status="error", never taking down the report), and grades.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

SCHEMA_VERSION = 1
SAMPLE_CAP = 20000          # probes run on at most this many rows (scalability)
CV_FOLDS = 3
BOOTSTRAP_K = 5

# severity → penalty applied to the 0-100 trust score
_FAIL_PENALTY = {"critical": 45, "high": 25, "medium": 15, "low": 8, "info": 0}
_WARN_PENALTY = {"critical": 15, "high": 10, "medium": 6, "low": 3, "info": 0}


def _result(pid, category, title, severity, status, headline,
            evidence=None, recommendation="", score=None):
    return {
        "id": pid, "category": category, "title": title, "severity": severity,
        "status": status, "headline": headline, "evidence": evidence or {},
        "recommendation": recommendation, "score": score,
    }


def _is_classification(task_type: str) -> bool:
    t = (task_type or "").lower()
    return ("class" in t or "label" in t) and "multilabel" not in t


def _score(y_true, y_pred, task_type: str) -> float:
    from sklearn.metrics import f1_score, r2_score
    if _is_classification(task_type):
        return float(f1_score(y_true, y_pred, average="weighted", zero_division=0))
    return float(r2_score(y_true, y_pred))


# ── Probe 1: data integrity ───────────────────────────────────────────────────
def probe_data_integrity(X: pd.DataFrame, y, task_type: str):
    n, p = X.shape
    missing = float(np.isnan(X.to_numpy(dtype="float64", na_value=np.nan)).mean()) if p else 0.0
    const_cols = [c for c in X.columns if X[c].nunique(dropna=False) <= 1]
    dup_rows = int(X.duplicated().sum())
    target_nans = int(pd.isna(pd.Series(y)).sum())
    ev = {"rows": int(n), "features": int(p), "missing_fraction": round(missing, 4),
          "constant_features": len(const_cols), "duplicate_rows": dup_rows, "target_nans": target_nans}
    if _is_classification(task_type):
        vals, counts = np.unique(np.asarray(y), return_counts=True)
        ev["majority_class_fraction"] = round(float(counts.max() / counts.sum()), 4)
        ev["n_classes"] = int(len(vals))
    status, sev, head, rec = "pass", "low", "No structural data problems found.", ""
    if target_nans > 0:
        status, sev, head = "fail", "high", f"{target_nans} rows have a missing target."
        rec = "Drop or impute rows with a null target before training."
    elif missing > 0.2 or len(const_cols) > 0:
        status, sev = "warn", "medium"
        head = f"{round(missing*100,1)}% missing cells; {len(const_cols)} constant feature(s)."
        rec = "Constant features add no signal; high missingness may bias the model."
    elif dup_rows > 0.05 * max(n, 1):
        status, sev, head = "warn", "low", f"{dup_rows} duplicate rows ({round(dup_rows/n*100,1)}%)."
        rec = "Duplicate rows can inflate CV scores; consider de-duplicating."
    return _result("data_integrity", "Data", "Data integrity", sev, status, head, ev, rec)


# ── Probe 2: significance vs the noise floor ──────────────────────────────────
def probe_significance(baseline, final, score_std, primary_metric):
    if baseline is None or final is None:
        return _result("significance", "Evaluation", "Significant improvement", "low", "skip",
                       "No baseline recorded to compare against.")
    lower_better = bool(_lower_better(primary_metric))
    lift = (baseline - final) if lower_better else (final - baseline)
    noise = float(score_std or 0.0)
    ev = {"baseline": round(float(baseline), 4), "final": round(float(final), 4),
          "lift": round(float(lift), 4), "cv_noise_std": round(noise, 4),
          "lower_is_better": lower_better}
    if lift <= 0:
        return _result("significance", "Evaluation", "Significant improvement", "high", "fail",
                       "The final model is no better than the baseline.", ev,
                       "Investigate — tuning/feature work added no real value.")
    if noise > 0 and lift < noise:
        return _result("significance", "Evaluation", "Significant improvement", "high", "fail",
                       "Improvement over baseline is within CV noise.", ev,
                       "The gain may be luck; widen CV or treat as not significant.")
    if noise > 0 and lift < 2 * noise:
        return _result("significance", "Evaluation", "Significant improvement", "medium", "warn",
                       "Improvement is positive but under 2x the noise floor.", ev,
                       "Confirm with more folds / a larger hold-out before trusting it.")
    return _result("significance", "Evaluation", "Significant improvement", "low", "pass",
                   "Improvement over baseline clears the noise floor.", ev)


def _lower_better(metric):
    import re
    return bool(re.search(r"log.?loss|rmse|\bmae\b|mape|smape|\bmse\b|error|drawdown|hamming", (metric or ""), re.I))


# ── Probe 3: evaluation honesty (overfit / lucky split / weak basis) ──────────
def probe_eval_honesty(train_score, cv_score, holdout_score, noise, basis, primary_metric):
    ev = {"train_score": _r(train_score), "cv_score": _r(cv_score),
          "holdout_score": _r(holdout_score), "evaluation_basis": basis}
    if train_score is None or cv_score is None:
        return _result("eval_honesty", "Evaluation", "Honest evaluation", "low", "skip",
                       "Not enough scores to cross-check the evaluation.", ev)
    gap = float(train_score - cv_score)            # higher-better metrics: train should be >= cv
    ev["train_minus_cv_gap"] = round(gap, 4)
    floor = max(2 * float(noise or 0.0), 0.05)
    status, sev, head, rec = "pass", "low", "Train, CV and hold-out scores are consistent.", ""
    if basis == "in_sample_split":
        status, sev = "warn", "medium"
        head = "Scored on an in-sample split — no untouched hold-out."
        rec = "Dataset was too small for a clean hold-out; treat the score as mildly optimistic."
    if gap > floor and abs(gap) > 0.1:
        status, sev = "fail", "high"
        head = f"Large train-vs-CV gap ({round(gap,3)}) — the model is overfitting."
        rec = "Regularize, simplify the model, or get more data."
    elif holdout_score is not None and cv_score is not None and (
            (_lower_better(primary_metric) and holdout_score < cv_score - floor) or
            (not _lower_better(primary_metric) and holdout_score > cv_score + floor)):
        status, sev = "warn", "medium"
        head = "Hold-out score is suspiciously better than CV — possibly a lucky split or leak."
        rec = "Re-run with a different split seed; check the leakage probe."
    return _result("eval_honesty", "Evaluation", "Honest evaluation", sev, status, head, ev, rec)


def _r(v):
    return None if v is None else round(float(v), 4)


# ── Probe 4: target leakage ───────────────────────────────────────────────────
def probe_leakage(X: pd.DataFrame, y, task_type: str):
    yarr = np.asarray(y)
    yv = pd.Series(yarr.ravel() if yarr.ndim == 1 else yarr[:, 0]).reset_index(drop=True)
    try:
        ycode = (yv.astype("float64").to_numpy() if not _is_classification(task_type)
                 else pd.factorize(yv)[0].astype("float64"))
    except Exception:
        ycode = pd.factorize(yv)[0].astype("float64")
    suspicious = []
    for c in X.columns:
        col = pd.to_numeric(X[c], errors="coerce")
        if col.nunique(dropna=True) <= 1:
            continue
        r = np.corrcoef(col.fillna(col.median()).to_numpy(), ycode)[0, 1]
        if np.isfinite(r) and abs(r) >= 0.9:
            suspicious.append({"feature": str(c), "abs_corr_with_target": round(abs(float(r)), 4)})
    suspicious.sort(key=lambda d: -d["abs_corr_with_target"])
    ev = {"suspicious_features": suspicious[:10], "n_suspicious": len(suspicious)}
    leaks = [s for s in suspicious if s["abs_corr_with_target"] >= 0.98]
    if leaks:
        return _result("leakage", "Leakage", "Target leakage", "critical", "fail",
                       f"{len(leaks)} feature(s) almost perfectly predict the target — likely leakage.",
                       ev, "Remove these features; they encode the answer and won't exist at inference.")
    if suspicious:
        return _result("leakage", "Leakage", "Target leakage", "high", "warn",
                       f"{len(suspicious)} feature(s) are very strongly correlated with the target.",
                       ev, "Verify these are legitimately available before prediction time.")
    return _result("leakage", "Leakage", "Target leakage", "low", "pass",
                   "No feature is suspiciously predictive of the target.", ev)


# ── Probe 5: feature stability + reliance ─────────────────────────────────────
def probe_feature_stability(model, X: pd.DataFrame, y, task_type: str):
    imp = _importances(model, X)
    if imp is None:
        return _result("feature_stability", "Robustness", "Feature stability", "low", "skip",
                       "Model exposes no feature importances to analyze.")
    top_feat = X.columns[int(np.argmax(imp))]
    share = float(np.max(imp) / (np.sum(np.abs(imp)) + 1e-12))
    ev = {"top_feature": str(top_feat), "top_feature_importance_share": round(share, 4)}
    # Bootstrap rank stability of the #1 feature.
    from sklearn.base import clone
    tops = []
    rng = np.random.RandomState(0)
    for _ in range(BOOTSTRAP_K):
        idx = rng.randint(0, len(X), len(X))
        try:
            m = clone(model).fit(X.iloc[idx], np.asarray(y)[idx])
            bi = _importances(m, X)
            if bi is not None:
                tops.append(X.columns[int(np.argmax(bi))])
        except Exception:
            continue
    if tops:
        stable = sum(1 for t in tops if t == top_feat) / len(tops)
        ev["top_feature_stability"] = round(stable, 3)
    else:
        stable = 1.0
    if share > 0.9:
        return _result("feature_stability", "Robustness", "Feature stability", "high", "warn",
                       f"The model leans almost entirely on one feature ('{top_feat}').", ev,
                       "Single-feature reliance is fragile and often a sign of leakage.")
    if stable < 0.6:
        return _result("feature_stability", "Robustness", "Feature stability", "medium", "warn",
                       "The most important feature changes across resamples — unstable signal.", ev,
                       "Importances are noisy; don't over-interpret the top feature.")
    return _result("feature_stability", "Robustness", "Feature stability", "low", "pass",
                   "Feature importances are stable across resamples.", ev)


def _importances(model, X):
    if hasattr(model, "feature_importances_"):
        v = np.asarray(model.feature_importances_, dtype="float64")
    elif hasattr(model, "coef_"):
        c = np.asarray(model.coef_, dtype="float64")
        v = np.abs(c).mean(axis=0) if c.ndim > 1 else np.abs(c)
    else:
        return None
    return v if v.shape[0] == X.shape[1] else None


# ── Probe 6: worst-slice discovery ────────────────────────────────────────────
def probe_worst_slice(X: pd.DataFrame, y, y_pred, task_type: str):
    if y_pred is None:
        return _result("worst_slice", "Robustness", "Worst-slice performance", "low", "skip",
                       "No cross-validated predictions available for slicing.")
    overall = _score(y, y_pred, task_type)
    yv, pv = np.asarray(y), np.asarray(y_pred)
    worst = []
    # one-hot / binary columns recover the original categorical slices
    bin_cols = [c for c in X.columns if set(pd.unique(pd.to_numeric(X[c], errors="coerce").dropna())) <= {0, 1}]
    cand = bin_cols[:40]
    for c in cand:
        mask = pd.to_numeric(X[c], errors="coerce").fillna(0).to_numpy() == 1
        if mask.sum() < 30:
            continue
        s = _score(yv[mask], pv[mask], task_type)
        worst.append({"slice": str(c), "n": int(mask.sum()), "score": round(float(s), 4),
                      "delta_vs_overall": round(float(s - overall), 4)})
    worst.sort(key=lambda d: d["delta_vs_overall"])
    ev = {"overall_score": round(float(overall), 4), "worst_slices": worst[:5]}
    bad = [w for w in worst if w["delta_vs_overall"] < -0.15]
    if bad:
        return _result("worst_slice", "Robustness", "Worst-slice performance", "medium", "warn",
                       f"{len(bad)} cohort(s) perform >15% worse than overall.", ev,
                       "The model is unreliable on these subgroups — inspect fairness/coverage.")
    return _result("worst_slice", "Robustness", "Worst-slice performance", "low", "pass",
                   "No cohort underperforms the overall score by a wide margin.", ev)


# ── Probe 7: distribution-shift fragility ─────────────────────────────────────
def probe_shift_fragility(model, X: pd.DataFrame, y, task_type: str):
    try:
        Xn = X.apply(lambda s: pd.to_numeric(s, errors="coerce")).fillna(0.0)
        base = _score(y, model.predict(Xn), task_type)
        rng = np.random.RandomState(0)
        stds = Xn.std(axis=0).replace(0, 1.0).to_numpy()
        noisy = Xn + rng.normal(0, 0.1 * stds, size=Xn.shape)
        noisy_score = _score(y, model.predict(noisy), task_type)
        drop = (base - noisy_score) / (abs(base) + 1e-9)
        ev = {"clean_score": round(float(base), 4), "noisy_score": round(float(noisy_score), 4),
              "relative_drop": round(float(drop), 4)}
    except Exception as e:
        return _result("shift_fragility", "Robustness", "Distribution-shift fragility", "low", "skip",
                       f"Could not run the perturbation test ({type(e).__name__}).")
    if drop > 0.2:
        return _result("shift_fragility", "Robustness", "Distribution-shift fragility", "high", "fail",
                       f"Score drops {round(drop*100,1)}% under mild noise — brittle to data shift.", ev,
                       "Add regularization/augmentation; monitor drift closely in production.")
    if drop > 0.1:
        return _result("shift_fragility", "Robustness", "Distribution-shift fragility", "medium", "warn",
                       f"Score drops {round(drop*100,1)}% under mild noise.", ev,
                       "Some sensitivity to shift — keep the drift monitor on.")
    return _result("shift_fragility", "Robustness", "Distribution-shift fragility", "low", "pass",
                   "Predictions are stable under mild input perturbation.", ev)


# ── Probe 8: probability calibration ──────────────────────────────────────────
def probe_calibration(y, proba, task_type: str):
    if not _is_classification(task_type) or proba is None:
        return _result("calibration", "Evaluation", "Probability calibration", "low", "skip",
                       "Calibration applies to classifiers with probability output.")
    try:
        p = np.asarray(proba)
        p = p[:, 1] if p.ndim == 2 and p.shape[1] == 2 else (p.max(axis=1) if p.ndim == 2 else p)
        yv = np.asarray(y).astype("float64")
        if p.ndim != 1 or len(np.unique(yv)) > 2:
            return _result("calibration", "Evaluation", "Probability calibration", "low", "skip",
                           "Calibration shown for binary classifiers.")
        bins = np.linspace(0, 1, 11)
        idx = np.digitize(p, bins) - 1
        ece = 0.0
        for b in range(10):
            m = idx == b
            if m.sum() == 0:
                continue
            ece += (m.sum() / len(p)) * abs(yv[m].mean() - p[m].mean())
        ev = {"expected_calibration_error": round(float(ece), 4)}
    except Exception as e:
        return _result("calibration", "Evaluation", "Probability calibration", "low", "skip",
                       f"Calibration could not be computed ({type(e).__name__}).")
    if ece > 0.1:
        return _result("calibration", "Evaluation", "Probability calibration", "medium", "warn",
                       f"Probabilities are mis-calibrated (ECE={round(ece,3)}).", ev,
                       "Apply Platt/isotonic calibration before trusting predicted probabilities.")
    return _result("calibration", "Evaluation", "Probability calibration", "low", "pass",
                   f"Probabilities are well calibrated (ECE={round(ece,3)}).", ev)


# ── Grading ───────────────────────────────────────────────────────────────────
def grade(probes):
    score = 100.0
    capped = False
    for p in probes:
        if p["status"] == "fail":
            score -= _FAIL_PENALTY.get(p["severity"], 10)
            if p["severity"] == "critical":
                capped = True
        elif p["status"] == "warn":
            score -= _WARN_PENALTY.get(p["severity"], 5)
    score = max(0.0, score)
    if capped:
        score = min(score, 35.0)   # a critical failure can never earn a passing grade
    letter = ("A" if score >= 90 else "B" if score >= 80 else "C" if score >= 70
              else "D" if score >= 55 else "F")
    return round(score, 1), letter


# ── Orchestration (runs inside the sandbox) ───────────────────────────────────
def run_all(enriched_path, model_path, task_type, primary_metric,
            baseline_score=None, final_score=None, score_std=0.0, evaluation_basis="holdout"):
    import ast
    import joblib
    from sklearn.base import clone
    from sklearn.model_selection import cross_val_predict, StratifiedKFold, KFold

    df = pd.read_csv(enriched_path)
    if "__target__" not in df.columns:
        return {"schema_version": SCHEMA_VERSION, "error": "enriched data has no __target__ column",
                "probes": [], "trust_score": None, "grade": "?"}
    if task_type == "multilabel_classification":
        y = np.array(df["__target__"].apply(lambda v: ast.literal_eval(v) if isinstance(v, str) else v).tolist())
    else:
        y = df["__target__"].to_numpy()
    X = df.drop(columns=["__target__"])
    if len(X) > SAMPLE_CAP:
        samp = np.random.RandomState(0).choice(len(X), SAMPLE_CAP, replace=False)
        X, y = X.iloc[samp].reset_index(drop=True), y[samp]

    model = joblib.load(model_path)
    classif = _is_classification(task_type)
    multilabel = task_type == "multilabel_classification"

    # Shared artifacts computed once.
    train_score = cv_score = cv_pred = cv_proba = None
    try:
        train_score = _score(y, model.predict(X), task_type) if not multilabel else None
    except Exception:
        pass
    if not multilabel:
        try:
            k = max(2, min(CV_FOLDS, int(np.min(np.unique(y, return_counts=True)[1])) if classif else CV_FOLDS))
            cv = StratifiedKFold(k, shuffle=True, random_state=0) if classif else KFold(k, shuffle=True, random_state=0)
            cv_pred = cross_val_predict(clone(model), X, y, cv=cv)
            cv_score = _score(y, cv_pred, task_type)
            if classif and hasattr(model, "predict_proba"):
                cv_proba = cross_val_predict(clone(model), X, y, cv=cv, method="predict_proba")
        except Exception:
            pass

    def _safe(fn, *a):
        try:
            return fn(*a)
        except Exception as e:
            return _result(getattr(fn, "_pid", fn.__name__), "Probe", fn.__name__, "low", "error",
                           f"Probe crashed: {type(e).__name__}: {str(e)[:160]}")

    probes = [
        _safe(probe_data_integrity, X, y, task_type),
        _safe(probe_significance, baseline_score, final_score, score_std, primary_metric),
        _safe(probe_eval_honesty, train_score, cv_score, final_score, score_std, evaluation_basis, primary_metric),
    ]
    if not multilabel:
        probes += [
            _safe(probe_leakage, X, y, task_type),
            _safe(probe_feature_stability, model, X, y, task_type),
            _safe(probe_worst_slice, X, y, cv_pred, task_type),
            _safe(probe_shift_fragility, model, X, y, task_type),
            _safe(probe_calibration, y, cv_proba, task_type),
        ]
    score, letter = grade(probes)
    return {
        "schema_version": SCHEMA_VERSION,
        "task_type": task_type,
        "primary_metric": primary_metric,
        "trust_score": score,
        "grade": letter,
        "n_pass": sum(1 for p in probes if p["status"] == "pass"),
        "n_warn": sum(1 for p in probes if p["status"] == "warn"),
        "n_fail": sum(1 for p in probes if p["status"] == "fail"),
        "n_skip": sum(1 for p in probes if p["status"] in ("skip", "error")),
        "probes": probes,
    }
