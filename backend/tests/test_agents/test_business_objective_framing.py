"""Phase 3.5 — the framer's business-objective / rare-event-budget decisions.

Doctrine: the LLM DECIDES the cost model and the action budget from the goal; the
guardrail is the mechanic that stops a bad decision reaching the pipeline. These
tests pin the guardrail, because an unvalidated objective produces a confident,
wrong operating-point recommendation rather than an error.
"""
import pytest

from app.core import metric_registry as mreg
from app.core.guardrails import validate_and_fix_framing

COLS = ["age", "balance", "contacted", "converted"]


def _framing(**over):
    base = {"task_type": "binary_classification", "target_column": "converted",
            "primary_metric": "auc_roc", "good_enough_threshold": 0.8}
    base.update(over)
    return base


# ── rare_event_k ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("metric", ["precision_at_k", "recall_at_k", "lift_at_k"])
def test_valid_k_is_kept_for_at_k_metrics(metric):
    fixed, notes = validate_and_fix_framing(
        _framing(primary_metric=metric, rare_event_k=0.05), COLS)
    assert fixed["rare_event_k"] == 0.05
    assert not any("rare_event_k" in n for n in notes)


@pytest.mark.parametrize("bad", [0, -0.1, 1.5, "0.05", None, True])
def test_invalid_k_falls_back_to_default_with_a_note(bad):
    fixed, notes = validate_and_fix_framing(
        _framing(primary_metric="precision_at_k", rare_event_k=bad), COLS)
    assert fixed["rare_event_k"] == mreg.DEFAULT_K
    assert any("rare_event_k" in n for n in notes)


def test_missing_k_on_at_k_metric_gets_the_default():
    fixed, notes = validate_and_fix_framing(
        _framing(primary_metric="lift_at_k"), COLS)
    assert fixed["rare_event_k"] == mreg.DEFAULT_K
    assert any("rare_event_k" in n for n in notes)


def test_k_is_dropped_for_non_at_k_metrics():
    """Carrying a budget on an auc_roc run is noise that would confuse the UI."""
    fixed, _ = validate_and_fix_framing(
        _framing(primary_metric="auc_roc", rare_event_k=0.05), COLS)
    assert "rare_event_k" not in fixed


# ── business_objective ───────────────────────────────────────────────────────

def test_valid_objective_is_normalized_and_marked_llm_sourced():
    fixed, notes = validate_and_fix_framing(
        _framing(business_objective={"value_tp": 300, "cost_fp": -40, "currency": "USD"}),
        COLS)
    obj = fixed["business_objective"]
    assert obj["value_tp"] == 300.0
    assert obj["cost_fp"] == 40.0          # magnitude taken
    assert obj["currency"] == "USD"
    assert obj["source"] == "llm"          # provenance is recorded
    assert not notes


def test_degenerate_objective_is_dropped_not_used():
    """All-equal values mean no threshold beats any other. Returning an
    'optimal' threshold there would be a fabricated recommendation."""
    fixed, notes = validate_and_fix_framing(
        _framing(business_objective={"value_tp": 1, "cost_fp": 1,
                                     "cost_fn": 1, "value_tn": 1}), COLS)
    assert fixed["business_objective"] is None
    assert any("degenerate" in n for n in notes)


def test_absent_objective_stays_absent():
    """The framer is instructed NOT to invent unit economics. If it omitted the
    field, nothing should materialize one."""
    fixed, _ = validate_and_fix_framing(_framing(), COLS)
    assert fixed.get("business_objective") is None


def test_garbage_objective_does_not_crash_the_run():
    for junk in ("nonsense", 42, [], {"value_tp": "abc"}):
        fixed, _ = validate_and_fix_framing(_framing(business_objective=junk), COLS)
        # Either dropped as degenerate or coerced to defaults — never an exception.
        assert "business_objective" in fixed


# ── the framer prompt actually offers these ──────────────────────────────────

def test_framer_prompt_documents_the_new_fields():
    from app.agents.problem_framer import SYSTEM_PROMPT
    assert "rare_event_k" in SYSTEM_PROMPT
    assert "business_objective" in SYSTEM_PROMPT
    # The anti-fabrication instruction is the load-bearing part.
    assert "Do not invent numbers" in SYSTEM_PROMPT


def test_framer_menu_includes_at_k_for_binary_only():
    menu = mreg.framer_menu_text()
    binary_line = next(l for l in menu.splitlines() if l.startswith("- binary_classification"))
    regression_line = next(l for l in menu.splitlines() if l.startswith("- regression"))
    assert "precision_at_k" in binary_line
    assert "precision_at_k" not in regression_line


def test_state_declares_the_new_keys():
    """LangGraph silently drops undeclared state keys — a documented bug family."""
    from app.core.state import AgentState
    ann = AgentState.__annotations__
    assert "rare_event_k" in ann
    assert "business_objective" in ann
