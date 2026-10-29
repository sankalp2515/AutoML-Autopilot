"""ML oracle (card K3): does the patched repository still behave correctly as an ML system?

Protocol: predictions out, labels in.
    * The candidate's own train and predict commands run inside a sandbox built from the
      pristine base snapshot plus the frozen diff. They see holdout FEATURES only.
    * The oracle reads the predictions file back, validates it, and only then joins the
      sealed labels on the oracle side. Labels never enter a sandbox and the oracle never
      loads a candidate artifact (unpickling one would run candidate code here).
    * Gates that can reject without labels (pipeline runs, schema, train/serve parity,
      prediction-time availability) run BEFORE the single-use holdout permit is consumed.

Segment guardrail metric is recall (the miss rate a protected group experiences),
whatever the task metric is; a group needs ≥30 rows with ≥5 positives and ≥5 negatives
to be judged.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Protocol

import numpy as np
import pandas as pd

from app.repair.contracts import (ECC, CheckStatus, Decision, MLReceipt, RepoContract, SealedManifest)
from app.repair.oracle import common as cm
from app.repair.sandbox.base import SCRATCH, SandboxError, SandboxRunner, Upload

PASS, FAIL, INCON = CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.INCONCLUSIVE
HARNESS = Path(__file__).with_name("harness") / "parity_probe.py"
SEG_MIN_ROWS, SEG_MIN_CLASS = 30, 5
THRESHOLD_FREE = {"roc_auc"}


# ── single-use holdout ────────────────────────────────────────────────────────
class HoldoutLedger(Protocol):
    def consume(self, task_id: str, candidate_id: str) -> None:
        """Grant the task's one holdout opening to this candidate, or raise PermissionError.
        Replaying the same candidate is allowed (idempotent); a second candidate is refused."""


class FileHoldoutLedger:
    """Durable across restarts: one O_EXCL permit file per task."""
    def __init__(self, root: str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def consume(self, task_id: str, candidate_id: str) -> None:
        p = self.root / f"{task_id}.permit"
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            holder = p.read_text(encoding="utf-8").strip()
            if holder != candidate_id:
                raise PermissionError(f"holdout for task {task_id} was already opened for {holder}")
            return
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(candidate_id)


# ── metrics ───────────────────────────────────────────────────────────────────
def _metric(metric: str, y: np.ndarray, score: np.ndarray, decision: np.ndarray) -> float:
    from sklearn.metrics import (balanced_accuracy_score, f1_score, precision_score, recall_score,
                                 roc_auc_score)
    if len(np.unique(y)) < 2:
        return float("nan")
    if metric == "roc_auc":
        return float(roc_auc_score(y, score))
    fn = {"f1": f1_score, "recall": recall_score, "precision": precision_score}.get(metric)
    if fn:
        return float(fn(y, decision, zero_division=0))
    return float(balanced_accuracy_score(y, decision))


def paired_delta(metric: str, y, sa, da, sb, db, n_boot: int = 400, seed: int = 0) -> dict[str, float]:
    """Paired bootstrap of metric(b) - metric(a) on the same holdout rows."""
    rng = np.random.default_rng(seed)
    n, deltas = len(y), []
    for _ in range(n_boot):
        i = rng.integers(0, n, n)
        if len(np.unique(y[i])) < 2:
            continue
        deltas.append(_metric(metric, y[i], sb[i], db[i]) - _metric(metric, y[i], sa[i], da[i]))
    point = _metric(metric, y, sb, db) - _metric(metric, y, sa, da)
    lo, hi = np.percentile(deltas, [2.5, 97.5]) if deltas else (float("nan"), float("nan"))
    return {"delta": round(float(point), 4), "ci_low": round(float(lo), 4), "ci_high": round(float(hi), 4),
            "n_boot": len(deltas)}


# ── predictions ───────────────────────────────────────────────────────────────
class PredictionError(ValueError):
    pass


def read_predictions(raw: bytes, id_col: str, expected_ids: pd.Series) -> pd.DataFrame:
    try:
        df = pd.read_csv(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise PredictionError(f"predictions are not a readable CSV: {e}") from e
    if id_col not in df.columns or "score" not in df.columns:
        raise PredictionError(f"predictions must have columns {id_col},score[,decision]")
    if df[id_col].duplicated().any():
        raise PredictionError("duplicate ids in predictions")
    if set(df[id_col].astype(str)) != set(expected_ids.astype(str)):
        raise PredictionError("prediction ids do not match the holdout feature ids")
    s = pd.to_numeric(df["score"], errors="coerce")
    if s.isna().any() or not np.isfinite(s).all() or (s < 0).any() or (s > 1).any():
        raise PredictionError("scores must be finite probabilities in [0, 1]")
    out = pd.DataFrame({"id": df[id_col].astype(str), "score": s.astype(float)})
    if "decision" in df.columns:
        d = pd.to_numeric(df["decision"], errors="coerce")
        if not d.isin([0, 1]).all():
            raise PredictionError("decision must be 0 or 1")
        out["decision"] = d.astype(int)
    else:
        out["decision"] = (out["score"] >= 0.5).astype(int)
    return out.set_index("id")


def _pipeline(runner: SandboxRunner, env: str, contract: RepoContract, *, holdout: str | None = None,
              predictions: str = cm.PREDICTIONS, train: bool = True, uploads=None) -> tuple[bool, str, str]:
    """Run train (optional) and predict. Returns (ok, stage, log tail)."""
    ph = cm.placeholders(contract, predictions=predictions, **({"holdout_features": holdout} if holdout else {}))
    if train:
        r = runner.run(env, cm.expand(contract.commands.train, ph), timeout_s=contract.limits.train_seconds)
        if r.exit_code != 0:
            return False, "train", cm.tail(r)
    r = runner.run(env, cm.expand(contract.commands.predict, ph), timeout_s=contract.limits.train_seconds,
                   uploads=uploads, collect=[predictions])
    if r.exit_code != 0:
        return False, "predict", cm.tail(r)
    return True, "", ""


def baseline_predictions(runner: SandboxRunner, base_dir: str, contract: RepoContract) -> pd.DataFrame | None:
    """Predictions of the unpatched repository, or None if its pipeline cannot run.
    Computed once per task and reused for every candidate."""
    ids = pd.read_csv(Path(base_dir) / contract.data.holdout_features)[contract.data.id_column]
    with cm.environment(runner, base_dir, contract) as env:
        ok, _, _ = _pipeline(runner, env, contract)
        if not ok:
            return None
        try:
            return read_predictions(runner.read_file(env, cm.PREDICTIONS), contract.data.id_column, ids)
        except (FileNotFoundError, PredictionError):
            return None


# ── the oracle ────────────────────────────────────────────────────────────────
def verify(runner: SandboxRunner, *, base_dir: str, contract: RepoContract, ecc: ECC, task_id: str,
           candidate_id: str, diff: str, sealed_dir: str | None, manifest: SealedManifest | None,
           ledger: HoldoutLedger | None,
           baseline: pd.DataFrame | None, open_holdout: bool = True) -> MLReceipt:
    """open_holdout=False runs only the label-free gates (used to screen candidates during
    selection); it never touches labels and returns INCONCLUSIVE/SCREEN_ONLY when they pass."""
    checks: list = []
    metrics: dict = {}
    opened = False
    ddig = cm.diff_digest(diff)
    data = contract.data
    inv = set(ecc.success.ml_invariants)
    tol = ecc.success.parity_tolerance

    def receipt(decision: Decision, reasons: list[str]) -> MLReceipt:
        return MLReceipt(task_id=task_id, candidate_id=candidate_id, contract_digest=ecc.contract_digest,
                         diff_digest=ddig, checks=checks, decision=decision, reasons=reasons, runner=runner.name,
                         metrics=metrics, holdout_opened=opened).sealed()

    if not ecc.intact():
        checks.append(cm.check("contract_integrity", FAIL, "engineering change contract digest does not match"))
        return receipt(Decision.REJECTED, ["E_CONTRACT_TAMPERED"])
    if data is None:
        checks.append(cm.check("ml_contract", INCON, "the repository contract declares no data"))
        return receipt(Decision.INCONCLUSIVE, ["E_NO_ML_CONTRACT"])
    try:
        files = cm.apply_diff(base_dir, diff)
    except ValueError as e:
        checks.append(cm.check("diff_applies", FAIL, str(e)))
        return receipt(Decision.REJECTED, ["E_DIFF_DOES_NOT_APPLY"])

    holdout = pd.read_csv(Path(base_dir) / data.holdout_features)
    ids = holdout[data.id_column]
    try:
        with cm.environment(runner, base_dir, contract, files) as env:
            # 1 the candidate's own pipeline runs
            ok, stage, log = _pipeline(runner, env, contract)
            if not ok:
                checks.append(cm.check("pipeline_runs", FAIL, f"the repository's {stage} command failed", tail=log))
                return receipt(Decision.REJECTED, ["E_PIPELINE_BROKEN"])
            try:
                preds = read_predictions(runner.read_file(env, cm.PREDICTIONS), data.id_column, ids)
            except (FileNotFoundError, PredictionError) as e:
                checks.append(cm.check("prediction_schema", FAIL, str(e)))
                return receipt(Decision.REJECTED, ["E_PREDICTIONS_INVALID"])
            checks.append(cm.check("pipeline_runs", PASS, f"train and predict ran; {len(preds)} holdout rows scored"))

            # 2 train/serve parity, executed inside the candidate environment
            if "train_serve_parity" in inv:
                checks.append(_parity(runner, env, contract, manifest.parity_rows if manifest else 200, tol))

            # 3 prediction-time availability (leakage through post-outcome columns)
            leaky = [c for c in data.unavailable_at_prediction if c in holdout.columns]
            if "no_target_leakage" in inv or leaky:
                checks.append(_availability(runner, env, contract, holdout, leaky, preds, tol))
    except SandboxError as e:
        checks.append(cm.check("sandbox", INCON, f"execution environment failed: {e}"))
        return receipt(Decision.SYSTEM_FAILURE, ["E_SANDBOX"])

    if any(c.status is FAIL for c in checks):
        return receipt(Decision.REJECTED, [f"E_{c.name.upper()}" for c in checks if c.status is FAIL])
    if not open_holdout or ledger is None:
        return receipt(Decision.INCONCLUSIVE, ["SCREEN_ONLY"])
    if manifest is None or sealed_dir is None:
        # No evaluation pack: every label-free gate passed, quality on unseen data is unmeasured.
        checks.append(cm.check("holdout_quality", INCON, "no holdout labels were provided, so predictive "
                               "quality was not measured; parity and availability were"))
        return receipt(Decision.VERIFIED, ["LIMITED_NO_LABELS"])

    # 4 single-use holdout: only now, after every label-free gate passed
    try:
        ledger.consume(task_id, candidate_id)
    except PermissionError as e:
        checks.append(cm.check("holdout_integrity", FAIL, str(e)))
        return receipt(Decision.REJECTED, ["E_HOLDOUT_REUSE"])
    opened = True
    labels = pd.read_csv(Path(sealed_dir) / manifest.labels)
    labels[data.id_column] = labels[data.id_column].astype(str)
    labels = labels.set_index(data.id_column).loc[preds.index]
    y = (labels[data.target].astype(str) == str(data.positive_label)).astype(int).to_numpy()
    checks.append(cm.check("holdout_integrity", PASS, "holdout labels opened once, for the frozen finalist",
                           rows=int(len(y)), positives=int(y.sum())))
    if y.sum() < SEG_MIN_CLASS or (len(y) - y.sum()) < SEG_MIN_CLASS:
        checks.append(cm.check("sample_size", INCON, "too few positives or negatives to judge"))
        return receipt(Decision.INCONCLUSIVE, ["E_EVIDENCE_MISSING"])

    m = ecc.success.metric
    sc, dc = preds["score"].to_numpy(), preds["decision"].to_numpy()
    metrics["holdout"] = {"metric": m, "candidate": round(_metric(m, y, sc, dc), 4), "rows": int(len(y))}
    base = baseline.loc[preds.index] if baseline is not None else None
    if base is not None:
        sb, db = base["score"].to_numpy(), base["decision"].to_numpy()
        metrics["holdout"]["baseline"] = round(_metric(m, y, sb, db), 4)
        metrics["delta"] = paired_delta(m, y, sb, db, sc, dc)

    # 5 primary objective
    checks.append(_objective(ecc, metrics, base is not None, manifest.baseline_metric))

    # 6 segment guardrail
    if data.segment_column:
        checks.append(_segments(data, labels, holdout, preds, base, y, metrics))

    statuses = [c.status for c in checks]
    reasons = [f"E_{c.name.upper()}" for c in checks if c.status is not PASS]
    if FAIL in statuses:
        return receipt(Decision.REJECTED, reasons)
    if INCON in statuses:
        return receipt(Decision.INCONCLUSIVE, reasons)
    return receipt(Decision.VERIFIED, [])


def _parity(runner, env, contract: RepoContract, rows: int, tol: float):
    ep = contract.entrypoints.get("serve")
    out = f"{SCRATCH}/parity.json"
    argv = ["python", f"{SCRATCH}/parity_probe.py", "--entrypoint", ep, "--model", cm.model_path(contract),
            "--input", contract.data.holdout_features, "--predictions", cm.PREDICTIONS,
            "--id-column", contract.data.id_column, "--rows", str(rows), "--out", out]
    r = runner.run(env, argv, timeout_s=contract.limits.test_seconds,
                   uploads=[Upload(f"{SCRATCH}/parity_probe.py", HARNESS.read_bytes())], collect=[out])
    try:
        rep = json.loads(runner.read_file(env, out))
    except (FileNotFoundError, ValueError):
        return cm.check("train_serve_parity", FAIL, "parity probe produced no report", tail=cm.tail(r))
    diffs = [d for d in rep.get("diffs") or [] if d is not None]
    mism = sum(1 for d in rep.get("diffs") or [] if d is None or d > tol)
    ok = not rep.get("errors") and rep.get("n_rows", 0) > 0 and rep.get("max_abs_diff") is not None \
        and rep["max_abs_diff"] <= tol
    detail = (f"online scores match batch scores on {rep.get('n_rows')} rows (max |Δ| {rep.get('max_abs_diff'):.2e})"
              if ok else f"online scoring disagrees with batch scoring on {mism}/{rep.get('n_rows', 0)} rows")
    return cm.check("train_serve_parity", PASS if ok else FAIL, detail, entrypoint=ep, tolerance=tol,
                    max_abs_diff=rep.get("max_abs_diff"), mismatched_rows=mism,
                    median_abs_diff=float(np.median(diffs)) if diffs else None, errors=rep.get("errors", [])[:3])


def _availability(runner, env, contract: RepoContract, holdout: pd.DataFrame, leaky: list[str],
                  preds: pd.DataFrame, tol: float):
    """Blank every column that is not known at prediction time and score again. A model that
    does not depend on them produces the same scores."""
    if not leaky:
        return cm.check("no_target_leakage", PASS, "no post-outcome columns are present in prediction-time inputs")
    blanked = holdout.copy()
    blanked[leaky] = np.nan
    src, dst = f"{SCRATCH}/holdout_blanked.csv", f"{SCRATCH}/predictions_blanked.csv"
    ok, stage, log = _pipeline(runner, env, contract, holdout=src, predictions=dst, train=False,
                               uploads=[Upload(src, blanked.to_csv(index=False).encode())])
    if not ok:
        return cm.check("no_target_leakage", FAIL, "the model cannot score without post-outcome columns",
                        columns=leaky, tail=log)
    try:
        again = read_predictions(runner.read_file(env, dst), contract.data.id_column, holdout[contract.data.id_column])
    except (FileNotFoundError, PredictionError) as e:
        return cm.check("no_target_leakage", FAIL, f"scoring without post-outcome columns failed: {e}", columns=leaky)
    diff = float(np.max(np.abs(again.loc[preds.index, "score"].to_numpy() - preds["score"].to_numpy())))
    ok = diff <= tol
    return cm.check("no_target_leakage", PASS if ok else FAIL,
                    "scores do not depend on post-outcome columns" if ok else
                    "scores change when post-outcome columns are removed: the model uses information "
                    "that does not exist at prediction time", columns=leaky, max_abs_diff=diff)


def _objective(ecc: ECC, metrics: dict, have_base: bool, recorded: float | None):
    s, m = ecc.success, ecc.success.metric
    margin = s.non_inferiority_margin
    if s.task_type == "improve":
        if not have_base:
            return cm.check("holdout_improves", INCON, "no baseline predictions to compare against")
        d = metrics["delta"]
        if d["ci_low"] > 0:
            return cm.check("holdout_improves", PASS, f"{m} improves; 95% CI excludes zero", **d)
        if d["delta"] > 0:
            return cm.check("holdout_improves", INCON, f"{m} gain is not distinguishable from noise", **d)
        return cm.check("holdout_improves", FAIL, f"{m} does not improve", **d)
    if have_base:
        d = metrics["delta"]
        ok = d["ci_low"] > -margin
        return cm.check("holdout_non_inferior", PASS if ok else FAIL,
                        f"{m} is not worse than the base by more than {margin} (paired 95% CI)", **d)
    if recorded is not None:
        cand = metrics["holdout"]["candidate"]
        ok = cand >= recorded - margin
        return cm.check("holdout_non_inferior", PASS if ok else FAIL,
                        f"{m} {cand} vs last known good {recorded} (margin {margin}); the base commit "
                        "cannot produce predictions", candidate=cand, recorded_baseline=recorded)
    return cm.check("holdout_non_inferior", INCON, "no baseline: the base commit cannot run and none is recorded")


def _segments(data, labels: pd.DataFrame, holdout: pd.DataFrame, preds, base, y, metrics: dict):
    col = data.segment_column
    if col in labels.columns:
        seg = labels[col].astype(str).to_numpy()
    elif col in holdout.columns:
        seg = holdout.set_index(holdout[data.id_column].astype(str)).loc[preds.index, col].astype(str).to_numpy()
    else:
        return cm.check("segment_guardrail", INCON, f"segment column {col} is not available")
    if base is None:
        return cm.check("segment_guardrail", INCON, "no baseline predictions to compare groups against")
    dc, db = preds["decision"].to_numpy(), base["decision"].to_numpy()
    by, small = {}, []
    for g in sorted(set(seg)):
        mk = seg == g
        pos = int(y[mk].sum())
        if mk.sum() < SEG_MIN_ROWS or pos < SEG_MIN_CLASS or (mk.sum() - pos) < SEG_MIN_CLASS:
            small.append(g)
            continue
        c = _metric("recall", y[mk], dc[mk], dc[mk])
        b = _metric("recall", y[mk], db[mk], db[mk])
        by[g] = {"candidate": round(c, 4), "baseline": round(b, 4), "delta": round(c - b, 4), "rows": int(mk.sum())}
    metrics["segments"] = {"column": col, "metric": "recall", "by_group": by, "too_small": small}
    if not by:
        return cm.check("segment_guardrail", INCON, "no group is large enough to judge", too_small=small)
    worst = min(by, key=lambda g: by[g]["delta"])
    ok = by[worst]["delta"] >= -data.segment_max_drop
    return cm.check("segment_guardrail", PASS if ok else FAIL,
                    f"recall must not drop more than {data.segment_max_drop} in any {col} group "
                    f"(worst: {worst}, {by[worst]['delta']:+.4f})", worst_group=worst, too_small=small)
