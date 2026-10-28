"""Worker mode: the API only queues; a worker claims by lease, runs, and fails safe."""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.autopilot import guard, service, worker
from app.autopilot.models import AutopilotInvestigation
from app.config import settings
from app.database import AsyncSessionLocal

BASE = "/api/v1/autopilot"


@pytest.fixture(autouse=True)
def _worker_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "AUTOPILOT_EXECUTION", "worker")
    monkeypatch.setattr(settings, "AUTOPILOT_RUNNER", "inline")
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path))
    for k in ("TAVILY_API_KEY", "NEBIUS_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setattr(settings, k, "")
    guard.reset()


def _h():
    return {"X-Autopilot-Session": uuid.uuid4().hex}


async def test_api_only_queues_and_the_worker_runs_to_a_verdict(client):
    h = _h()
    r = await client.post(f"{BASE}/investigations", json={"scenario_id": "S02"}, headers=h)
    rid = r.json()["run_id"]
    s = (await client.get(f"{BASE}/investigations/{rid}", headers=h)).json()
    assert s["status"] == "CREATED" and s["queue_position"] >= 1           # nothing ran in the API
    await worker.run_forever(once=True)
    s = (await client.get(f"{BASE}/investigations/{rid}", headers=h)).json()
    assert s["decision"]["decision"] == "CORRECTION_VERIFIED" and s["queue_position"] is None
    async with AsyncSessionLocal() as db:
        row = await db.get(AutopilotInvestigation, rid)
        assert row.lease_owner is None                                       # lease released


async def test_two_claims_never_take_the_same_investigation(client):
    h = _h()
    ids = [(await client.post(f"{BASE}/investigations", json={"scenario_id": "S01"}, headers=h)).json()["run_id"]
           for _ in range(2)]
    first, second = await worker.claim(), await worker.claim()
    assert first and second and first[0] != second[0]
    assert {first[0], second[0]} == set(ids)
    async with AsyncSessionLocal() as db:                                    # release for other tests
        for rid in (first[0], second[0]):
            row = await db.get(AutopilotInvestigation, rid)
            row.status, row.lease_owner = "CANCELLED", None
        await db.commit()


async def test_step_mode_goes_through_the_queue_one_stage_at_a_time(client):
    h = _h()
    rid = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S02", "auto_run": False},
                             headers=h)).json()["run_id"]
    s = (await client.post(f"{BASE}/investigations/{rid}/next", headers=h)).json()
    assert s["status"] == "CREATED" and s["step_pending"] is True
    await worker.run_forever(once=True)
    s = (await client.get(f"{BASE}/investigations/{rid}", headers=h)).json()
    assert s["status"] == "VALIDATING" and s["step_pending"] is False


async def test_an_abandoned_lease_fails_safe_and_never_reports_success(client):
    h = _h()
    rid = (await client.post(f"{BASE}/investigations", json={"scenario_id": "S02"}, headers=h)).json()["run_id"]
    async with AsyncSessionLocal() as db:                                    # a worker died mid-run
        row = await db.get(AutopilotInvestigation, rid)
        row.status, row.lease_owner = "EXECUTING", "dead-worker:1"
        row.lease_until = datetime.now(timezone.utc) - timedelta(seconds=5)
        await db.commit()
    assert await worker.fail_abandoned() >= 1
    s = (await client.get(f"{BASE}/investigations/{rid}", headers=h)).json()
    assert s["status"] == "FAILED" and s["decision"]["reason_codes"] == ["E_INTERRUPTED"]
    assert s["decision"]["incumbent_changed"] is False


async def test_providers_come_from_the_worker_heartbeat(client):
    await worker.heartbeat(active=0)
    p = (await client.get(f"{BASE}/providers")).json()
    assert p["execution"] == "worker" and p["worker"]["online"] is True and p["worker"]["id"] == worker.WORKER_ID
    assert "key" not in str(p).lower().replace("configured", "")


def test_verifier_service_requires_token_and_a_run_id_not_a_path(monkeypatch, tmp_path):
    from app.autopilot import verifier_service
    monkeypatch.setattr(settings, "AUTOPILOT_VERIFIER_TOKEN", "t0ken")
    c = TestClient(verifier_service.app)
    assert c.get("/health").json()["service"] == "verifier"
    body = {"run_id": "../../etc", "candidate": {"model_path": "/etc/passwd"}, "mode": "improve",
            "expected_protocol_hash": "x", "may_open": True}
    assert c.post("/verify", json=body).status_code == 401
    assert c.post("/verify", json=body, headers={"X-Verifier-Token": "wrong"}).status_code == 401
    assert c.post("/verify", json=body, headers={"X-Verifier-Token": "t0ken"}).status_code == 422
    body["run_id"] = str(uuid.uuid4())
    assert c.post("/verify", json=body, headers={"X-Verifier-Token": "t0ken"}).status_code == 404
