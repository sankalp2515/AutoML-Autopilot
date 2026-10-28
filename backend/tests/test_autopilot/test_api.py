"""Autopilot HTTP API: create/idempotency, polling to a verdict, receipts, review, step mode, cancel."""
import asyncio

import pytest

from app.autopilot.schemas import TERMINAL, Status
from app.config import settings
from app.core.auth import current_tenant
from app.main import app

BASE = "/api/v1/autopilot"


@pytest.fixture(autouse=True)
def _inline(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTOPILOT_RUNNER", "inline")
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "TAVILY_API_KEY", "")
    monkeypatch.setattr(settings, "NEBIUS_API_KEY", "")
    monkeypatch.setattr(settings, "NVIDIA_API_KEY", "")
    monkeypatch.setattr(settings, "AUTOPILOT_CREATES_PER_10MIN", 1000)
    from app.autopilot import guard
    guard.reset()


async def _wait(client, rid, timeout=60):
    for _ in range(timeout * 10):
        r = await client.get(f"{BASE}/investigations/{rid}")
        if Status(r.json()["status"]) in TERMINAL:
            return r.json()
        await asyncio.sleep(0.1)
    raise AssertionError("investigation did not finish")


async def test_scenarios_and_providers(client):
    r = await client.get(f"{BASE}/scenarios")
    assert r.status_code == 200 and len(r.json()["scenarios"]) >= 10
    p = (await client.get(f"{BASE}/providers")).json()
    assert p["nebius_token_factory"]["status"] == "on_hold" and p["tavily"]["configured"] is False
    assert "key" not in str(p).lower().replace("configured", "")


async def test_create_runs_to_verdict_and_receipt_has_no_server_paths(client):
    r = await client.post(f"{BASE}/investigations", json={"scenario_id": "S02"})
    assert r.status_code == 202
    s = await _wait(client, r.json()["run_id"])
    assert s["decision"]["decision"] == "CORRECTION_VERIFIED" and s["status"] == "REVIEW_READY"
    rec = (await client.get(f"{BASE}/investigations/{s['run_id']}/receipt")).json()
    text = str(rec)
    assert "model_path" not in text and str(settings.DATA_DIR) not in text
    assert rec["tests"][0]["template_id"] == "TRAIN_SERVE_PARITY"
    assert rec["alternatives"][0]["considered"]
    ev = (await client.get(f"{BASE}/investigations/{s['run_id']}/events")).json()["events"]
    assert [e["seq"] for e in ev] == sorted(e["seq"] for e in ev) and ev[-1]["kind"] == "transition"
    exp = await client.get(f"{BASE}/investigations/{s['run_id']}/export")
    assert "attachment" in exp.headers["content-disposition"]


async def test_idempotency_key_returns_the_same_run(client):
    h = {"Idempotency-Key": "demo-key-1"}
    a = await client.post(f"{BASE}/investigations", json={"scenario_id": "S01"}, headers=h)
    b = await client.post(f"{BASE}/investigations", json={"scenario_id": "S01"}, headers=h)
    assert a.json()["run_id"] == b.json()["run_id"] and b.json()["created"] is False
    await _wait(client, a.json()["run_id"])


async def test_validation_errors(client):
    assert (await client.post(f"{BASE}/investigations", json={"scenario_id": "NOPE"})).status_code == 422
    assert (await client.post(f"{BASE}/investigations", json={"scenario_id": "S01", "shell": "ls"})).status_code == 422
    assert (await client.post(f"{BASE}/investigations", json={"scenario_id": "S01", "max_experiments": 99})
            ).status_code == 422
    assert (await client.get(f"{BASE}/investigations/does-not-exist")).status_code == 404


async def test_review_only_on_verified_receipt_and_records_only(client):
    rid = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S03"})).json()["run_id"]
    await _wait(client, rid)
    ok = await client.post(f"{BASE}/investigations/{rid}/review", json={"action": "approve", "reviewer": "lead"})
    assert ok.status_code == 200 and ok.json()["status"] == "APPROVED"
    assert "no model alias" in ok.json()["review"]["effect"]
    again = await client.post(f"{BASE}/investigations/{rid}/review", json={"action": "approve", "reviewer": "x2"})
    assert again.status_code == 409

    rid2 = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S09"})).json()["run_id"]
    s2 = await _wait(client, rid2)
    assert s2["decision"]["decision"] == "REJECT"
    bad = await client.post(f"{BASE}/investigations/{rid2}/review", json={"action": "approve", "reviewer": "lead"})
    assert bad.status_code == 409


async def test_step_mode_and_cancel(client):
    rid = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S02", "auto_run": False})).json()["run_id"]
    s = (await client.post(f"{BASE}/investigations/{rid}/next")).json()
    assert s["status"] == "VALIDATING"
    s = (await client.post(f"{BASE}/investigations/{rid}/next")).json()
    assert s["status"] == "BASELINING"
    c = (await client.post(f"{BASE}/investigations/{rid}/cancel")).json()
    assert c["status"] == "CANCELLED"
    assert (await client.post(f"{BASE}/investigations/{rid}/next")).status_code == 409


async def test_other_tenant_cannot_read(client):
    rid = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S01"})).json()["run_id"]
    await _wait(client, rid)
    app.dependency_overrides[current_tenant] = lambda: "tenant-b"
    try:
        assert (await client.get(f"{BASE}/investigations/{rid}")).status_code == 404
        assert (await client.get(f"{BASE}/investigations/{rid}/receipt")).status_code == 404
    finally:
        app.dependency_overrides.pop(current_tenant, None)


async def test_per_tenant_cap_on_unfinished_investigations(client, monkeypatch):
    from app.autopilot import service
    monkeypatch.setattr(service, "MAX_ACTIVE_PER_TENANT", 1)
    app.dependency_overrides[current_tenant] = lambda: "tenant-cap"
    try:
        first = await client.post(f"{BASE}/investigations", json={"scenario_id": "S01", "auto_run": False})
        assert first.status_code == 202
        second = await client.post(f"{BASE}/investigations", json={"scenario_id": "S01", "auto_run": False})
        assert second.status_code == 429
        await client.post(f"{BASE}/investigations/{first.json()['run_id']}/cancel")
        third = await client.post(f"{BASE}/investigations", json={"scenario_id": "S01", "auto_run": False})
        assert third.status_code == 202
    finally:
        app.dependency_overrides.pop(current_tenant, None)


async def test_restart_mid_run_fails_safe_and_never_reports_success(client):
    """Kill-the-server drill: an auto run caught mid-flight by a restart ends FAILED with
    E_INTERRUPTED and the incumbent untouched; a step-mode run survives for the user."""
    from app.autopilot import service
    from app.autopilot.schemas import CreateInvestigation
    auto, _ = await service.create("restart-drill", CreateInvestigation(scenario_id="S02"), None)
    step, _ = await service.create("restart-drill", CreateInvestigation(scenario_id="S02", auto_run=False), None)
    await service.recover_orphans()                          # what the app lifespan runs on boot
    a = await service.get(auto["run_id"], "restart-drill")
    assert a["status"] == "FAILED" and a["decision"]["reason_codes"] == ["E_INTERRUPTED"]
    assert a["decision"]["incumbent_changed"] is False and a["verification"] is None
    s = await service.get(step["run_id"], "restart-drill")
    assert s["status"] == "CREATED"
