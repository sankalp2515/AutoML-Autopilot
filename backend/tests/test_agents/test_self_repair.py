"""
Tier-1 self-repair micro-loop tests.

We stub execute_code (so no real sandbox) and the LLM repair call, then assert:
  1. A failure that the revised params fix → recovers, repaired=True.
  2. A failure the LLM can't fix → exhausts at MAX_REPAIRS, repaired=False.
  3. A first-try success → no repair attempts, no LLM call.
"""

from unittest.mock import AsyncMock

import pytest

from app.agents.base_agent import BaseAgent


class _Agent(BaseAgent):
    name = "test_agent"


def _ok(result=None):
    return {"success": True, "result": result or {"ok": 1}, "stdout": "", "error": ""}


def _fail(msg="ValueError: bad column 'X'"):
    return {"success": False, "result": None, "stdout": "", "error": msg}


@pytest.fixture
def agent():
    a = _Agent()
    # Silence side-effects unrelated to the loop logic
    a.emit = AsyncMock()
    a._log_decision = AsyncMock()
    return a


@pytest.mark.asyncio
async def test_repair_recovers(agent):
    # render encodes the param into the code string; execute "fails" until
    # the param becomes "good".
    def render(p: dict) -> str:
        return f"PARAM={p['encoding']}"

    calls = {"n": 0}

    async def fake_exec(code, run_id, timeout):
        calls["n"] += 1
        return _ok() if "good" in code else _fail()

    agent.execute_code = fake_exec
    agent.llm.complete_json = AsyncMock(return_value={
        "diagnosis": "bad encoding",
        "revised_params": {"encoding": "good"},
        "change_summary": "switch to good",
    })

    res = await agent.execute_code_with_repair(
        "run1", render, {"encoding": "bad"}, repair_goal="test", timeout=5,
    )

    assert res["success"] is True
    assert res["repaired"] is True
    assert res["repair_attempts"] == 1
    assert res["final_params"]["encoding"] == "good"
    assert calls["n"] == 2  # first fail + one repaired success


@pytest.mark.asyncio
async def test_repair_exhausts(agent):
    def render(p: dict) -> str:
        return f"PARAM={p['encoding']}"

    async def always_fail(code, run_id, timeout):
        return _fail()

    agent.execute_code = always_fail
    # LLM keeps proposing changes, but the code fails regardless
    agent.llm.complete_json = AsyncMock(return_value={
        "diagnosis": "still bad",
        "revised_params": {"encoding": "other"},
        "change_summary": "try other",
    })

    res = await agent.execute_code_with_repair(
        "run1", render, {"encoding": "bad"}, repair_goal="test", timeout=5,
        max_repairs=2,
    )

    assert res["success"] is False
    assert res["repaired"] is False
    assert res["repair_attempts"] == 2  # capped
    # 1 initial + 2 repair re-runs = 3 executions
    assert agent.llm.complete_json.await_count == 2


@pytest.mark.asyncio
async def test_no_repair_on_first_success(agent):
    def render(p: dict) -> str:
        return "code"

    agent.execute_code = AsyncMock(return_value=_ok())
    agent.llm.complete_json = AsyncMock()

    res = await agent.execute_code_with_repair(
        "run1", render, {"x": 1}, repair_goal="test",
    )

    assert res["success"] is True
    assert res["repaired"] is False
    assert res["repair_attempts"] == 0
    agent.llm.complete_json.assert_not_awaited()  # LLM never consulted
