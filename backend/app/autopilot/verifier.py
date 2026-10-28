"""Independent verifier — the only component that writes metric results.

It reloads the candidate artifact itself, recomputes every number from labels and
its own predictions, and applies deterministic gates in a fixed order. Gates that
can already reject (integrity, protocol, leakage, sample size) run BEFORE the
final test is opened, so the single-use holdout is never spent on a candidate
that was invalid anyway. No model text participates in the verdict.
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np

from app.autopilot import mlcore
from app.autopilot.schemas import utcnow
from app.autopilot.workspace import Workspace

VERIFIER_VERSION = "autopilot-verifier/1"
MIN_ROWS, MIN_POS = 150, 20
NON_INFERIORITY_MARGIN = 0.02
LEAK_AUC = 0.85


def _check(name: str, status: str, detail: str, **evidence: Any) -> dict[str, Any]:
    return {"check": name, "status": status, "detail": detail, "evidence": evidence}


def _single_feature_auc(df, col: str, target: str) -> float:
    from sklearn.metrics import roc_auc_score
    x = np.asarray(df[col], dtype=float)
    y = df[target].astype(int).to_numpy()
    if len(np.unique(y)) < 2 or np.nanstd(x) == 0:
        return 0.5
    x = np.nan_to_num(x, nan=np.nanmedian(x))
    auc = roc_auc_score(y, x)
    return float(max(auc, 1 - auc))


def verify(ws: Workspace, candidate: dict[str, Any], self_report: dict[str, Any], *, mode: str,
           expected_protocol_hash: str, declared_split: str = "same",
           open_final_test: Callable[[str], Any]) -> dict[str, Any]:
    """mode: "improve" (fix a degradation) or "non_inferior" (repair a failed retrain)."""
    pack = ws.pack()
    target, metric = pack["target"], pack["primary_metric"]
    cfg = pack.get("incumbent_pipeline") or pack["pipeline"]
    contract = set(cfg["numeric"] + cfg["categorical"] + cfg.get("optional_features", []))
    cand = mlcore.Candidate.from_dict(candidate)
    inc = ws.incumbent()
    checks: list[dict[str, Any]] = []
    metrics: dict[str, Any] = {}

    def receipt(decision: str, reasons: list[str]) -> dict[str, Any]:
        return {"verifier_version": VERIFIER_VERSION, "decision": decision, "reason_codes": reasons,
                "checks": checks, "metrics": metrics, "candidate": cand.label, "incumbent": inc.label,
                "metric": metric, "mode": mode, "protocol_hash": expected_protocol_hash, "at": utcnow()}

    # 1 artifact integrity
    try:
        digest = mlcore.sha256_file(cand.model_path)
        checks.append(_check("artifact_integrity", "PASS", "candidate artifact loaded and hashed",
                             sha256=digest))
    except OSError:
        checks.append(_check("artifact_integrity", "FAIL", "candidate artifact missing"))
        return receipt("REJECT", ["E_ARTIFACT_MISSING"])

    # 2 protocol parity
    actual = ws.protocol()["manifest"]["protocol_hash"]
    if actual != expected_protocol_hash or declared_split != "same":
        checks.append(_check("protocol_parity", "FAIL",
                             "candidate metrics were not produced on the frozen protocol"
                             if declared_split != "same" else "protocol hash changed since baseline",
                             declared_split=declared_split, expected=expected_protocol_hash, actual=actual))
        return receipt("REJECT", ["E_PROTOCOL_MISMATCH"])
    checks.append(_check("protocol_parity", "PASS", "same frozen split and metric as the incumbent",
                         protocol_hash=actual))

    sel = ws.rows("selection_validation")
    # 3 feature contract / leakage
    feats = cand.features or sorted(contract)
    undeclared = [f for f in feats if f not in contract]
    if undeclared:
        powers = {f: round(_single_feature_auc(sel, f, target), 4) for f in undeclared if f in sel.columns}
        leak = {f: p for f, p in powers.items() if p >= LEAK_AUC}
        checks.append(_check("leakage", "FAIL",
                             "feature alone nearly predicts the label — likely post-outcome leakage"
                             if leak else "uses features outside the declared contract",
                             undeclared=undeclared, single_feature_auc=powers))
        return receipt("REJECT", ["E_LEAKAGE" if leak else "E_CONTRACT_VIOLATION"])
    checks.append(_check("leakage", "PASS", "all features are within the declared contract"))

    # 4 evidence sufficiency
    n_sel, pos_sel = len(sel), int(sel[target].sum()) if len(sel) else 0
    roles = ws.protocol()["manifest"]["counts"]
    if n_sel < MIN_ROWS or pos_sel < MIN_POS or roles["final_test"] < MIN_ROWS:
        checks.append(_check("sample_size", "INCONCLUSIVE",
                             f"need ≥{MIN_ROWS} rows and ≥{MIN_POS} positives per split",
                             selection_rows=n_sel, selection_positives=pos_sel, final_test_rows=roles["final_test"]))
        return receipt("INCONCLUSIVE", ["E_EVIDENCE_MISSING"])
    checks.append(_check("sample_size", "PASS", "enough labelled rows", selection_rows=n_sel,
                         final_test_rows=roles["final_test"]))

    # 5 metric provenance: recompute the runner's self-reported validation metric
    p_sel = mlcore.predict_proba(cand, sel)
    recomputed = mlcore.score(sel[target], p_sel, cand.threshold)
    reported = (self_report or {}).get(metric)
    metrics["selection_validation"] = {"candidate": recomputed,
                                       "incumbent": mlcore.score(sel[target], mlcore.predict_proba(inc, sel),
                                                                 inc.threshold)}
    if reported is not None and abs(reported - recomputed[metric]) > 1e-6:
        checks.append(_check("metric_provenance", "FAIL", "runner-reported metric does not reproduce",
                             reported=reported, recomputed=recomputed[metric]))
        return receipt("REJECT", ["E_METRIC_NOT_REPRODUCIBLE"])
    checks.append(_check("metric_provenance", "PASS", "validation metric reproduced from saved artifact",
                         recomputed=recomputed[metric]))

    # 6 single-use final test
    try:
        fin = open_final_test(cand.label)
    except PermissionError as e:
        checks.append(_check("holdout_integrity", "FAIL", str(e)))
        return receipt("REJECT", ["E_HOLDOUT_REUSE"])
    checks.append(_check("holdout_integrity", "PASS", "final test opened exactly once, after candidate lock"))
    y = fin[target].astype(int).to_numpy()
    p_c, p_i = mlcore.predict_proba(cand, fin), mlcore.predict_proba(inc, fin)
    metrics["final_test"] = {"candidate": mlcore.score(y, p_c, cand.threshold),
                             "incumbent": mlcore.score(y, p_i, inc.threshold), "rows": int(len(y))}
    boot = mlcore.bootstrap_delta(y, p_i, inc.threshold, p_c, cand.threshold, metric)
    metrics["delta"] = boot
    reasons: list[str] = []

    # 7 primary objective
    if mode == "non_inferior":
        ok = boot["ci_low"] > -NON_INFERIORITY_MARGIN
        checks.append(_check("primary_objective", "PASS" if ok else "FAIL",
                             f"{metric} non-inferior to last good model (margin {NON_INFERIORITY_MARGIN})",
                             **boot))
        if not ok:
            reasons.append("E_PRIMARY_WORSE")
    elif boot["ci_low"] > 0:
        checks.append(_check("primary_objective", "PASS", f"{metric} improves; 95% CI excludes zero", **boot))
    elif boot["delta"] > 0:
        checks.append(_check("primary_objective", "INCONCLUSIVE", f"{metric} gain not distinguishable from noise",
                             **boot))
        reasons.append("E_UNCERTAIN_GAIN")
    else:
        checks.append(_check("primary_objective", "FAIL", f"{metric} does not improve", **boot))
        reasons.append("E_PRIMARY_WORSE")

    # 8 critical-segment guardrail
    seg = pack.get("critical_segment")
    if seg and seg.get("value") is None:
        # Column only: no group may get worse by more than max_drop.
        worst, rows_by = None, {}
        for v in sorted(fin[seg["column"]].dropna().astype(str).unique()):
            mm = (fin[seg["column"]].astype(str) == v).to_numpy()
            if mm.sum() < 30 or len(np.unique(y[mm])) < 2:
                continue
            sc = mlcore.score(y[mm], p_c[mm], cand.threshold)[seg["metric"]]
            si = mlcore.score(y[mm], p_i[mm], inc.threshold)[seg["metric"]]
            rows_by[v] = {"candidate": sc, "incumbent": si, "delta": round(sc - si, 4), "rows": int(mm.sum())}
            if worst is None or sc - si < rows_by[worst]["delta"]:
                worst = v
        if not rows_by:
            checks.append(_check("segment_guardrail", "INCONCLUSIVE", "no segment large enough to judge"))
            reasons.append("E_EVIDENCE_MISSING")
        else:
            w = rows_by[worst]
            metrics["critical_segment"] = {"segment": f"{seg['column']}={worst} (worst of {len(rows_by)})",
                                           "metric": seg["metric"], "candidate": w["candidate"],
                                           "incumbent": w["incumbent"], "rows": w["rows"], "by_group": rows_by}
            ok = w["delta"] >= -seg["max_drop"]
            checks.append(_check("segment_guardrail", "PASS" if ok else "FAIL",
                                 f"{seg['metric']} must not drop more than {seg['max_drop']} in any "
                                 f"{seg['column']} group vs the incumbent on the same rows (worst: {worst})",
                                 worst_group=worst, delta=w["delta"]))
            if not ok:
                reasons.append("E_GUARDRAIL_FAIL")
    elif seg:
        m = (fin[seg["column"]].astype(str) == str(seg["value"])).to_numpy()
        if m.sum() < 30 or len(np.unique(y[m])) < 2:
            checks.append(_check("segment_guardrail", "INCONCLUSIVE", "critical segment too small to judge",
                                 rows=int(m.sum())))
            reasons.append("E_EVIDENCE_MISSING")
        else:
            sc = mlcore.score(y[m], p_c[m], cand.threshold)[seg["metric"]]
            si = mlcore.score(y[m], p_i[m], inc.threshold)[seg["metric"]]
            # Paired: same final-test rows for candidate and incumbent. The healthy level
            # (incumbent as validated at training time) is reported as context only —
            # it comes from a different window and is too noisy to gate on.
            rv = ws.rows("ref_validation")
            mr = (rv[seg["column"]].astype(str) == str(seg["value"])).to_numpy()
            healthy = mlcore.score(rv[target][mr], mlcore.predict_proba(inc, rv)[mr],
                                   float(cfg["threshold"]))[seg["metric"]]
            metrics["critical_segment"] = {"segment": f"{seg['column']}={seg['value']}", "metric": seg["metric"],
                                           "candidate": sc, "incumbent": si, "healthy_reference": healthy,
                                           "rows": int(m.sum())}
            ok = sc - si >= -seg["max_drop"]
            checks.append(_check("segment_guardrail", "PASS" if ok else "FAIL",
                                 f"{seg['metric']} on {seg['column']}={seg['value']} must not drop more than "
                                 f"{seg['max_drop']} vs the incumbent on the same rows", candidate=sc,
                                 incumbent=si, delta=round(sc - si, 4), healthy_reference=healthy))
            if not ok:
                reasons.append("E_GUARDRAIL_FAIL")

    statuses = [c["status"] for c in checks]
    if "FAIL" in statuses:
        return receipt("REJECT", reasons)
    if "INCONCLUSIVE" in statuses:
        return receipt("INCONCLUSIVE", reasons)
    return receipt("CORRECTION_VERIFIED", [])
