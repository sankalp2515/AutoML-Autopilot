"""Small, deterministic ML primitives shared by templates and the verifier.

A *serving candidate* is everything that turns raw rows into decisions: a fitted
model artifact, an input fix (e.g. rescale one column), and a threshold. The
incumbent and every correction are expressed the same way, so the verifier scores
them with one code path on one frozen protocol.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

METRICS = ("f1", "recall", "precision", "roc_auc", "balanced_accuracy")
METRIC_IMPL_VERSION = "sklearn-metrics/1"


def build_pipeline(cfg: dict[str, Any], features: list[str] | None = None):
    """Construct the sklearn pipeline from config. Uses the config verbatim, so an
    outdated keyword (e.g. OneHotEncoder(sparse=...)) raises the library's REAL
    error — the T01 incident is reproduced, not simulated."""
    from sklearn.compose import ColumnTransformer
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import OneHotEncoder, StandardScaler

    numeric = [c for c in cfg["numeric"] if features is None or c in features]
    extra = [c for c in (features or []) if c not in cfg["numeric"] and c not in cfg["categorical"]]
    categorical = [c for c in cfg["categorical"] if features is None or c in features]
    num_pipe = Pipeline([("impute", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    cat_pipe = Pipeline([("impute", SimpleImputer(strategy="most_frequent")),
                         ("onehot", OneHotEncoder(**cfg.get("onehot", {})))])
    pre = ColumnTransformer([("num", num_pipe, numeric + extra), ("cat", cat_pipe, categorical)])
    if cfg.get("model") == "hist_gradient_boosting":
        est = HistGradientBoostingClassifier(max_iter=200, learning_rate=0.08, random_state=0,
                                             class_weight=cfg.get("class_weight"))
    else:
        est = LogisticRegression(max_iter=2000, C=1.0, class_weight=cfg.get("class_weight"))
    return Pipeline([("pre", pre), ("model", est)])


@dataclass
class Candidate:
    model_path: str
    threshold: float
    input_fix: dict[str, Any] = field(default_factory=dict)   # {"rescale": {"income": 1000.0}}
    features: list[str] | None = None
    label: str = "candidate"

    def to_dict(self) -> dict[str, Any]:
        return {"model_path": self.model_path, "threshold": self.threshold, "input_fix": self.input_fix,
                "features": self.features, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Candidate":
        return cls(**d)


def apply_input_fix(df: pd.DataFrame, fix: dict[str, Any]) -> pd.DataFrame:
    if not fix:
        return df
    out = df.copy()
    for col, factor in (fix.get("rescale") or {}).items():
        out[col] = pd.to_numeric(out[col], errors="coerce") * float(factor)
    return out


def predict_proba(cand: Candidate, df: pd.DataFrame) -> np.ndarray:
    import joblib
    model = joblib.load(cand.model_path)
    X = apply_input_fix(df, cand.input_fix)
    return model.predict_proba(X)[:, 1]


def score(y: np.ndarray, proba: np.ndarray, threshold: float) -> dict[str, float]:
    from sklearn.metrics import (balanced_accuracy_score, f1_score, precision_score,
                                 recall_score, roc_auc_score)
    y = np.asarray(y).astype(int)
    pred = (proba >= threshold).astype(int)
    out = {"f1": f1_score(y, pred, zero_division=0), "recall": recall_score(y, pred, zero_division=0),
           "precision": precision_score(y, pred, zero_division=0),
           "balanced_accuracy": balanced_accuracy_score(y, pred) if len(np.unique(y)) > 1 else float("nan")}
    out["roc_auc"] = roc_auc_score(y, proba) if len(np.unique(y)) > 1 else float("nan")
    return {k: round(float(v), 4) for k, v in out.items()}


def best_threshold(y: np.ndarray, proba: np.ndarray, metric: str = "f1") -> float:
    grid = np.round(np.arange(0.05, 0.951, 0.01), 2)
    vals = [score(y, proba, t)[metric] for t in grid]
    return float(grid[int(np.nanargmax(vals))])


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, default=str).encode())


def make_protocol(reference: pd.DataFrame, current: pd.DataFrame, target: str, seed: int,
                  metric: str) -> dict[str, Any]:
    """Freeze split roles BEFORE any fitting. Reference → train/ref_validation
    (stratified). Labelled current rows → selection_validation / final_test by a
    keyed hash of the row id, so assignment is reproducible and independent of
    row order."""
    from sklearn.model_selection import train_test_split
    ref_idx = np.arange(len(reference))
    tr, rv = train_test_split(ref_idx, test_size=0.2, random_state=seed, stratify=reference[target])
    labelled = np.where(current[target].notna().to_numpy())[0]
    keyed = [int(hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()[:8], 16) % 2 for i in labelled]
    sel = labelled[np.array(keyed, dtype=int) == 0] if len(labelled) else labelled
    fin = labelled[np.array(keyed, dtype=int) == 1] if len(labelled) else labelled
    roles = {"train": sorted(tr.tolist()), "ref_validation": sorted(rv.tolist()),
             "selection_validation": sorted(sel.tolist()), "final_test": sorted(fin.tolist())}
    manifest = {"version": 1, "seed": seed, "metric": metric, "target": target,
                "keying": "sha256(seed:row_index) mod 2 over labelled current rows",
                "counts": {k: len(v) for k, v in roles.items()}}
    manifest["protocol_hash"] = canonical_hash({"roles": roles, "seed": seed, "metric": metric})
    return {"manifest": manifest, "roles": roles}


def _fast(y: np.ndarray, pred: np.ndarray, metric: str) -> float:
    tp = int(((pred == 1) & (y == 1)).sum())
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    tn = int(((pred == 0) & (y == 0)).sum())
    rec = tp / (tp + fn) if tp + fn else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    if metric == "recall":
        return rec
    if metric == "precision":
        return prec
    if metric == "balanced_accuracy":
        spec = tn / (tn + fp) if tn + fp else 0.0
        return (rec + spec) / 2
    return 2 * prec * rec / (prec + rec) if prec + rec else 0.0


def bootstrap_delta(y: np.ndarray, p_a: np.ndarray, t_a: float, p_b: np.ndarray, t_b: float,
                    metric: str, n_boot: int = 400, seed: int = 0) -> dict[str, float]:
    """Paired bootstrap of metric(b) - metric(a) on the same rows."""
    if metric not in ("f1", "recall", "precision", "balanced_accuracy"):
        metric = "f1"   # threshold-free metrics are not bootstrapped here
    rng = np.random.default_rng(seed)
    y = np.asarray(y).astype(int)
    a = (np.asarray(p_a) >= t_a).astype(int)
    b = (np.asarray(p_b) >= t_b).astype(int)
    n = len(y)
    deltas = []
    for _ in range(n_boot):
        idx = rng.integers(0, n, n)
        if len(np.unique(y[idx])) < 2:
            continue
        deltas.append(_fast(y[idx], b[idx], metric) - _fast(y[idx], a[idx], metric))
    point = score(y, p_b, t_b)[metric] - score(y, p_a, t_a)[metric]
    lo, hi = (np.percentile(deltas, [2.5, 97.5]) if deltas else (float("nan"), float("nan")))
    return {"delta": round(float(point), 4), "ci_low": round(float(lo), 4), "ci_high": round(float(hi), 4),
            "n_boot": len(deltas)}
