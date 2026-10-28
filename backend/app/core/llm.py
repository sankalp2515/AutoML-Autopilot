"""
LLM client with rate-limit resilience.

Strategy:
  1. RETRY    — each provider gets up to 3 attempts with exponential backoff
                (honours Retry-After on 429s).
  2. FALLBACK — if a provider exhausts its retries, the next provider in the
                chain takes over: groq → gemini → openrouter → deepseek → ollama.
                Providers without an API key configured are skipped automatically.

All providers speak the OpenAI chat-completions dialect (Gemini and Ollama both
expose OpenAI-compatible endpoints), so one HTTP code path serves every provider.
Anthropic is also supported via its native SDK when configured.

Every call is instrumented: tokens, latency, estimated cost, and WHICH provider
actually answered — persisted to the llm_calls table and Prometheus.
"""

import asyncio
import json
import os
import random
import re
import time
from typing import Any

import httpx

from app.config import settings
from app.core import context
from app.core.logging import get_logger

_log = get_logger("llm")

# Approximate cost per 1M tokens (USD). Free tiers = $0.
_COST_PER_1M = {
    "llama-3.3-70b-versatile": {"prompt": 0.0, "completion": 0.0},
    "gemini-3.1-flash-lite": {"prompt": 0.0, "completion": 0.0},
    "deepseek-chat": {"prompt": 0.27, "completion": 1.10},
    "claude-opus-4-8": {"prompt": 15.0, "completion": 75.0},
    "claude-sonnet-4-6": {"prompt": 3.0, "completion": 15.0},
    "claude-haiku-4-5-20251001": {"prompt": 0.25, "completion": 1.25},
}

_MAX_RETRIES_PER_PROVIDER = 3
_BACKOFF_BASE_S = 1.2
_BACKOFF_CAP_S = 20.0
# Groq free tier = 12K tokens/MINUTE — a 429's Retry-After is often 30-60s.
# Waiting it out beats burning the retry budget and falling back prematurely.
_RETRY_AFTER_CAP_S = 75.0
_DEFAULT_COOLDOWN_S = 60.0

# Process-wide provider cooldown circuit-breaker. When a provider returns 429 we
# record "skip it until this epoch" so EVERY subsequent agent (and concurrent
# run) jumps straight to the next provider instead of re-paying 2 retries +
# Retry-After each time. Shared across the singleton LLM client = shared across
# all runs in the process. The single biggest latency fix under rate limiting.
_provider_cooldown: dict[str, float] = {}


def _cooling(name: str) -> bool:
    return time.time() < _provider_cooldown.get(name, 0.0)


def _cool(name: str, seconds: float) -> None:
    _provider_cooldown[name] = time.time() + min(max(seconds, 1.0), _RETRY_AFTER_CAP_S)


# In-process completion cache (opt-in). Identical (system,user,model,temp) prompts
# return the stored text — saving tokens + latency on retries/duplicate framing.
import hashlib

_completion_cache: dict[str, tuple[str, float]] = {}


def _cache_key(model: str, system: str, user: str, temperature: float) -> str:
    h = hashlib.sha256(f"{model}\x00{temperature}\x00{system}\x00{user}".encode()).hexdigest()
    return h


def _cache_get(key: str) -> str | None:
    hit = _completion_cache.get(key)
    if hit and time.time() < hit[1]:
        return hit[0]
    return None


def _cache_set(key: str, text: str) -> None:
    from app.config import settings as _s
    _completion_cache[key] = (text, time.time() + _s.LLM_CACHE_TTL_S)


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pricing = _COST_PER_1M.get(model, {"prompt": 0.0, "completion": 0.0})
    return (prompt_tokens * pricing["prompt"] + completion_tokens * pricing["completion"]) / 1_000_000


class _Provider:
    """One OpenAI-compatible provider in the fallback chain."""

    def __init__(self, name: str, base_url: str, api_key: str, model: str,
                 supports_json_mode: bool = True) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.supports_json_mode = supports_json_mode

    @property
    def configured(self) -> bool:
        # Ollama needs no key — a URL is enough
        return bool(self.api_key) or self.name == "ollama"

    async def chat(self, system: str, user: str, temperature: float,
                   max_tokens: int) -> tuple[str, int, int]:
        """One HTTP attempt. Returns (text, prompt_tokens, completion_tokens)."""
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.name == "openrouter":
            headers["HTTP-Referer"] = "http://localhost:3002"
            headers["X-Title"] = "AutoML Orchestrator"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if self.supports_json_mode:
            payload["response_format"] = {"type": "json_object"}

        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{self.base_url}/chat/completions", headers=headers, json=payload
            )

        if resp.status_code != 200:
            raise _ProviderHTTPError(resp.status_code, resp.text[:300], resp.headers.get("retry-after"))

        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage") or {}
        return (
            text,
            int(usage.get("prompt_tokens") or 0),
            int(usage.get("completion_tokens") or 0),
        )


