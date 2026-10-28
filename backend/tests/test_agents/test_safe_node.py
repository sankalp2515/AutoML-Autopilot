"""Regression: an unhandled agent exception must not hard-crash the pipeline.

This is the exact failure from the field — baseline_builder raised KeyError('n_features')
(a RESULT key the sandbox self-repair couldn't see). The node boundary now converts
any such crash into a clean failed status the graph can route on.
"""
import pytest

from app.agents.orchestrator import _safe_run


class _Boom:
    name = "boomer"

    async def run(self, state):
        raise KeyError("n_features")


class _Ok:
    name = "fine"

    async def run(self, state):
        return {"status": "running", "value": 1}


@pytest.mark.asyncio
async def test_safe_run_converts_keyerror_to_failed_status():
    out = await _safe_run(_Boom(), {"run_id": "r1"})
    assert out["status"] == "failed"
    assert "boomer" in out["error"] and "n_features" in out["error"]


@pytest.mark.asyncio
async def test_safe_run_passes_through_success():
    out = await _safe_run(_Ok(), {"run_id": "r1"})
    assert out["status"] == "running" and out["value"] == 1
