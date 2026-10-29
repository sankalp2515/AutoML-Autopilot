import pytest
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI

from app.repair import routes
from tests.test_repair.test_service import store, create, candidate, receipt, repo_contract


async def test_task_api_and_cross_session_isolation(store):
    service, _ = store
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    headers = {"X-Autopilot-Session": "session_a_123456789"}
    other = {"X-Autopilot-Session": "session_b_123456789"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        payload = {"contract": repo_contract().model_dump(mode="json"), "base_commit_sha": "a" * 40,
                   "repo_snapshot_digest": "sha256:" + "b" * 64, "target": "Restore training"}
        response = await client.post("/api/v1/v5/tasks", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        task = response.json()
        prefix = f"/api/v1/v5/tasks/{task['id']}"
        assert (await client.get("/api/v1/v5/tasks", headers=headers)).json() == [task]
        assert (await client.get("/api/v1/v5/tasks", headers=other)).json() == []
        for suffix in ("", "/events", "/candidates"):
            assert (await client.get(prefix + suffix, headers=other)).status_code == 404
        assert (await client.post(prefix + "/cancel", headers=other)).status_code == 404
        assert (await client.get(prefix + "/events?after=1", headers=headers)).json() == []
        assert (await client.get(prefix + "/events?after=-1", headers=headers)).status_code == 422
        assert (await client.post(prefix + "/cancel", json={"version": 99}, headers=headers)).status_code == 409
        assert (await client.post(prefix + "/cancel", json={"version": 0}, headers=headers)).json()["status"] == "CANCELLED"
        assert (await client.get(prefix + "/events?after=1", headers=headers)).json()[0]["type"] == "task.cancelled"


async def test_candidate_diff_receipt_and_task_binding(store):
    service, _ = store
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    app.dependency_overrides[routes.autopilot_tenant] = lambda: "tenant-a"
    task = await create(service)
    unrelated = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cand = candidate(task)
    lease = await service.save_candidate(lease, cand)
    await service.save_receipt(lease, receipt(task, cand))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        prefix = f"/api/v1/v5/candidates/{cand.candidate_id}"
        assert (await client.get(prefix + "/diff")).text == cand.diff
        assert (await client.get(prefix + "/receipt")).json()["code"]["decision"] == "VERIFIED"
        wrong_task = f"/api/v1/v5/tasks/{unrelated['id']}/candidates/{cand.candidate_id}"
        for suffix in ("/diff", "/receipt"):
            assert (await client.get(wrong_task + suffix)).status_code == 404
        app.dependency_overrides[routes.autopilot_tenant] = lambda: "tenant-b"
        for suffix in ("/diff", "/receipt"):
            assert (await client.get(prefix + suffix)).status_code == 404


async def test_create_rejects_unfrozen_contract_data(store):
    service, _ = store
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post("/api/v1/v5/tasks", json={"ecc": {"status": "VERIFIED"}})).status_code == 422