class _ProviderHTTPError(Exception):
    def __init__(self, status: int, body: str, retry_after: str | None = None):
        self.status = status
        self.retry_after = retry_after
        super().__init__(f"HTTP {status}: {body}")

    @property
    def retryable(self) -> bool:
        return self.status == 429 or self.status >= 500


def _build_chain() -> list[_Provider]:
    """Build the fallback chain from settings; unconfigured providers are skipped."""
    catalog = {
        "groq": _Provider(
            "groq", "https://api.groq.com/openai/v1",
            settings.GROQ_API_KEY, settings.GROQ_MODEL,
        ),
        "gemini": _Provider(
            "gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
            settings.GEMINI_API_KEY, settings.GEMINI_MODEL,
        ),
        "openrouter": _Provider(
            "openrouter", "https://openrouter.ai/api/v1",
            settings.OPENROUTER_API_KEY, settings.OPENROUTER_MODEL,
        ),
        "deepseek": _Provider(
            "deepseek", "https://api.deepseek.com",
            settings.DEEPSEEK_API_KEY, settings.DEEPSEEK_MODEL,
        ),
        "ollama": _Provider(
            "ollama", f"{settings.OLLAMA_URL}/v1",
            "", settings.OLLAMA_MODEL,
            supports_json_mode=False,  # older ollama versions reject response_format
        ),
    }
    chain = []
    for name in [p.strip().lower() for p in settings.LLM_FALLBACK_CHAIN.split(",") if p.strip()]:
        provider = catalog.get(name)
        if provider and provider.configured:
            chain.append(provider)
    return chain


