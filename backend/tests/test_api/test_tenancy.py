"""Phase 4 — multi-tenant auth + isolation + quota.

Verifies the safe-by-default behavior (public mode = no change) and, when tenant
keys are configured, correct resolution + cross-tenant isolation at the query level.
"""

import pytest

from app.config import settings
from app.core import auth
from app.models.run import Run


def _enable_tenants(monkeypatch, mapping_json):
    monkeypatch.setattr(settings, "AUTH_ENABLED", True)
    monkeypatch.setattr(settings, "TENANT_API_KEYS", mapping_json)
    auth._key_map.cache_clear()


@pytest.fixture(autouse=True)
def _reset_key_cache():
    auth._key_map.cache_clear()
    yield
    auth._key_map.cache_clear()


def test_public_mode_is_default():
    assert auth.tenant_mode_enabled() is False
    assert auth.resolve_tenant(None) == "public"
    assert auth.resolve_tenant("anything") == "public"


def test_tenant_mode_resolution(monkeypatch):
    _enable_tenants(monkeypatch, '{"key_acme": "acme", "key_globex": "globex"}')
    assert auth.tenant_mode_enabled() is True
    assert auth.resolve_tenant("key_acme") == "acme"
    assert auth.resolve_tenant("key_globex") == "globex"
    assert auth.resolve_tenant("bogus") is None     # unknown key
    assert auth.resolve_tenant(None) is None         # missing key


def test_malformed_key_map_is_ignored(monkeypatch):
    _enable_tenants(monkeypatch, "{not valid json")
    assert auth.tenant_mode_enabled() is False
    assert auth.resolve_tenant(None) == "public"


@pytest.mark.asyncio
async def test_runs_are_isolated_by_tenant(db_session):
    # Two tenants' runs must not bleed across a tenant-filtered query.
    from sqlalchemy import select

    db_session.add_all([
        Run(id="r-acme-1", tenant_id="acme", dataset_filename="a.csv",
            dataset_path="/d/a", user_goal="goal aaaaaaaaaa"),
        Run(id="r-globex-1", tenant_id="globex", dataset_filename="g.csv",
            dataset_path="/d/g", user_goal="goal bbbbbbbbbb"),
    ])
    await db_session.commit()

    acme = (await db_session.execute(
        select(Run).where(Run.tenant_id == "acme"))).scalars().all()
    assert [r.id for r in acme] == ["r-acme-1"]
    assert "r-globex-1" not in [r.id for r in acme]


@pytest.mark.asyncio
async def test_active_run_quota_counts_only_active(db_session):
    from sqlalchemy import func, select

    db_session.add_all([
        Run(id="q1", tenant_id="t", status="queued", dataset_filename="x.csv",
            dataset_path="/d/x", user_goal="goal cccccccccc"),
        Run(id="q2", tenant_id="t", status="running", dataset_filename="x.csv",
            dataset_path="/d/x", user_goal="goal dddddddddd"),
        Run(id="q3", tenant_id="t", status="completed", dataset_filename="x.csv",
            dataset_path="/d/x", user_goal="goal eeeeeeeeee"),
    ])
    await db_session.commit()

    active = (await db_session.execute(
        select(func.count()).select_from(Run).where(
            Run.tenant_id == "t", Run.status.in_(("queued", "running"))))).scalar()
    assert active == 2  # completed run does not count against the quota


@pytest.mark.asyncio
async def test_tenant_cost_budget_sums_only_that_tenant(db_session):
    from sqlalchemy import func, select
    from app.models.run import LLMCall

    db_session.add_all([
        Run(id="cb-a", tenant_id="acme", dataset_filename="a.csv", dataset_path="/d/a",
            user_goal="goal aaaaaaaaaa"),
        Run(id="cb-b", tenant_id="globex", dataset_filename="b.csv", dataset_path="/d/b",
            user_goal="goal bbbbbbbbbb"),
    ])
    await db_session.flush()
    db_session.add_all([
        LLMCall(run_id="cb-a", agent_name="x", provider="p", model="m", estimated_cost_usd=0.30),
        LLMCall(run_id="cb-a", agent_name="y", provider="p", model="m", estimated_cost_usd=0.25),
        LLMCall(run_id="cb-b", agent_name="z", provider="p", model="m", estimated_cost_usd=9.0),
    ])
    await db_session.commit()

    spent = (await db_session.execute(
        select(func.coalesce(func.sum(LLMCall.estimated_cost_usd), 0.0))
        .select_from(LLMCall).join(Run, LLMCall.run_id == Run.id)
        .where(Run.tenant_id == "acme"))).scalar()
    assert round(float(spent), 2) == 0.55   # globex's $9 is NOT counted
