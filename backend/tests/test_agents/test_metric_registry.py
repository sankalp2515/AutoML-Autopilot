"""Phase 3 — metric_registry is the single source of truth for scorers.

Parity guard: the registry must reproduce the EXACT scorer mappings that used to
be hand-duplicated in baseline_builder (and the framer prompt), so centralizing
them changes no behavior. Also pins the framer menu to the registry.
"""

import pytest

from app.agents.problem_framer import SYSTEM_PROMPT
from app.core import metric_registry as mr

# The exact mapping baseline_builder used before centralization.
_OLD_BASE = {
    "auc_roc": "roc_auc", "recall": "recall", "precision": "precision",
    "f1": "f1", "accuracy": "accuracy", "rmse": "neg_root_mean_squared_error",
    "mae": "neg_mean_absolute_error", "r2": "r2", "pr_auc": "average_precision",
    "f1_micro": "f1_micro", "f1_macro": "f1_macro", "f1_samples": "f1_samples",
    "hamming_loss": "hamming_loss",
}
_OLD_MULTICLASS_REMAP = {
    "roc_auc": "roc_auc_ovr_weighted", "recall": "recall_weighted",
    "precision": "precision_weighted", "f1": "f1_weighted",
    "average_precision": "average_precision",
}


@pytest.mark.parametrize("metric,expected", _OLD_BASE.items())
def test_non_multiclass_scorer_parity(metric, expected):
    # Use binary as the representative non-multiclass classification task.
    task = "regression" if metric in ("rmse", "mae", "r2") else (
        "multilabel_classification" if metric in ("f1_micro", "f1_macro", "f1_samples", "hamming_loss")
        else "binary_classification")
    assert mr.sklearn_scorer(metric, task) == expected


@pytest.mark.parametrize("metric", ["auc_roc", "recall", "precision", "f1", "accuracy", "pr_auc"])
def test_multiclass_remap_parity(metric):
    base = _OLD_BASE[metric]
    expected = _OLD_MULTICLASS_REMAP.get(base, base)
    assert mr.sklearn_scorer(metric, "multiclass_classification") == expected


def test_defaults_match_old_baseline():
    assert mr.default_scorer("regression") == "neg_root_mean_squared_error"
    assert mr.default_scorer("multiclass_classification") == "f1_weighted"
    assert mr.default_scorer("multilabel_classification") == "f1_micro"
    assert mr.default_scorer("binary_classification") == "roc_auc"


def test_unknown_metric_falls_back_to_task_default():
    assert mr.sklearn_scorer("nonsense", "regression") == "neg_root_mean_squared_error"


def test_allowed_metrics_are_task_appropriate():
    assert set(mr.allowed_for("regression")) == {"rmse", "mae", "r2"}
    assert "auc_roc" not in mr.allowed_for("multiclass_classification")  # binary-only
    assert set(mr.allowed_for("multilabel_classification")) == {
        "f1_micro", "f1_macro", "f1_samples", "hamming_loss"}


def test_framer_prompt_is_registry_derived():
    # Every registry metric appears in the prompt; nothing hand-typed drifts.
    for m in mr.all_metrics():
        assert m in SYSTEM_PROMPT
    assert "Allowed metrics by task" in SYSTEM_PROMPT
