"""Public-demo safety: per-browser sessions, create rate limits, daily provider budget.

The hosted demo is open without login until judging ends, so three things must hold:
one visitor cannot block the others, a script cannot run up the Nebius/Tavily bill,
and a visitor always sees why they are waiting. The create rate limit is in-process
(one API container); the daily budget is counted from receipts in the database.
"""
from __future__ import annotations

import re
import time
from collections import deque

from app.config import settings

_SESSION = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_creates: dict[str, deque[float]] = {}
_waiting: list[str] = []


def scoped_tenant(tenant: str, public_tenant: str, session: str | None) -> str:
    """In public mode each browser session gets its own tenant, so one visitor's runs
    never count against another's limit. Authenticated tenants are unchanged."""
    if tenant == public_tenant and session and _SESSION.match(session):
        return f"anon:{session}"
    return tenant


def allow_create(client_key: str, now: float | None = None) -> bool:
    """Sliding window: AUTOPILOT_CREATES_PER_10MIN investigations per client per 10 minutes."""
    now = time.monotonic() if now is None else now
    q = _creates.setdefault(client_key, deque())
    while q and now - q[0] > 600:
        q.popleft()
    if len(q) >= settings.AUTOPILOT_CREATES_PER_10MIN:
        return False
    q.append(now)
    return True


def budget_left(used: dict[str, int]) -> dict[str, bool]:
    """`used` comes from receipts in the database (service.usage_today)."""
    return {"llm": used.get("llm", 0) < settings.AUTOPILOT_DAILY_LLM_CALLS,
            "tavily": used.get("tavily", 0) < settings.AUTOPILOT_DAILY_TAVILY_CALLS}


def usage(used: dict[str, int]) -> dict[str, int]:
    return {"llm_calls_today": used.get("llm", 0), "llm_daily_cap": settings.AUTOPILOT_DAILY_LLM_CALLS,
            "tavily_calls_today": used.get("tavily", 0), "tavily_daily_cap": settings.AUTOPILOT_DAILY_TAVILY_CALLS}


def enqueue(run_id: str) -> None:
    if run_id not in _waiting:
        _waiting.append(run_id)


def dequeue(run_id: str) -> None:
    if run_id in _waiting:
        _waiting.remove(run_id)


def queue_position(run_id: str) -> int | None:
    """1-based position among investigations waiting for a worker slot, or None if not waiting."""
    return _waiting.index(run_id) + 1 if run_id in _waiting else None


def reset() -> None:
    """Tests only."""
    _creates.clear()
    _waiting.clear()
