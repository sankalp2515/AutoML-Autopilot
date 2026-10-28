"""P1.8 resume plumbing + the feature-engineer salvage path + P1.6 parsing."""

import asyncio

import pytest

from app.agents import orchestrator as orch
from app.core import investigation as inv
from langgraph.graph import END


# ── Salvage routing ───────────────────────────────────────────────────────────

def test_fe_failure_with_model_salvages():
    state = {"status": "failed", "current_score": 0.83,
             "tuned_model_path": "/data/x/artifacts/tuned_model.pkl"}
    assert orch.route_after_feature_engineer(state) == "salvage"


def test_fe_failure_without_model_ends():
    assert orch.route_after_feature_engineer({"status": "failed"}) == END
    assert orch.route_after_feature_engineer(
        {"status": "failed", "current_score": 0.8}) == END  # score but no model


def test_fe_success_continues():
    assert orch.route_after_feature_engineer({"status": "running"}) == "model_selector"


def test_salvage_node_clears_failure(monkeypatch):
    async def _noop(*a, **k):
        return None
    monkeypatch.setattr(orch, "publish_progress", _noop)
    out = asyncio.run(orch.node_salvage({"run_id": "t", "status": "failed",
                                         "error": "LLM providers failed"}))
    assert out["status"] == "running"
    assert out["error"] is None


# ── Resume plumbing ───────────────────────────────────────────────────────────

def test_build_graph_accepts_resume_entry():
    # Any resumable node compiles as an entry point; unknown falls back safely.
    for entry in ("feature_engineer", "tuner", "evaluator"):
        assert orch.build_graph(entry_point=entry) is not None
    assert orch.build_graph(entry_point="not_a_node") is not None


def test_resumable_nodes_match_agent_names():
    # AgentStep.agent_name is the resume key — every graph agent must map 1:1.
    for name in ("data_auditor", "problem_framer", "data_splitter", "baseline_builder",
                 "eda_agent", "preprocessor", "feature_engineer", "model_selector",
                 "tuner", "evaluator", "exporter"):
        assert name in orch.RESUMABLE_NODES


# ── P1.6 custom_code escalation parsing / budgeting ──────────────────────────

def _hyp(prim, params, sev="high"):
    return {"id": "h1", "statement": "s" * 30, "severity": sev, "primitive": prim,
            "params": params, "expected": {"direction": ">", "threshold": 0.5}}


def test_parse_accepts_custom_code_with_description():
    got = inv.parse_hypotheses({"hypotheses": [
        _hyp("custom_code", {"description": "compare score on rows where amount > p99 vs rest"}),
    ]})
    assert len(got) == 1 and got[0]["primitive"] == "custom_code"


def test_parse_rejects_custom_code_without_description():
    got = inv.parse_hypotheses({"hypotheses": [
        _hyp("custom_code", {}),
        _hyp("custom_code", {"description": "too short"}),
    ]})
    assert got == []


def test_select_caps_custom_code():
    hyps = [dict(_hyp("custom_code",
                      {"description": f"a sufficiently long description number {i}"}),
                 id=f"h{i}") for i in range(4)]
    picked = inv.select(hyps)
    assert sum(1 for h in picked if h["primitive"] == "custom_code") == inv.MAX_CUSTOM_PER_ROUND