class LLMClient:
    def __init__(self) -> None:
        self.chain = _build_chain()
        if not self.chain and not settings.ANTHROPIC_API_KEY:
            raise ValueError(
                "No LLM provider configured. Set at least one of: GROQ_API_KEY, "
                "GEMINI_API_KEY, OPENROUTER_API_KEY, DEEPSEEK_API_KEY, or run Ollama."
            )
        # Backward-compat: report the head of the chain
        self.provider = self.chain[0].name if self.chain else "anthropic"
        self.model = self.chain[0].model if self.chain else settings.ANTHROPIC_MODEL

        # NOTE: which run/agent is calling is read from app.core.context (a
        # contextvar, per-task), NOT stored here — this client is a process
        # singleton shared by all concurrent runs.

    # ── Core call with retry + fallback ──────────────────────────────────────

    async def complete(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> str:
        last_error: Exception | None = None
        from app.core import metrics

        # Prompt-regression harness (LLM_REPLAY_FILE): deterministic replay of
        # recorded responses keyed by (prompt_version, user-hash). No network, no
        # provider needed — so CI runs the real agent parsing over fixed responses.
        replay = _replay_lookup(system, user)
        if replay is not None:
            return replay

        # Prompt & context logging (Layer 2): the prompt VERSION is a hash of the
        # system prompt — it changes whenever the template changes, so degradations
        # can be traced to a prompt edit. We log sizes + a short preview (profiles,
        # not raw rows) rather than the full prompt.
        prompt_version = hashlib.sha256(system.encode()).hexdigest()[:8]
        _log.info("llm_request", request_id=context.get_request_id(),
                  run_id=context.get_run_id(), agent=context.get_agent_name(),
                  prompt_version=prompt_version, system_chars=len(system),
                  user_chars=len(user), user_preview=user[:200], temperature=temperature)

        # Cache: identical prompt → stored completion (opt-in, saves tokens/latency).
        cache_model = self.chain[0].model if self.chain else self.model
        ckey = _cache_key(cache_model, system, user, temperature)
        if settings.LLM_CACHE_ENABLED:
            metrics.llm_cache_requests_total.inc()
            cached = _cache_get(ckey)
            if cached is not None:
                metrics.llm_cache_hits_total.inc()
                _log.info("llm_cache_hit", agent=context.get_agent_name(),
                          request_id=context.get_request_id(), prompt_version=prompt_version)
                return cached

        # Outer loop runs at most twice: the 2nd pass only happens if EVERY provider
        # was skipped due to cooldown and we waited for the soonest one to recover.
        for outer in range(2):
            attempted_any = False
            for provider in self.chain:
                if _cooling(provider.name):
                    _log.info("llm_provider_cooling_skip", provider=provider.name,
                              agent=context.get_agent_name())
                    continue
                attempted_any = True

                for attempt in range(1, _MAX_RETRIES_PER_PROVIDER + 1):
                    t0 = time.perf_counter()
                    try:
                        text, p_tok, c_tok = await provider.chat(system, user, temperature, max_tokens)
                        await self._record(provider.name, provider.model, p_tok, c_tok,
                                           time.perf_counter() - t0)
                        if settings.LLM_CACHE_ENABLED:
                            _cache_set(ckey, text)
                        return text

                    except _ProviderHTTPError as exc:
                        last_error = exc
                        if exc.status == 429:
                            # Cool this provider so the NEXT agent/run skips it instead of
                            # re-paying retries + Retry-After. Move on immediately.
                            cd = _DEFAULT_COOLDOWN_S
                            if exc.retry_after:
                                try:
                                    cd = float(exc.retry_after)
                                except ValueError:
                                    pass
                            _cool(provider.name, cd)
                            _log.warning("llm_rate_limited_cooldown", provider=provider.name,
                                         cooldown_s=round(min(cd, _RETRY_AFTER_CAP_S), 1),
                                         agent=context.get_agent_name())
                            break  # to next provider — no long in-request sleep
                        if not exc.retryable:
                            _log.warning("llm_provider_rejected", provider=provider.name,
                                         status=exc.status, agent=context.get_agent_name())
                            break  # bad key / bad request — next provider
                        if attempt < _MAX_RETRIES_PER_PROVIDER:
                            await asyncio.sleep(self._backoff_delay(attempt))  # 5xx backoff

                    except (httpx.TimeoutException, httpx.ConnectError, httpx.ReadError) as exc:
                        last_error = exc
                        if attempt < _MAX_RETRIES_PER_PROVIDER:
                            delay = self._backoff_delay(attempt)
                            _log.warning("llm_network_error_retrying", provider=provider.name,
                                         attempt=attempt, error=str(exc)[:120])
                            await asyncio.sleep(delay)

                _log.error("llm_provider_exhausted_falling_back", provider=provider.name,
                           agent=context.get_agent_name())

            if attempted_any:
                break  # tried real providers this pass — don't wait-and-loop
            # Every provider is cooling — wait for the soonest to recover, once.
            soonest = min((_provider_cooldown.get(p.name, 0.0) for p in self.chain), default=0.0)
            wait = soonest - time.time()
            if outer == 0 and 0 < wait <= _RETRY_AFTER_CAP_S:
                _log.warning("llm_all_providers_cooling_waiting", wait_s=round(wait, 1),
                             agent=context.get_agent_name())
                await asyncio.sleep(wait + 0.5)
                continue
            break

        # Anthropic as the final resort if configured (native SDK, not OpenAI-compatible)
        if settings.ANTHROPIC_API_KEY:
            try:
                return await self._anthropic_complete(system, user, temperature, max_tokens)
            except Exception as exc:
                last_error = exc

        tried = ", ".join(p.name for p in self.chain) or "none"
        raise RuntimeError(
            f"All LLM providers failed (tried: {tried}). "
            f"Add GEMINI_API_KEY / OPENROUTER_API_KEY / DEEPSEEK_API_KEY to .env, "
            f"or start Ollama. Last error: {str(last_error)[:200]}"
        )

    @staticmethod
    def _backoff_delay(attempt: int, retry_after: str | None = None) -> float:
        # An explicit Retry-After is authoritative — honour it (within reason)
        # so per-minute token limits recover instead of exhausting retries.
        if retry_after:
            try:
                return min(float(retry_after) + 1.0, _RETRY_AFTER_CAP_S)
            except ValueError:
                pass
        return min(_BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5), _BACKOFF_CAP_S)

    async def _anthropic_complete(self, system: str, user: str,
                                  temperature: float, max_tokens: int) -> str:
        from anthropic import AsyncAnthropic
        t0 = time.perf_counter()
        client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = await client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=temperature,
            max_tokens=max_tokens,
        )
        text = response.content[0].text
        p_tok = response.usage.input_tokens or 0
        c_tok = response.usage.output_tokens or 0
        await self._record("anthropic", settings.ANTHROPIC_MODEL, p_tok, c_tok,
                           time.perf_counter() - t0)
        return text

    # ── Instrumentation ───────────────────────────────────────────────────────

    async def _record(self, provider: str, model: str, prompt_tokens: int,
                      completion_tokens: int, latency_s: float) -> None:
        from app.core import metrics
        agent = context.get_agent_name()
        run_id = context.get_run_id()

        metrics.llm_calls_total.labels(agent_name=agent, provider=provider).inc()
        metrics.llm_tokens_total.labels(agent_name=agent, direction="prompt").inc(prompt_tokens)
        metrics.llm_tokens_total.labels(agent_name=agent, direction="completion").inc(completion_tokens)
        metrics.llm_latency_seconds.labels(agent_name=agent).observe(latency_s)
        cost = _estimate_cost(model, prompt_tokens, completion_tokens)
        if cost > 0:
            metrics.llm_cost_usd_total.labels(agent_name=agent).inc(cost)

        # One structured trace line per LLM call. request_id correlates it to the
        # originating HTTP request (Layer 5); run_id correlates the pipeline trace.
        _log.info("llm_call", request_id=context.get_request_id(), run_id=run_id,
                  agent=agent, provider=provider, model=model,
                  prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                  latency_ms=round(latency_s * 1000, 1), cost_usd=round(cost, 6))

        if run_id:
            await _persist_llm_call(
                run_id=run_id,
                agent_name=agent,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=round(latency_s * 1000, 1),
                estimated_cost_usd=cost,
            )

    # ── JSON helpers (unchanged contract) ─────────────────────────────────────

    async def complete_json(
        self,
        system: str,
        user: str,
        temperature: float = 0.1,
        max_tokens: int = 4096,
    ) -> dict[str, Any]:
        raw = await self.complete(system, user, temperature, max_tokens)
        return self._parse_json(raw)

    def _parse_json(self, raw: str) -> dict[str, Any]:
        """Robust JSON extraction. LLMs frequently append a SECOND object, trailing
        prose, or a stray newline after the JSON ("Extra data: line 2 ..."), wrap it
        in a ```json fence, or prefix it with text. `raw_decode` parses the FIRST
        complete JSON value and ignores everything after it, so none of those crash."""
        s = (raw or "").strip()
        fence = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", s)
        if fence:
            s = fence.group(1).strip()
        decoder = json.JSONDecoder()
        # Try the whole string, then from the first { / [ (skipping any leading prose).
        candidates = [s]
        starts = [i for i in (s.find("{"), s.find("[")) if i != -1]
        if starts:
            candidates.append(s[min(starts):])
        for cand in candidates:
            try:
                obj, _ = decoder.raw_decode(cand)
                return obj
            except json.JSONDecodeError:
                continue
        raise ValueError(f"LLM did not return valid JSON:\n{raw[:500]}")


