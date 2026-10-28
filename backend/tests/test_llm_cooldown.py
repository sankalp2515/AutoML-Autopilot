"""Provider cooldown circuit-breaker (latency fix).

When a provider 429s, it must be put in a process-wide cooldown so the NEXT call
(next agent / concurrent run) skips straight to the fallback instead of re-paying
retries + Retry-After. This is the fix for the "every agent re-tries Groq then
falls back to Gemini" latency problem.
"""

import pytest

from app.core import llm as llmmod
from app.core.llm import LLMClient, _ProviderHTTPError


class _FakeProvider:
    def __init__(self, name, behavior):
        self.name = name
        self.model = f"{name}-model"
        self._behavior = behavior   # "429" | "ok"
        self.calls = 0

    async def chat(self, system, user, temperature, max_tokens):
        self.calls += 1
        if self._behavior == "429":
            raise _ProviderHTTPError(429, "rate limited", retry_after="60")
        return ("ok", 1, 1)


@pytest.fixture
def client(monkeypatch):
    # Isolate cooldown behavior from the completion cache: with caching on, a
    # second identical call would be served from cache and never reach a provider,
    # which is irrelevant to (and masks) the cooldown logic under test.
    from app.config import settings
    monkeypatch.setattr(settings, "LLM_CACHE_ENABLED", False)
    llmmod._completion_cache.clear()
    c = LLMClient()                 # GROQ test key exists in conftest env
    llmmod._provider_cooldown.clear()
    yield c
    llmmod._provider_cooldown.clear()


@pytest.mark.asyncio
async def test_429_cools_provider_and_falls_back(client):
    a = _FakeProvider("groq", "429")
    b = _FakeProvider("gemini", "ok")
    client.chain = [a, b]

    out = await client.complete("sys", "user")
    assert out == "ok"
    assert b.calls == 1
    assert a.calls == 1                       # tried once, no 3x retry storm
    assert llmmod._cooling("groq") is True     # now cooling


@pytest.mark.asyncio
async def test_subsequent_call_skips_cooling_provider(client):
    a = _FakeProvider("groq", "429")
    b = _FakeProvider("gemini", "ok")
    client.chain = [a, b]

    await client.complete("s", "u")            # cools groq
    a.calls = 0                                # reset to observe the 2nd call
    out = await client.complete("s", "u")      # should skip groq entirely

    assert out == "ok"
    assert a.calls == 0                        # groq skipped while cooling
    assert b.calls == 2                        # gemini served both


@pytest.mark.asyncio
async def test_healthy_provider_is_never_cooled(client):
    a = _FakeProvider("groq", "ok")
    client.chain = [a]
    await client.complete("s", "u")
    assert llmmod._cooling("groq") is False
