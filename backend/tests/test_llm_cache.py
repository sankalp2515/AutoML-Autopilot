"""LLM completion cache (opt-in) — identical prompts skip the provider."""

import pytest

from app.config import settings
from app.core import llm as llmmod
from app.core.llm import LLMClient


class _CountingProvider:
    def __init__(self):
        self.name = "groq"
        self.model = "m"
        self.calls = 0

    async def chat(self, system, user, temperature, max_tokens):
        self.calls += 1
        return (f"resp-{self.calls}", 1, 1)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CACHE_ENABLED", True)
    monkeypatch.setattr(settings, "LLM_CACHE_TTL_S", 3600)
    llmmod._completion_cache.clear()
    llmmod._provider_cooldown.clear()
    c = LLMClient()
    yield c
    llmmod._completion_cache.clear()


@pytest.mark.asyncio
async def test_identical_prompt_is_cached(client):
    p = _CountingProvider()
    client.chain = [p]
    a = await client.complete("sys", "same question")
    b = await client.complete("sys", "same question")
    assert a == b == "resp-1"
    assert p.calls == 1                       # 2nd call served from cache


@pytest.mark.asyncio
async def test_different_prompt_not_cached(client):
    p = _CountingProvider()
    client.chain = [p]
    await client.complete("sys", "q1")
    await client.complete("sys", "q2")
    assert p.calls == 2


@pytest.mark.asyncio
async def test_cache_disabled_always_calls(monkeypatch):
    monkeypatch.setattr(settings, "LLM_CACHE_ENABLED", False)
    llmmod._completion_cache.clear()
    llmmod._provider_cooldown.clear()
    c = LLMClient()
    p = _CountingProvider()
    c.chain = [p]
    await c.complete("s", "q")
    await c.complete("s", "q")
    assert p.calls == 2
