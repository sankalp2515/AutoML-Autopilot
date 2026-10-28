"""Phase 0.1 regression: request-scoped context must isolate concurrent runs.

Before the fix, run/agent attribution lived on singleton instances and two
overlapping pipeline runs clobbered each other's cost + decision attribution.
contextvars are copied per asyncio task, so concurrent runs must NOT see each
other's values even while interleaving at await points.
"""

import asyncio

import pytest

from app.core import context


@pytest.mark.asyncio
async def test_context_is_isolated_across_concurrent_tasks():
    observed: dict[str, tuple[str | None, str]] = {}

    async def run(run_id: str, agent: str, hold: float):
        # Each task is its own contextvar copy (asyncio.create_task snapshots ctx).
        context.set_agent_context(run_id, agent, start_time=0.0)
        # Yield control so the two tasks interleave — the moment that broke
        # the old singleton implementation.
        await asyncio.sleep(hold)
        observed[run_id] = (context.get_run_id(), context.get_agent_name())

    await asyncio.gather(
        run("run-A", "preprocessor", hold=0.02),
        run("run-B", "tuner", hold=0.01),
    )

    assert observed["run-A"] == ("run-A", "preprocessor")
    assert observed["run-B"] == ("run-B", "tuner")


@pytest.mark.asyncio
async def test_context_defaults_are_safe_outside_a_run():
    # Reads outside any bound context must not raise (ad-hoc LLM calls, etc.).
    async def fresh():
        return context.get_run_id(), context.get_agent_name(), context.get_agent_start()

    run_id, agent, start = await asyncio.create_task(fresh())
    assert run_id is None
    assert agent == "unknown"
    assert start == 0.0
