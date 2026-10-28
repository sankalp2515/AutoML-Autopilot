import io
import pytest
from unittest.mock import AsyncMock, patch


def _make_csv() -> bytes:
    return b"age,income,churned\n30,50000,0\n45,75000,1\n25,40000,0\n"


@pytest.mark.asyncio
async def test_create_run_rejects_non_csv(client):
    data = {"user_goal": "predict churn for customers"}
    files = {"file": ("data.txt", io.BytesIO(b"not a csv"), "text/plain")}
    response = await client.post("/api/v1/runs", data=data, files=files)
    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]


@pytest.mark.asyncio
async def test_create_run_rejects_short_goal(client):
    files = {"file": ("data.csv", io.BytesIO(_make_csv()), "text/csv")}
    data = {"user_goal": "predict"}
    response = await client.post("/api/v1/runs", data=data, files=files)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_run_success(client):
    with patch("app.api.routes.runs.submit_run", new_callable=AsyncMock):
        files = {"file": ("titanic.csv", io.BytesIO(_make_csv()), "text/csv")}
        data = {
            "user_goal": "Predict whether a customer will churn next month",
            "exclude_columns": "",
            "fp_fn_preference": "missing a churner is worse than a false alarm",
            "interpretability_required": "false",
        }
        response = await client.post("/api/v1/runs", data=data, files=files)

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["status"] == "queued"
    assert body["dataset_filename"] == "titanic.csv"
    return body["id"]


@pytest.mark.asyncio
async def test_get_run_not_found(client):
    response = await client.get("/api/v1/runs/nonexistent-id")
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_run_exists(client):
    with patch("app.api.routes.runs.submit_run", new_callable=AsyncMock):
        files = {"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")}
        data = {"user_goal": "Predict customer churn from billing data"}
        create_resp = await client.post("/api/v1/runs", data=data, files=files)

    assert create_resp.status_code == 201
    run_id = create_resp.json()["id"]

    response = await client.get(f"/api/v1/runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == run_id
    assert "agent_steps" in body
    assert "decision_logs" in body


@pytest.mark.asyncio
async def test_list_runs(client):
    response = await client.get("/api/v1/runs")
    assert response.status_code == 200
    body = response.json()
    assert "runs" in body
    assert "total" in body
    assert isinstance(body["runs"], list)


@pytest.mark.asyncio
async def test_results_returns_409_if_not_complete(client):
    with patch("app.api.routes.runs.submit_run", new_callable=AsyncMock):
        files = {"file": ("test.csv", io.BytesIO(_make_csv()), "text/csv")}
        data = {"user_goal": "Predict which loans will default next quarter"}
        create_resp = await client.post("/api/v1/runs", data=data, files=files)

    run_id = create_resp.json()["id"]
    response = await client.get(f"/api/v1/runs/{run_id}/results")
    assert response.status_code == 409
