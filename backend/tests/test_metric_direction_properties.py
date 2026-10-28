"""Property tests over the ENTIRE metric x task space (P1.4).

The r2 / iteration-gate / reporting bugs were all one root cause: the four places
that reason about "which direction is better" disagreed. These tests assert they are
mutually self-consistent for EVERY metric and task, plus randomized value sweeps —
so any future metric added, or any component drifting, fails immediately.

No `hypothesis` dependency: exhaustive parametrization + seeded random sweeps give the
same guarantee for this small, closed space.
"""
import random

import pytest

from app.core import metric_registry as mreg
from app.core.trust_probes import _lower_better as probe_lower_better

ALL_METRICS = mreg.all_metrics()
TASKS = ["binary_classification", "multiclass_classification",
         "multilabel_classification", "regression"]
_LOWER_BETTER_METRICS = {"rmse", "mae", "hamming_loss"}   # the only lower-better ones in the registry


# ── 1. higher_is_better is authoritative and matches the registry ─────────────

@pytest.mark.parametrize("metric", ALL_METRICS)
def test_higher_is_better_matches_registry(metric):
    expected = metric not in _LOWER_BETTER_METRICS
    assert mreg.higher_is_better(metric) is expected


# ── 2. stored_score_lower_is_better fires ONLY for regression + neg_* ─────────

@pytest.mark.parametrize("metric", ALL_METRICS)
@pytest.mark.parametrize("task", TASKS)
def test_stored_direction_only_regression_neg(metric, task):
    scorer = mreg.sklearn_scorer(metric, task)
    expected = (task == "regression" and scorer.startswith("neg_"))
    assert mreg.stored_score_lower_is_better(metric, task) is expected
    # r2 is the trap: regression + higher-better → must NOT be lower-better.
    if metric == "r2" and task == "regression":
        assert mreg.stored_score_lower_is_better(metric, task) is False


# ── 3. directional_gain agrees with higher_is_better for random values ────────

@pytest.mark.parametrize("metric", ALL_METRICS)
def test_directional_gain_sign_matches_direction(metric):
    rng = random.Random(hash(metric) & 0xFFFF)
    hib = mreg.higher_is_better(metric)
    for _ in range(200):
        a, b = rng.uniform(-100, 100), rng.uniform(-100, 100)
        gain = mreg.directional_gain(a, b, metric)
        if a == b:
            assert gain == 0.0
        elif hib:
            # higher better: gain>0 iff new(a) > old(b)
            assert (gain > 0) == (a > b)
        else:
            # lower better: gain>0 iff new(a) < old(b)
            assert (gain > 0) == (a < b)


# ── 4. The trust-probe direction agrees with the registry ────────────────────

@pytest.mark.parametrize("metric", ALL_METRICS)
def test_probe_direction_agrees_with_registry(metric):
    # The probe uses a regex; the registry uses a table. They must agree for every
    # registered metric (a mismatch = the Skeptic grading in the wrong direction).
    assert probe_lower_better(metric) == (not mreg.higher_is_better(metric))


# ── 5. Cross-component consistency: gate/selector/reporter never disagree ──────

@pytest.mark.parametrize("metric", ALL_METRICS)
@pytest.mark.parametrize("task", TASKS)
def test_gain_and_stored_direction_are_consistent(metric, task):
    """If the stored score is lower-better, a lower new value must be a POSITIVE
    gain — the selector (min/max) and the reporter (directional_gain) must never
    point opposite ways."""
    lower_stored = mreg.stored_score_lower_is_better(metric, task)
    # For regression, the stored value's direction IS the reporting direction.
    if task == "regression":
        gain_better_when_lower = mreg.directional_gain(1.0, 2.0, metric) > 0
        # rmse/mae: lower is better in BOTH; r2: higher in both.
        assert gain_better_when_lower == (not mreg.higher_is_better(metric))
        # and the selector's min/max choice matches the stored flag
        assert lower_stored == (not mreg.higher_is_better(metric) and
                                mreg.sklearn_scorer(metric, task).startswith("neg_"))


# ── 6. Unknown metric degrades safely everywhere ──────────────────────────────

def test_unknown_metric_safe_defaults():
    assert mreg.higher_is_better("totally_made_up") is True          # optimistic default
    assert mreg.directional_gain(2.0, 1.0, "totally_made_up") == 1.0  # treated higher-better
    # unknown on regression falls back to the default neg_ scorer → lower-better
    assert mreg.stored_score_lower_is_better("totally_made_up", "regression") is True
