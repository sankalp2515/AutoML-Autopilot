"""P1.7 dynamic planner — validation clamps + safe-by-default routing."""

from app.core import planner


def test_default_plan_no_skips():
    p = planner.default_plan(3)
    assert p["skip_feature_engineer"] is False
    assert p["skip_tuner"] is False
    assert p["max_iterations"] == 3


def test_validate_clamps_iterations():
    assert planner.validate_plan({"max_iterations": 99}, 3)["max_iterations"] == 3
    assert planner.validate_plan({"max_iterations": -5}, 3)["max_iterations"] == 0
    assert planner.validate_plan({"max_iterations": 2}, 3)["max_iterations"] == 2


def test_validate_accepts_valid_skips():
    p = planner.validate_plan({"skip_feature_engineer": True, "skip_tuner": True,
                               "max_iterations": 1, "rationale": "tiny data"}, 3)
    assert p["skip_feature_engineer"] and p["skip_tuner"]
    assert p["max_iterations"] == 1
    assert p["source"] == "strategist"


def test_validate_bad_input_falls_back():
    for bad in (None, [], "nope", 42, {"skip_tuner": "yes"}):
        p = planner.validate_plan(bad, 3)
        assert p["max_iterations"] == 3
        assert p["skip_tuner"] is False        # non-bool → False


def test_validate_nested_plan_key():
    p = planner.validate_plan({"plan": {"skip_tuner": True, "max_iterations": 2}}, 3)
    assert p["skip_tuner"] and p["max_iterations"] == 2


def test_plan_summary_readable():
    s = planner.plan_summary(planner.validate_plan(
        {"skip_feature_engineer": True, "max_iterations": 1}, 3))
    assert "skipping feature engineering" in s
    assert "1 improvement" in s
