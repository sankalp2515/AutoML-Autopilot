"""End-to-end tests — run against a REAL running stack, not mocks.

Why a separate tier. The unit/integration suite mocks the sandbox, the LLM and
Redis. That is fast and deterministic, and it has repeatedly failed to catch the
bugs that mattered: the holdout leak, the streaming timeout, the empty
target_classes, the class_weight/partial_fit incompatibility. Every one of those
needed the REAL path. This tier is the standing version of "run the real thing".

Two tiers, because a full pipeline run costs LLM tokens and takes minutes:

  SMOKE  (`e2e` marker)      — no LLM, no training. Health, studios, contract,
                               validation, auth, 404/409 handling. Fast enough to
                               run on every push in CI.
  FULL   (`e2e_full` marker) — uploads a real CSV and drives a complete pipeline
                               to a trained, trust-graded model. Needs an LLM key.
                               Nightly / on-demand, not per-push.

Both are SKIPPED unless E2E_BASE_URL is set, so `pytest` on a laptop with no stack
running behaves exactly as before.
"""
from __future__ import annotations

import os
import time

import httpx
import pytest

BASE_URL = os.getenv("E2E_BASE_URL", "").rstrip("/")
API_KEY = os.getenv("E2E_API_KEY", "")
# A full run does data audit → framing → split → baseline → EDA → preprocess →
# features → selection → tuning → evaluation → trust, each with LLM latency.
FULL_RUN_TIMEOUT_S = int(os.getenv("E2E_FULL_TIMEOUT", "1800"))

requires_stack = pytest.mark.skipif(
    not BASE_URL,
    reason="E2E_BASE_URL not set — start the stack and export it to run E2E tests",
)


def pytest_configure(config):
    config.addinivalue_line("markers", "e2e: smoke E2E against a live stack (no LLM)")
    config.addinivalue_line("markers", "e2e_full: full pipeline E2E (needs an LLM key)")


@pytest.fixture(scope="session")
def base_url() -> str:
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL not set")
    return BASE_URL


@pytest.fixture(scope="session")
def client(base_url):
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    with httpx.Client(base_url=base_url, headers=headers, timeout=60.0) as c:
        yield c


@pytest.fixture(scope="session")
def api(client):
    """Client rooted at /api/v1 — the versioned surface the frontend uses."""
    class _Api:
        def __init__(self, c):
            self._c = c

        def __getattr__(self, verb):
            def call(path, **kw):
                return getattr(self._c, verb)(f"/api/v1{path}", **kw)
            return call
    return _Api(client)


def wait_for_run(api, run_id: str, timeout_s: int = FULL_RUN_TIMEOUT_S,
                 poll_s: float = 5.0) -> dict:
    """Poll a run to a terminal state. Returns the final run detail.

    Fails loudly with the last known status and error rather than timing out
    silently — a silent E2E timeout tells you nothing about what broke.
    """
    deadline = time.time() + timeout_s
    last: dict = {}
    transient = 0
    while time.time() < deadline:
        # In-process execution (USE_JOB_QUEUE=false) means a CPU-bound agent can
        # block the event loop long enough for the server to drop an idle
        # keep-alive connection. That is a transport hiccup, not a run failure —
        # retry it rather than failing a 20-minute test on a closed socket.
        try:
            r = api.get(f"/runs/{run_id}")
        except (httpx.RemoteProtocolError, httpx.ReadError,
                httpx.ConnectError, httpx.ReadTimeout) as exc:
            transient += 1
            if transient > 10:
                pytest.fail(f"run {run_id}: {transient} consecutive transport errors, "
                            f"last was {type(exc).__name__}: {exc}")
            time.sleep(poll_s)
            continue
        transient = 0
        if r.status_code != 200:
            pytest.fail(f"run {run_id} became unreadable: HTTP {r.status_code} {r.text[:300]}")
        last = r.json()
        status = last.get("status")
        if status in ("completed", "failed", "cancelled"):
            return last
        time.sleep(poll_s)
    pytest.fail(
        f"run {run_id} did not finish within {timeout_s}s. "
        f"last status={last.get('status')!r} step={last.get('current_step')!r} "
        f"error={str(last.get('error_message'))[:300]!r}"
    )
