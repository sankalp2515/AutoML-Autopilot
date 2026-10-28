"""Sandbox timeout clamp — no agent can 400 the sandbox with timeout > 600.

The never-run streaming candidate requested timeout=900; the sandbox rejects any
timeout > 600 with HTTP 400, failing the whole agent. The executor now clamps to
[1, 600] so this class of bug cannot recur.
"""
import pytest

from app.sandbox.executor import SandboxExecutor


class _CapturingClient:
    """Stand-in httpx.AsyncClient that records the payload instead of calling out."""
    captured = {}

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json):
        _CapturingClient.captured = json

        class _Resp:
            def raise_for_status(self):
                pass

            def json(self):
                return {"success": True, "result": {}}
        return _Resp()


@pytest.mark.asyncio
@pytest.mark.parametrize("requested,expected", [(900, 600), (5000, 600), (0, 1), (-3, 1), (120, 120), (600, 600)])
async def test_timeout_is_clamped(monkeypatch, requested, expected):
    import app.sandbox.executor as ex
    monkeypatch.setattr(ex.httpx, "AsyncClient", _CapturingClient)
    monkeypatch.setattr(ex, "get_executor", lambda: SandboxExecutor(), raising=False)
    await SandboxExecutor().execute("RESULT = {}", "run1", timeout=requested)
    assert _CapturingClient.captured["timeout"] == expected
