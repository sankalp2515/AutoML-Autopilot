"""Prediction matrix, information-gain selection and belief updates.

Each cause predicts which observation bucket each diagnostic template will
return. With belief weights b over the live hypotheses:

    P(o | E) = Σ_i b_i · [pred_i(E) = o]
    IG(E)    = H(b) − Σ_o P(o | E) · H(b | o)

IG is exact *given* the predictions and weights; the weights themselves are an
uncalibrated heuristic and are labelled as such everywhere they are shown.
Score = IG / (cost + ε) − duplicate penalty. Correctives are never chosen by IG:
they are proposed only after a cause is supported.
"""
from __future__ import annotations

import math
from typing import Any

from app.autopilot.profiler import MIN_ROWS_FOR_SHIFT
from app.autopilot.schemas import CandidateScore, Cause, Hypothesis
from app.autopilot.templates import TEMPLATES

EPS = 0.25
INCONSISTENT_FACTOR = 0.1
REFUTE_BELOW = 0.04
SUPPORT_AT = 0.8

_DISTRIBUTIONAL = {"TRAIN_SERVE_PARITY", "SHIFT_AND_LABEL_CHECK"}

C = Cause
# None = the cause makes no reliable prediction for that test (it can go either way).
_MATRIX: dict[str, dict[Cause, str | None]] = {
    "CONTRACT_DIAGNOSTIC": {c: "CONTRACT_OK" for c in C} | {C.DATA_CONTRACT_BREAK: "CONTRACT_BROKEN"},
    "TRAIN_SERVE_PARITY": {c: "PARITY_OK" for c in C} | {
        C.PREPROCESSING_MISMATCH: "SCALE_MISMATCH", C.FEATURE_SHIFT: "SHAPE_SHIFT",
        C.DATA_CONTRACT_BREAK: "SHAPE_SHIFT"},
    "SHIFT_AND_LABEL_CHECK": {c: "STABLE" for c in C} | {
        C.FEATURE_SHIFT: "COVARIATE_SHIFT", C.PREPROCESSING_MISMATCH: "COVARIATE_SHIFT",
        C.DATA_CONTRACT_BREAK: "COVARIATE_SHIFT", C.LABEL_PRIOR_SHIFT: "PRIOR_SHIFT"},
    "THRESHOLD_STUDY": {c: "OPERATING_POINT_OK" for c in C} | {
        C.THRESHOLD_MISCALIBRATION: "RANKING_INTACT_THRESHOLD_OFF",
        C.LABEL_PRIOR_SHIFT: "RANKING_INTACT_THRESHOLD_OFF",
        # A unit error on one feature may or may not destroy the ranking (a tree model
        # can be nearly invariant to it), so this test says nothing about that cause.
        C.FEATURE_SHIFT: "RANKING_DEGRADED", C.PREPROCESSING_MISMATCH: None,
        C.DATA_CONTRACT_BREAK: "RANKING_DEGRADED"},
    "SEGMENT_FAILURE_AUDIT": {c: "UNIFORM" for c in C} | {C.SEGMENT_FAILURE: "CONCENTRATED"},
    "DEPENDENCY_API_PROBE": {c: "API_COMPATIBLE" for c in C} | {C.LIBRARY_API_CHANGE: "PARAM_REMOVED"},
}


def predict(cause: Cause, template_id: str, ctx: dict[str, Any]) -> str | None:
    """Expected bucket, or None when the template cannot say anything here."""
    t = TEMPLATES[template_id]
    if t.kind != "diagnostic":
        return None
    if t.requires_labels and not ctx.get("labels_available"):
        return None
    if template_id in _DISTRIBUTIONAL and ctx.get("current_rows", 0) < MIN_ROWS_FOR_SHIFT:
        return None          # PSI on a tiny window is noise; the test cannot discriminate
    if template_id == "SHIFT_AND_LABEL_CHECK" and not ctx.get("labels_available"):
        return "UNLABELED"
    if template_id == "DEPENDENCY_API_PROBE" and not ctx.get("probe_params"):
        return None
    if template_id == "SEGMENT_FAILURE_AUDIT" and not ctx.get("has_segment", True):
        return None          # no segment column declared: the audit cannot discriminate
    return _MATRIX[template_id][cause]


def diagnostic_ids() -> list[str]:
    return [t for t, spec in TEMPLATES.items() if spec.kind == "diagnostic"]


def _entropy(ws: list[float]) -> float:
    s = sum(ws)
    if s <= 0:
        return 0.0
    return -sum((w / s) * math.log2(w / s) for w in ws if w > 0)


def live(hyps: list[Hypothesis]) -> list[Hypothesis]:
    return [h for h in hyps if h.status != "REFUTED"]