async def _persist_llm_call(
    run_id: str,
    agent_name: str,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    latency_ms: float,
    estimated_cost_usd: float,
) -> None:
    try:
        from app.database import AsyncSessionLocal
        from app.models.run import LLMCall

        async with AsyncSessionLocal() as db:
            call = LLMCall(
                run_id=run_id,
                agent_name=agent_name,
                provider=provider,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                latency_ms=latency_ms,
                estimated_cost_usd=estimated_cost_usd,
            )
            db.add(call)
            await db.commit()
    except Exception:
        pass  # never crash the pipeline on observability writes


# ── Prompt-regression replay (test/CI only) ───────────────────────────────────
# When LLM_REPLAY_FILE points at a JSON map of {key: response_text}, complete()
# returns the recorded response instead of calling a provider. The key is stable
# across prompt edits to the USER content but changes with the SYSTEM prompt
# (prompt_version) — so a system-prompt change misses the fixture and is caught.
_replay_map: dict[str, str] | None = None
_replay_loaded_from: str | None = None


def replay_key(system: str, user: str) -> str:
    pv = hashlib.sha256(system.encode()).hexdigest()[:8]
    uh = hashlib.sha256(user.encode()).hexdigest()[:12]
    return f"{pv}:{uh}"


def _replay_lookup(system: str, user: str) -> str | None:
    global _replay_map, _replay_loaded_from
    path = os.environ.get("LLM_REPLAY_FILE")
    if not path:
        return None
    if _replay_loaded_from != path:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                _replay_map = json.load(fh)
        except (OSError, ValueError):
            _replay_map = {}
        _replay_loaded_from = path
    return (_replay_map or {}).get(replay_key(system, user))


_llm_client: LLMClient | None = None


def get_llm() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
