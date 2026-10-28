"""Guardrails — input sanitization + LLM framing validation."""

from app.core.guardrails import sanitize_user_goal, validate_and_fix_framing


def test_sanitize_strips_control_chars_and_collapses_space():
    assert sanitize_user_goal("predict\x00 churn\x07   now\t") == "predict churn now"


def test_sanitize_caps_length():
    assert len(sanitize_user_goal("x" * 5000)) == 2000


def test_sanitize_handles_empty():
    assert sanitize_user_goal("") == ""
    assert sanitize_user_goal(None) == ""


def test_framing_fixes_invalid_task():
    fixed, notes = validate_and_fix_framing({"task_type": "magic", "primary_metric": "f1",
                                             "good_enough_threshold": 0.8, "target_column": "y"}, ["y"])
    assert fixed["task_type"] == "binary_classification"
    assert any("task_type" in n for n in notes)


def test_framing_fixes_metric_not_valid_for_task():
    # auc_roc is binary-only — invalid for regression → must fall back to default (rmse).
    fixed, notes = validate_and_fix_framing(
        {"task_type": "regression", "primary_metric": "auc_roc",
         "good_enough_threshold": 0.5, "target_column": "price"}, ["price"])
    assert fixed["primary_metric"] == "rmse"
    assert any("primary_metric" in n for n in notes)


def test_framing_clamps_bad_threshold():
    fixed, _ = validate_and_fix_framing(
        {"task_type": "binary_classification", "primary_metric": "auc_roc",
         "good_enough_threshold": 9.0, "target_column": "y"}, ["y"])
    assert fixed["good_enough_threshold"] == 0.8


def test_framing_flags_missing_target_column():
    _, notes = validate_and_fix_framing(
        {"task_type": "binary_classification", "primary_metric": "auc_roc",
         "good_enough_threshold": 0.7, "target_column": "nope"}, ["a", "b", "churned"])
    assert any("target_column" in n for n in notes)


def test_framing_valid_input_passes_clean():
    fixed, notes = validate_and_fix_framing(
        {"task_type": "binary_classification", "primary_metric": "recall",
         "good_enough_threshold": 0.7, "target_column": "churned"}, ["x", "churned"])
    assert notes == []
    assert fixed["primary_metric"] == "recall"
