"""Rare-event / needle-in-haystack metrics (Phase 3.5).

Why this exists: at a 0.29% base rate (Criteo-UPLIFT) or a 3% one (KDD98), ROC-AUC
is a poor *selection* metric. It integrates over the whole ranking, including the
99% of the list nobody will ever act on. What the business actually buys is the
quality of the TOP of the ranking — "we can afford to contact 50,000 people, how
many converters are in that slice?".

So these are ranking-at-a-budget metrics: given a scored population and a budget
k (a FRACTION of the population, not a count — budgets are relative and datasets
change size), how good is the top-k slice?

Doctrine note: the *choice* to optimize precision@k instead of AUC is an ML
DECISION the LLM makes from the goal. The math here is a MECHANIC. See
metric_registry, which offers these to the framer and wires them into the sandbox.

Tie handling: we rank by descending score and cut at exactly ceil(n*k). Ties that
straddle the cut are broken by position, which is what a real campaign does (you
export the top N rows and mail them). We do NOT average over tied permutations —
that would report a number the operator cannot reproduce.
"""
from __future__ import annotations

import math
from typing import Sequence

import numpy as np


def _prepare(y_true: Sequence, y_score: Sequence, k: float) -> tuple[np.ndarray, int, int]:
    """Sort labels by descending score, return (sorted_labels, cut, n_positive).

    Raises ValueError on the inputs that would otherwise produce a silently
    meaningless number (mismatched lengths, k outside (0, 1]).
    """
    y = np.asarray(y_true).ravel()
    s = np.asarray(y_score, dtype=float).ravel()
    if y.shape[0] != s.shape[0]:
        raise ValueError(f"y_true and y_score length mismatch: {y.shape[0]} vs {s.shape[0]}")
    if y.shape[0] == 0:
        raise ValueError("empty input")
    if not (0.0 < float(k) <= 1.0):
        raise ValueError(f"k must be a fraction in (0, 1], got {k!r}")

    y = (y > 0).astype(int)          # tolerate bool / {-1,1} / probabilities-as-labels
    n = y.shape[0]
    # Stable sort so equal scores keep their original order — reproducible for the
    # operator who exports the same CSV twice.
    order = np.argsort(-s, kind="stable")
    cut = max(1, min(n, math.ceil(n * float(k))))
    return y[order], cut, int(y.sum())


def precision_at_k(y_true: Sequence, y_score: Sequence, k: float = 0.1) -> float:
    """Fraction of the top-k slice that is actually positive.

    This is the "if we contact these people, what share convert" number.
    """
    y_sorted, cut, _ = _prepare(y_true, y_score, k)
    return float(y_sorted[:cut].sum()) / float(cut)


def recall_at_k(y_true: Sequence, y_score: Sequence, k: float = 0.1) -> float:
    """Fraction of ALL positives that live inside the top-k slice.

    The "how much of the opportunity do we keep while spending only k of the
    budget" number. Returns 0.0 when there are no positives at all (rather than
    NaN) so a degenerate fold cannot poison a CV mean.
    """
    y_sorted, cut, n_pos = _prepare(y_true, y_score, k)
    if n_pos == 0:
        return 0.0
    return float(y_sorted[:cut].sum()) / float(n_pos)


def lift_at_k(y_true: Sequence, y_score: Sequence, k: float = 0.1) -> float:
    """precision@k divided by the base rate — "how many times better than random".

    A lift of 1.0 means the model is worthless at this budget. Returns 0.0 when
    the base rate is 0 (no positives), for the same fold-safety reason as above.
    """
    y_sorted, cut, n_pos = _prepare(y_true, y_score, k)
    n = y_sorted.shape[0]
    base_rate = n_pos / n
    if base_rate <= 0.0:
        return 0.0
    return (float(y_sorted[:cut].sum()) / float(cut)) / base_rate


def top_k_table(y_true: Sequence, y_score: Sequence,
                ks: Sequence[float] = (0.01, 0.05, 0.10, 0.20, 0.30, 0.50)) -> dict:
    """The full budget curve — one row per budget, for the UI and the model card.

    Every row is computed from the SAME ranking (sorted once), so the operator can
    read it top-to-bottom as "as I spend more, here is what I get".
    """
    y_sorted, _, n_pos = _prepare(y_true, y_score, 1.0)
    n = int(y_sorted.shape[0])
    base_rate = n_pos / n if n else 0.0
    cumulative = np.cumsum(y_sorted)

    rows = []
    for k in ks:
        if not (0.0 < float(k) <= 1.0):
            continue
        cut = max(1, min(n, math.ceil(n * float(k))))
        captured = int(cumulative[cut - 1])
        precision = captured / cut
        rows.append({
            "k_pct": round(float(k) * 100, 2),
            "n_targeted": cut,
            "positives_captured": captured,
            "precision_at_k": round(precision, 6),
            "recall_at_k": round(captured / n_pos, 6) if n_pos else 0.0,
            "lift_at_k": round(precision / base_rate, 4) if base_rate > 0 else 0.0,
        })
    return {"n": n, "n_positive": n_pos,
            "base_rate": round(base_rate, 8),
            "rows": rows}