def information_gain(hyps: list[Hypothesis], template_id: str) -> tuple[float, dict[str, str]]:
    hs = live(hyps)
    preds = {h.hypothesis_id: h.prediction_by_template.get(template_id) for h in hs}
    if not hs or all(p is None for p in preds.values()):
        return 0.0, {}
    # Expected entropy if a mismatched prediction rules a hypothesis out. A hypothesis
    # that made no prediction survives every outcome (it is not an outcome of its
    # own, which would overstate the test) and spreads its mass evenly over them.
    outcomes = sorted({p for p in preds.values() if p})
    if not outcomes:
        return 0.0, {}
    total = sum(h.belief_weight for h in hs)
    if not total:
        return 0.0, {}
    h0 = _entropy([h.belief_weight for h in hs])
    cond = 0.0
    for o in outcomes:
        p_o = sum(h.belief_weight * (1.0 / len(outcomes) if preds[h.hypothesis_id] is None
                                     else float(preds[h.hypothesis_id] == o)) for h in hs) / total
        post = [h.belief_weight for h in hs if preds[h.hypothesis_id] in (None, o)]
        cond += p_o * _entropy(post)
    return max(0.0, round(h0 - cond, 4)), {k: v for k, v in preds.items() if v}


def rank(hyps: list[Hypothesis], ctx: dict[str, Any], done_signatures: set[str],
         signature_of, budget_left: float) -> list[CandidateScore]:
    """Score every template (diagnostics by IG/cost, correctives shown with IG 0
    so the UI can explain why a retrain was NOT run first)."""
    out: list[CandidateScore] = []
    for tid, spec in TEMPLATES.items():
        if tid == "CHALLENGER_REVIEW":
            continue
        if spec.kind == "corrective":
            out.append(CandidateScore(template_id=tid, kind="corrective", eligible=False,
                                      reason="corrective: proposed only after a cause is supported",
                                      expected_information_bits=0.0, estimated_cost_units=spec.cost_units,
                                      duplicate_penalty=0.0, score=0.0))
            continue
        ig, preds = information_gain(hyps, tid)
        dup = 1.0 if signature_of(tid) in done_signatures else 0.0
        eligible, reason = True, "eligible"
        if spec.requires_labels and not ctx.get("labels_available"):
            eligible, reason = False, "needs labelled current rows"
        elif tid == "DEPENDENCY_API_PROBE" and not ctx.get("probe_params"):
            eligible, reason = False, "no library error to probe"
        elif dup:
            eligible, reason = False, "identical experiment already run (memory)"
        elif spec.cost_units > budget_left:
            eligible, reason = False, "exceeds remaining budget"
        elif ig <= 1e-6:
            eligible, reason = False, "does not discriminate between live hypotheses"
        score = round(ig / (spec.cost_units + EPS) - dup, 4) if eligible else 0.0
        out.append(CandidateScore(template_id=tid, kind="diagnostic", eligible=eligible, reason=reason,
                                  expected_information_bits=ig, estimated_cost_units=spec.cost_units,
                                  duplicate_penalty=dup, score=score, predicted_buckets=preds))
    out.sort(key=lambda c: (-c.score, c.estimated_cost_units, c.template_id))
    return out


def update(hyps: list[Hypothesis], template_id: str, bucket: str) -> list[dict[str, Any]]:
    """Apply one observation. Returns the per-hypothesis change log."""
    changes = []
    for h in live(hyps):
        pred = h.prediction_by_template.get(template_id)
        before = h.belief_weight
        if pred is None:
            verdict = "no_prediction"
        elif pred == bucket:
            verdict = "consistent"
            h.consistent_observations += 1
        else:
            verdict = "inconsistent"
            h.belief_weight = before * INCONSISTENT_FACTOR
        changes.append({"hypothesis_id": h.hypothesis_id, "predicted": pred, "observed": bucket,
                        "verdict": verdict, "before": round(before, 4)})
    hs = live(hyps)
    total = sum(h.belief_weight for h in hs) or 1.0
    for h in hs:
        h.belief_weight = round(h.belief_weight / total, 4)
        h.revision += 1
        if h.belief_weight < REFUTE_BELOW:
            h.status = "REFUTED"
    # SUPPORTED needs measured backing: a high prior alone never qualifies. The backing
    # may come from an earlier test; a later test that says nothing about this cause
    # can still rule out its rivals.
    for h in live(hyps):
        backed = h.consistent_observations > 0
        h.status = "SUPPORTED" if h.belief_weight >= SUPPORT_AT and backed else "UNRESOLVED"
    by_id = {h.hypothesis_id: h for h in hyps}
    for c in changes:
        c["after"] = by_id[c["hypothesis_id"]].belief_weight
        c["status"] = by_id[c["hypothesis_id"]].status
    return changes


def supported(hyps: list[Hypothesis]) -> Hypothesis | None:
    s = [h for h in hyps if h.status == "SUPPORTED"]
    return s[0] if len(s) == 1 else None
