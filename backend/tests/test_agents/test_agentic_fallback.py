"""Phase 2 — uniform template-first → agentic-repair fallback.

`try_agentic_repair` is wired into every template-running agent. Its contract:
  - On a SUCCESSFUL sandbox result it is a pure no-op (returns it unchanged and
    NEVER calls the LLM) — so the happy path costs nothing.
  - On a FAILED result it delegates to execute_code_agentic with the agent's
    role/contract so the agent can write its own fix.
"""

from unittest.mock import AsyncMock

import pytest

from app.agents.base_agent import BaseAgent


class _Agent(BaseAgent):
    name = "test_agent"


@pytest.fixture
def agent():
    a = _Agent()
    a.execute_code_agentic = AsyncMock(
        return_value={"success": True, "result": {"x": 1}, "agentic_used": True}
    )
    return a


@pytest.mark.asyncio
async def test_success_is_a_noop_no_llm(agent):
    ok = {"success": True, "result": {"final_score": 0.9}, "stdout": "", "error": ""}
    out = await agent.try_agentic_repair(
        "rid", "CODE", ok, result_keys=["final_score"], goal="g")
    assert out is ok                       # returned unchanged
    agent.execute_code_agentic.assert_not_awaited()  # zero LLM cost on the happy path


@pytest.mark.asyncio
async def test_failure_delegates_to_agentic(agent):
    bad = {"success": False, "result": None, "stdout": "", "error": "ValueError: boom"}
    out = await agent.try_agentic_repair(
        "rid", "CODE", bad, result_keys=["a", "b"], goal="do the thing",
        task_type="regression", tags=["test_agent", "x"], timeout=99)

    assert out["success"] is True and out["agentic_used"] is True
    agent.execute_code_agentic.assert_awaited_once()
    kwargs = agent.execute_code_agentic.await_args.kwargs
    assert kwargs["agent_role"] == "test_agent"
    assert kwargs["result_keys"] == ["a", "b"]
    assert kwargs["task_type"] == "regression"
    assert kwargs["tags"] == ["test_agent", "x"]
    assert kwargs["timeout"] == 99


@pytest.mark.asyncio
async def test_default_tags_fall_back_to_agent_name(agent):
    bad = {"success": False, "result": None, "stdout": "", "error": "boom"}
    await agent.try_agentic_repair("rid", "CODE", bad, result_keys=["a"], goal="g")
    assert agent.execute_code_agentic.await_args.kwargs["tags"] == ["test_agent"]
