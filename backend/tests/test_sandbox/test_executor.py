import pytest
from unittest.mock import AsyncMock, patch, MagicMock
import httpx

from app.sandbox.executor import SandboxExecutor


@pytest.mark.asyncio
async def test_executor_sends_correct_payload():
    executor = SandboxExecutor(base_url="http://sandbox:8001")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "success": True,
        "result": {"shape": [100, 5]},
        "stdout": "",
        "error": "",
    }
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await executor.execute("RESULT = {'shape': [100, 5]}", "test-run", timeout=60)

    assert result["success"] is True
    assert result["result"]["shape"] == [100, 5]

    call_kwargs = mock_client.post.call_args
    assert call_kwargs[1]["json"]["code"] == "RESULT = {'shape': [100, 5]}"
    assert call_kwargs[1]["json"]["run_id"] == "test-run"
    assert call_kwargs[1]["json"]["timeout"] == 60


@pytest.mark.asyncio
async def test_executor_health_check_ok():
    executor = SandboxExecutor(base_url="http://sandbox:8001")

    mock_response = MagicMock()
    mock_response.status_code = 200

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value = mock_client

        result = await executor.health()

    assert result is True


@pytest.mark.asyncio
async def test_executor_health_check_failure():
    executor = SandboxExecutor(base_url="http://sandbox:8001")

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        mock_client_cls.return_value = mock_client

        result = await executor.health()

    assert result is False
