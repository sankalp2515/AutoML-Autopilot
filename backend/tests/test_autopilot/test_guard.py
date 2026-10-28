"""Public-demo guards: per-browser tenants, create rate limit, idle expiry, daily provider budget."""
import uuid

import pytest

from app.autopilot import guard, service
from app.config import settings
from app.core.auth import PUBLIC_TENANT

BASE = "/api/v1/autopilot"
S1, S2 = "a" * 32, "b" * 32          # for the pure-function tests


def _sid() -> str:
    return uuid.uuid4().hex             # API tests share one database: fresh visitor per test


@pytest.fixture(autouse=True)
def _demo(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTOPILOT_RUNNER", "inline")
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    for k in ("TAVILY_API_KEY", "NEBIUS_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setattr(settings, k, "")
    guard.reset()
    yield
    guard.reset()


def test_session_header_scopes_only_the_public_tenant():
    assert guard.scoped_tenant(PUBLIC_TENANT, PUBLIC_TENANT, S1) == f"anon:{S1}"
    assert guard.scoped_tenant("user:42", PUBLIC_TENANT, S1) == "user:42"          # logged-in users unchanged
    assert guard.scoped_tenant(PUBLIC_TENANT, PUBLIC_TENANT, "short") == PUBLIC_TENANT
    assert guard.scoped_tenant(PUBLIC_TENANT, PUBLIC_TENANT, "x" * 20 + "'; drop") == PUBLIC_TENANT


def test_create_rate_limit_is_a_sliding_window(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_CREATES_PER_10MIN", 2)
    assert guard.allow_create("ip", now=0) and guard.allow_create("ip", now=1)
    assert not guard.allow_create("ip", now=2)
    assert guard.allow_create("other", now=2)                 # per client
    assert guard.allow_create("ip", now=601)                  # window slides


def test_daily_budget_uses_counts_from_receipts(monkeypatch):
    monkeypatch.setattr(settings, "AUTOPILOT_DAILY_LLM_CALLS", 1)
    assert guard.budget_left({"llm": 0, "tavily": 0})["llm"]
    assert not guard.budget_left({"llm": 1, "tavily": 0})["llm"]
    assert guard.usage({"llm": 1})["llm_calls_today"] == 1


async def test_usage_today_is_counted_from_stored_receipts(client):
    from app.autopilot.schemas import CreateInvestigation
    st, _ = await service.create(f"anon:{_sid()}", CreateInvestigation(scenario_id="S01", auto_run=False), None)
    before = await service.usage_today()
    st["planner"]["calls"] = [{"correlation_id": "c1"}, {"correlation_id": "c2"}]
    st["research"]["calls"] = [{"correlation_id": "t1"}]
    await service._persist(st, None)
    after = await service.usage_today()
    assert after["llm"] - before["llm"] == 2 and after["tavily"] - before["tavily"] == 1


async def test_visitors_do_not_share_the_open_run_limit(client, monkeypatch):
    s1, s2 = _sid(), _sid()
    monkeypatch.setattr(service, "MAX_ACTIVE_PER_TENANT", 1)
    h1, h2 = {"X-Autopilot-Session": s1}, {"X-Autopilot-Session": s2}
    body = {"scenario_id": "S01", "auto_run": False}
    assert (await client.post(f"{BASE}/investigations", json=body, headers=h1)).status_code == 202
    assert (await client.post(f"{BASE}/investigations", json=body, headers=h1)).status_code == 429
    r = await client.post(f"{BASE}/investigations", json=body, headers=h2)
    assert r.status_code == 202                               # a second visitor is not blocked
    # and cannot read the first visitor's runs
    mine = (await client.get(f"{BASE}/investigations", headers=h2)).json()["investigations"]
    assert [x["run_id"] for x in mine] == [r.json()["run_id"]]


async def test_abandoned_step_mode_run_expires_and_frees_the_slot(client, monkeypatch):
    s1, s2 = _sid(), _sid()
    monkeypatch.setattr(service, "MAX_ACTIVE_PER_TENANT", 1)
    h = {"X-Autopilot-Session": s1}
    body = {"scenario_id": "S01", "auto_run": False}
    rid = (await client.post(f"{BASE}/investigations", json=body, headers=h)).json()["run_id"]
    monkeypatch.setattr(settings, "AUTOPILOT_IDLE_EXPIRY_MIN", -1)     # everything counts as idle
    assert (await client.post(f"{BASE}/investigations", json=body, headers=h)).status_code == 202
    old = (await client.get(f"{BASE}/investigations/{rid}", headers=h)).json()
    assert old["status"] == "CANCELLED" and old["decision"]["reason_codes"] == ["E_IDLE_EXPIRED"]
    assert old["decision"]["incumbent_changed"] is False


async def test_rate_limit_returns_a_readable_429(client, monkeypatch):
    s1, s2 = _sid(), _sid()
    monkeypatch.setattr(settings, "AUTOPILOT_CREATES_PER_10MIN", 1)
    h = {"X-Autopilot-Session": s1}
    body = {"scenario_id": "S01"}
    assert (await client.post(f"{BASE}/investigations", json=body, headers=h)).status_code == 202
    r = await client.post(f"{BASE}/investigations", json=body, headers=h)
    assert r.status_code == 429 and "per 10 minutes" in r.json()["detail"]
