"""Regression guard (2026-07-04): winner selection must follow the METRIC's
direction, not the task type.

The bug: model_selector assumed "regression ⇒ lower is better". True for the
neg_* scorers (the template flips them to a positive error) but FALSE for r2,
which is stored as-is and is higher-is-better — so an r2 regression run silently
selected the WORST candidate, and the tuner then optimized that wrong model.
No crash; just a bad model shipped.

The direction rule now lives in ONE place (metric_registry.stored_score_lower_is_better)
used by the parallel path, and mirrored by the template's inline expression.
"""

import pytest

from app.agents.model_selector import TRAINING_CODE_TEMPLATE
from app.core import metric_registry as mreg


# (metric, task_type, expected_lower_is_better)
_CASES = [
    # Regression: only the neg_* scorers are stored flipped → lower-better.
    ("rmse", "regression", True),
    ("mae", "regression", True),
    ("r2", "regression", False),          # ← the bug: was treated as lower-better
    # Classification: never flipped → always higher-better.
    ("f1", "binary_classification", False),
    ("accuracy", "binary_classification", False),
    ("auc_roc", "binary_classification", False),
    ("f1", "multiclass_classification", False),
    ("recall", "multiclass_classification", False),
    # Multilabel: not regression → not flipped.
    ("f1_micro", "multilabel_classification", False),
    ("f1_macro", "multilabel_classification", False),
]


@pytest.mark.parametrize("metric,task,expected", _CASES)
def test_stored_score_direction(metric, task, expected):
    assert mreg.stored_score_lower_is_better(metric, task) is expected


def test_r2_is_higher_better_but_not_flipped():
    """The precise shape of the bug: r2 IS higher-is-better AND is a regression
    metric — so task-type-based direction logic gets it exactly backwards."""
    assert mreg.higher_is_better("r2") is True
    assert not mreg.sklearn_scorer("r2", "regression").startswith("neg_")
    assert mreg.stored_score_lower_is_better("r2", "regression") is False


def test_unknown_metric_defaults_safely():
    # Unknown metric on a regression task falls back to the default scorer
    # (neg_root_mean_squared_error) → lower-better, matching the template.
    assert mreg.stored_score_lower_is_better("not_a_metric", "regression") is True
    assert mreg.stored_score_lower_is_better("not_a_metric", "binary_classification") is False


def test_directional_gain_positive_always_means_better():
    """Reporting used a bare `final - baseline`, printing a genuine rmse win
    (3.5 → 0.9) as -2.6 in the model card / notebook / Overview."""
    # lower-is-better: error went DOWN → positive gain
    assert mreg.directional_gain(0.9, 3.5, "rmse") == pytest.approx(2.6)
    assert mreg.directional_gain(3.5, 0.9, "rmse") == pytest.approx(-2.6)   # got worse
    assert mreg.directional_gain(0.2, 0.5, "mae") == pytest.approx(0.3)
    # higher-is-better: score went UP → positive gain
    assert mreg.directional_gain(0.92, 0.80, "f1") == pytest.approx(0.12)
    assert mreg.directional_gain(0.80, 0.92, "f1") == pytest.approx(-0.12)  # got worse
    assert mreg.directional_gain(0.99, 0.10, "r2") == pytest.approx(0.89)


def test_directional_gain_is_safe_on_junk():
    assert mreg.directional_gain(None, 1.0, "f1") == 0.0
    assert mreg.directional_gain("abc", 1.0, "rmse") == 0.0


def test_iteration_gate_gain_is_directional():
    """The gate computed abs(current - prev): a score that got WORSE by more than
    the threshold looked like progress, so the loop burned its budget chasing a
    regression. Gain must be signed by metric direction."""
    from app.agents import orchestrator

    def gate(current, prev, metric, iteration=0, max_iter=3, std=0.0):
        return orchestrator.route_after_evaluator({
            "current_score": current, "prev_score": prev, "primary_metric": metric,
            "iteration": iteration, "max_iterations": max_iter, "score_std": std,
            "status": "running",
        })

    # rmse (lower better): 3.5 → 0.9 is a real gain → keep iterating
    assert gate(0.9, 3.5, "rmse") == "feature_engineer"
    # rmse got WORSE 0.9 → 3.5: previously abs()=2.6 looked like progress. Must stop.
    assert gate(3.5, 0.9, "rmse") == "trust_gate"
    # f1 (higher better): 0.80 → 0.92 gain → iterate; reverse → stop
    assert gate(0.92, 0.80, "f1") == "feature_engineer"
    assert gate(0.80, 0.92, "f1") == "trust_gate"


def test_template_mirrors_the_same_rule():
    """The sandbox template cannot import app code, so it inlines the rule — this
    pins that the inline expression still matches the single source of truth."""
    assert 'lower_is_better = (task_type == "regression" and scoring.startswith("neg_"))' \
        in TRAINING_CODE_TEMPLATE
    # And that the winner picks min/max off THAT flag, not off task_type.
    assert "if lower_is_better:" in TRAINING_CODE_TEMPLATE
    assert 'if task_type == "regression":\n    winner = min(' not in TRAINING_CODE_TEMPLATE
