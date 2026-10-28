"""Business objective + threshold optimization (Phase 3.5).

The gap this closes: the pipeline could tell you a probability, and the decision
endpoint could show you a threshold SWEEP, but nothing ever *chose* a threshold.
Choosing needs a unit economics model — what a true positive is worth, what a
false positive costs — and that is a business input, not a statistical one.

    net benefit = TP*value_tp - FP*cost_fp - FN*cost_fn + TN*value_tn

Doctrine: the LLM DECIDES the cost model from the user's goal ("we lose about $40
per wasted call and a conversion is worth $300"); this module is the MECHANIC that
turns that model into an operating point. See problem_framer.

## The honesty problem this module refuses to have

Picking the threshold that maximizes net benefit ON the holdout and then reporting
that same maximum as the expected benefit is optimistic — the threshold is fitted
to the noise in that particular sample. It is the same class of error as reporting
the best CV score as a generalization estimate, which this codebase already refuses
to do (see the holdout carve in Phase 0.2).

So `optimize_threshold` splits the scored population in two: a TUNE half where the
threshold is chosen, and a REPORT half where the resulting benefit is measured. The
returned `expected_value` is always the honest one. `expected_value_optimistic` is
also returned, precisely so the gap between them is visible rather than hidden.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Sequence

import numpy as np

# A threshold grid finer than this buys nothing but overfitting to the tune split.
_GRID = 201


@dataclass(frozen=True)
class BusinessObjective:
    """Unit economics for a binary decision. All values in one currency unit.

    Signs are normalized so that VALUES are gains and COSTS are losses; callers
    pass positive numbers for both. `cost_fn` defaults to 0 because in most
    campaign problems a missed opportunity has no direct cash cost — but in
    fraud/churn it very much does, so the framer can set it.
    """
    value_tp: float = 1.0        # gain from a correctly-actioned positive
    cost_fp: float = 1.0         # cost of actioning a negative (wasted contact)
    cost_fn: float = 0.0         # cost of missing a positive
    value_tn: float = 0.0        # gain from correctly not-actioning
    currency: str = "unit"
    source: str = "default"      # "llm" | "user" | "default" — provenance matters

    @property
    def is_meaningful(self) -> bool:
        """False when every outcome is worth the same — a degenerate model where
        the optimizer would return an arbitrary threshold. Callers should skip
        optimization and say so rather than print a meaningless number."""
        return not (self.value_tp == self.cost_fp == self.cost_fn == self.value_tn)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Any) -> "BusinessObjective":
        """Tolerant constructor — an LLM-produced dict with junk keys, string
        numbers, or missing fields must never crash a run."""
        if not isinstance(d, dict):
            return cls()
        fields = {"value_tp", "cost_fp", "cost_fn", "value_tn"}
        kwargs: dict[str, Any] = {}
        for f in fields:
            if f in d:
                try:
                    v = float(d[f])
                except (TypeError, ValueError):
                    continue
                if np.isfinite(v):
                    kwargs[f] = abs(v)      # callers pass magnitudes; signs are ours
        cur = d.get("currency")
        if isinstance(cur, str) and cur.strip():
            kwargs["currency"] = cur.strip()[:16]
        src = d.get("source")
        if isinstance(src, str) and src in ("llm", "user", "default"):
            kwargs["source"] = src
        return cls(**kwargs)


def confusion_at(y_true: Sequence, proba: Sequence, threshold: float) -> tuple[int, int, int, int]:
    """(tp, fp, fn, tn) at a threshold. Predict positive when proba >= threshold."""
    y = (np.asarray(y_true).ravel() > 0).astype(int)
    p = np.asarray(proba, dtype=float).ravel()
    pred = (p >= float(threshold)).astype(int)
    tp = int(np.sum((pred == 1) & (y == 1)))
    fp = int(np.sum((pred == 1) & (y == 0)))
    fn = int(np.sum((pred == 0) & (y == 1)))
    tn = int(np.sum((pred == 0) & (y == 0)))
    return tp, fp, fn, tn


def net_benefit(y_true: Sequence, proba: Sequence, threshold: float,
                obj: BusinessObjective) -> float:
    """Total net benefit at a threshold, in the objective's currency."""
    tp, fp, fn, tn = confusion_at(y_true, proba, threshold)
    return (tp * obj.value_tp - fp * obj.cost_fp
            - fn * obj.cost_fn + tn * obj.value_tn)


