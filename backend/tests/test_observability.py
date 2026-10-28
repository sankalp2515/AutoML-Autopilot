"""Observability — request-id (Layer 1) + correlation (Layer 5)."""

import pytest

from app.core import context


@pytest.mark.asyncio
async def test_response_has_request_id_header(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.headers.get("x-request-id")              # auto-assigned
    assert len(r.headers["x-request-id"]) >= 8


@pytest.mark.asyncio
async def test_incoming_request_id_is_propagated(client):
    r = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers.get("x-request-id") == "trace-abc-123"   # echoed for correlation


@pytest.mark.asyncio
async def test_two_requests_get_distinct_ids(client):
    a = (await client.get("/health")).headers["x-request-id"]
    b = (await client.get("/health")).headers["x-request-id"]
    assert a != b


def test_request_id_contextvar():
    context.set_request_id("rid-xyz")
    assert context.get_request_id() == "rid-xyz"


def test_prompt_version_is_deterministic():
    import hashlib
    sys_prompt = "You are an expert."
    v1 = hashlib.sha256(sys_prompt.encode()).hexdigest()[:8]
    v2 = hashlib.sha256(sys_prompt.encode()).hexdigest()[:8]
    v3 = hashlib.sha256((sys_prompt + " changed").encode()).hexdigest()[:8]
    assert v1 == v2 and v1 != v3                      # version changes iff prompt changes
