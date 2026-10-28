import pytest
from unittest.mock import AsyncMock, patch


@pytest.mark.asyncio
async def test_health_returns_status(client):
    with (
        patch("app.api.routes.health.get_redis") as mock_redis_factory,
        patch("app.api.routes.health.get_executor") as mock_executor_factory,
    ):
        mock_redis = AsyncMock()
        mock_redis.ping = AsyncMock(return_value=True)
        mock_redis_factory.return_value = mock_redis

        mock_executor = AsyncMock()
        mock_executor.health = AsyncMock(return_value=True)
        mock_executor_factory.return_value = mock_executor

        response = await client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert "status" in data
    assert "checks" in data
    assert "postgres" in data["checks"]
    assert "redis" in data["checks"]
    assert "sandbox" in data["checks"]


@pytest.mark.asyncio
async def test_root_endpoint(client):
    response = await client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "service" in data
    assert "version" in data