def optimize_threshold(y_true: Sequence, proba: Sequence,
                       obj: BusinessObjective,
                       random_state: int = 42) -> dict[str, Any]:
    """Choose the threshold maximizing net benefit, and report it HONESTLY.

    The threshold is chosen on a random half (`tune`) and evaluated on the other
    half (`report`). `expected_value_per_case` is from the report half and is the
    number to trust; `expected_value_optimistic_per_case` is the tune-half maximum
    and is included only so the optimism gap is visible.

    Returns a dict that is safe to serialize straight into the API response.
    """
    y = (np.asarray(y_true).ravel() > 0).astype(int)
    p = np.asarray(proba, dtype=float).ravel()
    if y.shape[0] != p.shape[0]:
        raise ValueError(f"length mismatch: {y.shape[0]} vs {p.shape[0]}")
    n = int(y.shape[0])
    if n < 20:
        return {"error": f"need at least 20 scored rows to choose a threshold (got {n})"}
    if not obj.is_meaningful:
        return {"error": "business objective is degenerate (all outcomes valued equally) — "
                         "no threshold is better than any other"}

    rng = np.random.default_rng(random_state)
    idx = rng.permutation(n)
    half = n // 2
    tune_i, report_i = idx[:half], idx[half:]
    y_tune, p_tune = y[tune_i], p[tune_i]
    y_rep, p_rep = y[report_i], p[report_i]

    grid = np.linspace(0.0, 1.0, _GRID)
    tune_curve = [net_benefit(y_tune, p_tune, t, obj) for t in grid]
    best_i = int(np.argmax(tune_curve))
    best_t = float(grid[best_i])

    # Baselines a stakeholder will ask about, all measured on the report half.
    ev_report = net_benefit(y_rep, p_rep, best_t, obj)
    ev_default = net_benefit(y_rep, p_rep, 0.5, obj)
    ev_contact_all = net_benefit(y_rep, p_rep, 0.0, obj)
    ev_contact_none = net_benefit(y_rep, p_rep, 1.01, obj)   # >1 → predict nobody

    tp, fp, fn, tn = confusion_at(y_rep, p_rep, best_t)
    n_rep = int(y_rep.shape[0])

    # The full curve, measured on the REPORT half, for plotting. Coarser grid —
    # the UI cannot render 201 points usefully.
    curve = [{"threshold": round(float(t), 3),
              "net_benefit": round(net_benefit(y_rep, p_rep, t, obj), 4)}
             for t in np.linspace(0.0, 1.0, 51)]

    return {
        "objective": obj.to_dict(),
        "optimal_threshold": round(best_t, 4),
        "chosen_on_n": int(half),
        "measured_on_n": n_rep,
        "expected_value_total": round(float(ev_report), 4),
        "expected_value_per_case": round(float(ev_report) / n_rep, 6),
        "expected_value_optimistic_per_case": round(float(tune_curve[best_i]) / max(1, half), 6),
        "optimism_gap_per_case": round(
            (float(tune_curve[best_i]) / max(1, half)) - (float(ev_report) / n_rep), 6),
        "vs_threshold_0_5": {
            "net_benefit": round(float(ev_default), 4),
            "improvement": round(float(ev_report - ev_default), 4),
        },
        "vs_action_all": {
            "net_benefit": round(float(ev_contact_all), 4),
            "improvement": round(float(ev_report - ev_contact_all), 4),
        },
        "vs_action_none": {
            "net_benefit": round(float(ev_contact_none), 4),
            "improvement": round(float(ev_report - ev_contact_none), 4),
        },
        "confusion_at_optimal": {"tp": tp, "fp": fp, "fn": fn, "tn": tn},
        "action_rate": round((tp + fp) / n_rep, 4) if n_rep else 0.0,
        "curve": curve,
        "honesty_note": ("Threshold chosen on a random half of the holdout and measured on "
                         "the other half; expected_value_per_case is the measured number, "
                         "not the maximum found during the search."),
    }


def sample_weights_from_objective(y_true: Sequence, obj: BusinessObjective) -> np.ndarray:
    """Per-row training weights implementing COST-SENSITIVE LEARNING.

    A positive row's mistake costs (value_tp + cost_fn) — we forgo the gain AND
    pay the miss. A negative row's mistake costs (cost_fp + value_tn). Weighting
    rows by the cost of getting them wrong makes any weight-aware learner
    cost-sensitive without touching its loss function.

    Weights are normalized to mean 1.0 so that regularization strengths tuned on
    an unweighted run stay in the same ballpark.
    """
    y = (np.asarray(y_true).ravel() > 0).astype(int)
    w_pos = float(obj.value_tp + obj.cost_fn)
    w_neg = float(obj.cost_fp + obj.value_tn)
    if w_pos <= 0 and w_neg <= 0:
        return np.ones_like(y, dtype=float)
    w_pos = max(w_pos, 1e-9)
    w_neg = max(w_neg, 1e-9)
    w = np.where(y == 1, w_pos, w_neg).astype(float)
    m = w.mean()
    return w / m if m > 0 else np.ones_like(w)
