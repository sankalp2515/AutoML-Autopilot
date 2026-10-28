"""Phase 3.5 — rare-event metrics, business objective, threshold optimization.

The parity test at the bottom is the important one: `metric_registry.scorer_prelude`
INLINES the at-k maths as source code (the sandbox is a separate container and
cannot import app.core.rare_event). Two copies of a metric is exactly the drift
that produced the direction-bug family, so we execute the emitted source and
assert it agrees with the module on random inputs.
"""
import numpy as np
import pytest

from app.core import metric_registry as mreg
from app.core import rare_event as re_
from app.core.business_objective import (
    BusinessObjective, confusion_at, net_benefit, optimize_threshold,
    sample_weights_from_objective,
)


# ── rare_event: known-answer cases ───────────────────────────────────────────

def test_precision_at_k_perfect_ranking():
    # 10 rows, 2 positives, ranked first → top-20% is all positive.
    y = [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    s = [0.9, 0.8, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]
    assert re_.precision_at_k(y, s, 0.2) == 1.0
    assert re_.recall_at_k(y, s, 0.2) == 1.0
    assert re_.lift_at_k(y, s, 0.2) == pytest.approx(1.0 / 0.2)   # 5x over a 20% base


def test_precision_at_k_worst_ranking():
    y = [1, 1, 0, 0, 0, 0, 0, 0, 0, 0]
    s = [0.1, 0.1, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]
    assert re_.precision_at_k(y, s, 0.2) == 0.0
    assert re_.recall_at_k(y, s, 0.2) == 0.0
    assert re_.lift_at_k(y, s, 0.2) == 0.0


def test_k_is_ceiled_and_never_zero():
    y = [1] + [0] * 99
    s = [0.9] + [0.1] * 99
    # 1% of 100 = 1 row exactly; a tiny k must still target at least one row.
    assert re_.precision_at_k(y, s, 0.01) == 1.0
    assert re_.precision_at_k(y, s, 0.0001) == 1.0


def test_no_positives_returns_zero_not_nan():
    """A degenerate CV fold must not poison the mean with NaN."""
    y = [0] * 50
    s = list(np.linspace(0, 1, 50))
    assert re_.recall_at_k(y, s, 0.1) == 0.0
    assert re_.lift_at_k(y, s, 0.1) == 0.0
    assert re_.precision_at_k(y, s, 0.1) == 0.0


@pytest.mark.parametrize("bad_k", [0.0, -0.1, 1.5, 2])
def test_invalid_k_raises(bad_k):
    with pytest.raises(ValueError):
        re_.precision_at_k([1, 0], [0.9, 0.1], bad_k)


def test_length_mismatch_raises():
    with pytest.raises(ValueError):
        re_.precision_at_k([1, 0, 1], [0.9, 0.1], 0.5)


def test_top_k_table_is_monotone_in_recall():
    rng = np.random.default_rng(7)
    y = (rng.random(1000) < 0.05).astype(int)
    s = rng.random(1000)
    table = re_.top_k_table(y, s)
    recalls = [r["recall_at_k"] for r in table["rows"]]
    assert recalls == sorted(recalls), "recall@k must not decrease as budget grows"
    assert table["n"] == 1000
    assert table["n_positive"] == int(y.sum())


# ── BusinessObjective ────────────────────────────────────────────────────────

def test_from_dict_tolerates_llm_junk():
    obj = BusinessObjective.from_dict(
        {"value_tp": "300", "cost_fp": -40, "nonsense": "x", "currency": "USD", "source": "llm"})
    assert obj.value_tp == 300.0
    assert obj.cost_fp == 40.0          # magnitude taken; sign is ours
    assert obj.currency == "USD"
    assert obj.source == "llm"


def test_from_dict_survives_garbage():
    assert BusinessObjective.from_dict(None).value_tp == 1.0
    assert BusinessObjective.from_dict("nope").value_tp == 1.0
    assert BusinessObjective.from_dict({"value_tp": float("inf")}).value_tp == 1.0
    assert BusinessObjective.from_dict({"value_tp": "abc"}).value_tp == 1.0


def test_degenerate_objective_detected():
    assert not BusinessObjective(1, 1, 1, 1).is_meaningful
    assert BusinessObjective(300, 40).is_meaningful


def test_net_benefit_arithmetic():
    y = [1, 1, 0, 0]
    p = [0.9, 0.9, 0.9, 0.1]        # at t=0.5: tp=2, fp=1, fn=0, tn=1
    assert confusion_at(y, p, 0.5) == (2, 1, 0, 1)
    obj = BusinessObjective(value_tp=300, cost_fp=40)
    assert net_benefit(y, p, 0.5, obj) == 2 * 300 - 1 * 40


# ── threshold optimization ───────────────────────────────────────────────────

def test_optimizer_refuses_tiny_samples():
    out = optimize_threshold([1, 0] * 5, [0.6, 0.4] * 5, BusinessObjective(300, 40))
    assert "error" in out


def test_optimizer_refuses_degenerate_objective():
    rng = np.random.default_rng(0)
    y = (rng.random(500) < 0.2).astype(int)
    out = optimize_threshold(y, rng.random(500), BusinessObjective(1, 1, 1, 1))
    assert "error" in out


def test_optimizer_reports_honest_not_optimistic_value():
    """The headline number must come from the half the threshold was NOT chosen on."""
    rng = np.random.default_rng(3)
    n = 4000
    y = (rng.random(n) < 0.1).astype(int)
    # An informative but noisy score.
    p = np.clip(0.1 + 0.5 * y + rng.normal(0, 0.25, n), 0, 1)
    out = optimize_threshold(y, p, BusinessObjective(value_tp=300, cost_fp=40))
    assert "error" not in out
    assert out["chosen_on_n"] + out["measured_on_n"] == n
    assert 0.0 <= out["optimal_threshold"] <= 1.0
    # Honest and optimistic are both present, and the honest one is the headline.
    assert "expected_value_per_case" in out
    assert "expected_value_optimistic_per_case" in out
    assert out["optimism_gap_per_case"] == pytest.approx(
        out["expected_value_optimistic_per_case"] - out["expected_value_per_case"], abs=1e-6)


def test_optimizer_beats_naive_baselines_on_a_learnable_signal():
    rng = np.random.default_rng(11)
    n = 6000
    y = (rng.random(n) < 0.05).astype(int)          # 5% base rate
    p = np.clip(0.05 + 0.7 * y + rng.normal(0, 0.15, n), 0, 1)
    obj = BusinessObjective(value_tp=300, cost_fp=40)
    out = optimize_threshold(y, p, obj)
    # With a strong signal, a chosen threshold should beat contacting everyone.
    assert out["vs_action_all"]["improvement"] > 0
    assert out["confusion_at_optimal"]["tp"] > 0


def test_optimizer_says_contact_nobody_when_costs_dominate():
    """If a false positive costs far more than a true positive is worth and the
    signal is pure noise, the optimizer must not invent value."""
    rng = np.random.default_rng(5)
    n = 3000
    y = (rng.random(n) < 0.02).astype(int)
    p = rng.random(n)                                # no signal at all
    obj = BusinessObjective(value_tp=1, cost_fp=500)
    out = optimize_threshold(y, p, obj)
    assert out["action_rate"] < 0.15                 # essentially: don't act


# ── cost-sensitive sample weights ────────────────────────────────────────────

def test_sample_weights_favor_the_expensive_class():
    y = np.array([1, 0, 0, 0])
    w = sample_weights_from_objective(y, BusinessObjective(value_tp=300, cost_fp=40))
    assert w[0] > w[1]
    assert w.mean() == pytest.approx(1.0)


def test_sample_weights_are_safe_when_objective_is_empty():
    y = np.array([1, 0, 1, 0])
    w = sample_weights_from_objective(y, BusinessObjective(0, 0, 0, 0))
    assert np.allclose(w, 1.0)


# ── registry wiring ──────────────────────────────────────────────────────────

def test_at_k_metrics_offered_only_for_binary():
    for m in ("precision_at_k", "recall_at_k", "lift_at_k"):
        assert m in mreg.allowed_for("binary_classification")
        assert m not in mreg.allowed_for("regression")
        assert m not in mreg.allowed_for("multiclass_classification")
        assert mreg.higher_is_better(m) is True
        assert mreg.is_custom_k(m) is True


def test_plain_metric_prelude_is_a_simple_assignment():
    src = mreg.scorer_prelude("auc_roc", "binary_classification")
    assert src == "scoring = 'roc_auc'"
    # multiclass remap still applied through the same seam
    assert mreg.scorer_prelude("auc_roc", "multiclass_classification") == \
        "scoring = 'roc_auc_ovr_weighted'"


# ── PARITY: the inlined sandbox maths vs the module ──────────────────────────

@pytest.mark.parametrize("metric", ["precision_at_k", "recall_at_k", "lift_at_k"])
@pytest.mark.parametrize("k", [0.01, 0.1, 0.3])
def test_inlined_scorer_matches_module(metric, k):
    """Execute the emitted prelude and compare its metric fn to app.core.rare_event.

    Two implementations of one metric is the drift shape that caused the
    direction-bug family. This test is the guard.
    """
    src = mreg.scorer_prelude(metric, "binary_classification", k=k)
    ns: dict = {}
    exec(compile(src, "<prelude>", "exec"), ns)      # noqa: S102 - our own generated source
    inlined = ns["_at_k_metric"]
    module_fn = {"precision_at_k": re_.precision_at_k,
                 "recall_at_k": re_.recall_at_k,
                 "lift_at_k": re_.lift_at_k}[metric]

    rng = np.random.default_rng(hash((metric, k)) & 0xFFFF)
    for _ in range(25):
        n = int(rng.integers(50, 800))
        rate = float(rng.uniform(0.005, 0.4))
        y = (rng.random(n) < rate).astype(int)
        s = rng.random(n)
        assert inlined(y, s) == pytest.approx(module_fn(y, s, k), abs=1e-12)


def test_inlined_scorer_handles_2d_proba():
    """make_scorer may hand us the full (n, 2) probability matrix."""
    src = mreg.scorer_prelude("precision_at_k", "binary_classification", k=0.5)
    ns: dict = {}
    exec(compile(src, "<prelude>", "exec"), ns)      # noqa: S102
    inlined = ns["_at_k_metric"]
    y = [1, 1, 0, 0]
    proba_2d = np.array([[0.1, 0.9], [0.2, 0.8], [0.7, 0.3], [0.9, 0.1]])
    assert inlined(y, proba_2d) == 1.0


def test_prelude_k_is_clamped_to_default_when_invalid():
    src = mreg.scorer_prelude("precision_at_k", "binary_classification", k=5.0)
    assert f"_AT_K = {mreg.DEFAULT_K!r}" in src
