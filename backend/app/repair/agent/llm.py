"""Tiered Nemotron client with a token ledger.

One OpenAI-compatible endpoint (Nebius Token Factory, or the NVIDIA API catalog while the
Nebius key is on hold), three tiers, and hard per-task token ceilings taken from the ECC.
Every call is recorded with tokens, latency and estimated cost, so cost tuning works from
measurements. Thinking is off; a reply cut off by the length limit gets one compact retry.
"""
from __future__ import annotations

import json
import time
import threading
from email.utils import parsedate_to_datetime
import uuid
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from app.autopilot.pricing import est_cost_usd
from app.config import settings
from app.repair.contracts import TierTokens

Tier = Literal["ultra", "super", "nano"]


class BudgetExceeded(RuntimeError):
    pass


class CallRecord(BaseModel):
    correlation_id: str
    tier: str
    purpose: str
    provider: str
    model: str
    ok: bool
    status_code: int | None = None
    finish_reason: str | None = None
    latency_ms: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    est_cost_usd: float | None = None
    request_id: str | None = None
    error: str | None = None


class _Credentials:
    """Shared candidate credential cursor. Never serialized into call/report records."""
    def __init__(self, keys):
        self.keys=list(dict.fromkeys(k for k in keys if k))
        self.index=0
        self.lock=threading.Lock()
        self.cooldown={}
    def current(self):
        with self.lock:
            for offset in range(len(self.keys)):
                index=(self.index+offset)%len(self.keys)
                if self.cooldown.get(index,0)<=time.monotonic():
                    self.index=index
                    return self.keys[index],index
        raise RuntimeError("All configured NVIDIA credentials are cooling down; retry later")
    def defer(self, index, seconds):
        with self.lock:
            self.cooldown[index]=time.monotonic()+max(1,seconds)
            self.index=(index+1)%len(self.keys)


class ChatClient:
    TRANSIENT = (429, 500, 502, 503, 504)

    def __init__(self, *, provider: str, base_url: str, api_key: str, models: dict[str, str],
                 budget: TierTokens, timeout_s: float = 90.0, extra_body: dict[str, Any] | None = None,
                 transport: httpx.BaseTransport | None = None, retry_pause_s: float = 2.0, api_keys: list[str] | None = None):
        self.provider, self.base_url, self._key = provider, base_url.rstrip("/"), api_key
        self.models, self.budget, self.timeout_s = models, budget, timeout_s
        self.extra_body = extra_body or {}
        self._transport = transport
        self.retry_pause_s = retry_pause_s
        self._credentials = _Credentials([api_key, *(api_keys or [])])
        self.calls: list[CallRecord] = []
        self._purpose_scope: str | None = None

    def scoped(self, purpose: str, token_cap: int) -> ChatClient:
        """An independent candidate allowance with a shared, auditable call ledger.

        The planner allocates disjoint slices before starting candidate threads.
        A sibling's prompt/token use never consumes this client's allowance.
        """
        child = ChatClient(provider=self.provider, base_url=self.base_url, api_key=self._key,
                           models=dict(self.models), budget=self.budget.model_copy(update={"super": token_cap}),
                           timeout_s=self.timeout_s, extra_body=dict(self.extra_body),
                           transport=self._transport, retry_pause_s=self.retry_pause_s)
        child.calls, child._purpose_scope = self.calls, purpose
        child._credentials = self._credentials
        return child

    # ── ledger ────────────────────────────────────────────────────────────────
    def used(self, tier: str) -> int:
        return sum(c.prompt_tokens + c.completion_tokens for c in self.calls if c.tier == tier
                   and (self._purpose_scope is None or c.purpose == self._purpose_scope))

    def remaining(self, tier: str) -> int:
        return getattr(self.budget, tier) - self.used(tier)

    def usage(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for t in ("ultra", "super", "nano"):
            cs = [c for c in self.calls if c.tier == t]
            if cs:
                out[t] = {"calls": len(cs), "model": cs[-1].model, "prompt_tokens": sum(c.prompt_tokens for c in cs),
                          "completion_tokens": sum(c.completion_tokens for c in cs),
                          "est_cost_usd": round(sum(c.est_cost_usd or 0 for c in cs), 6),
                          "budget": getattr(self.budget, t)}
        out["total_est_cost_usd"] = round(sum(c.est_cost_usd or 0 for c in self.calls), 6)
        return out

    # ── calls ─────────────────────────────────────────────────────────────────
    def complete(self, tier: Tier, messages: list[dict[str, str]], *, max_tokens: int, purpose: str,
                 json_mode: bool = True) -> str:
        """Return the reply text, or raise BudgetExceeded / RuntimeError (after one retry for
        transient failures). Nothing is retried for a reply the model chose to give."""
        if self._purpose_scope is not None and purpose != self._purpose_scope:
            raise ValueError("candidate call purpose differs from its budget scope")
        if self.remaining(tier) <= 0:
            raise BudgetExceeded(f"{tier} token budget exhausted ({getattr(self.budget, tier)})")
        max_tokens = max(64, min(max_tokens, self.remaining(tier)))
        max_attempts=max(2,len(self._credentials.keys)+1)
        for attempt in range(1,max_attempts+1):
            text, rec = self._post(tier, messages, max_tokens, purpose, json_mode)
            self.calls.append(rec)
            if rec.ok and rec.finish_reason == "length" and attempt == 1 and self.remaining(tier) > 256:
                messages = messages + [{"role": "assistant", "content": (text or "")[:2000]},
                                       {"role": "user", "content": "Your reply was cut off. Reply again with a "
                                        "shorter, complete JSON object only."}]
                continue
            if rec.ok:
                return text or ""
            if self.provider == "nvidia_api" and rec.status_code in (402,429) and attempt < max_attempts and len(self._credentials.keys)>1:
                time.sleep(self.retry_pause_s)
                continue
            if attempt == 1 and (rec.status_code in self.TRANSIENT or (rec.error or "").endswith("Timeout")):
                time.sleep(self.retry_pause_s)
                continue
            raise RuntimeError(f"{self.provider} {rec.model}: {rec.error}")
        return text or ""

    def _post(self, tier, messages, max_tokens, purpose, json_mode) -> tuple[str | None, CallRecord]:
        model = self.models[tier]
        rec = dict(correlation_id=f"local-{uuid.uuid4().hex[:12]}", tier=tier, purpose=purpose,
                   provider=self.provider, model=model)
        body = {"model": model, "temperature": 0, "max_tokens": max_tokens, "messages": messages, **self.extra_body}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        try:
            key, credential_index = self._credentials.current()
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                r = c.post(f"{self.base_url}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                           json=body)
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                if self.provider == "nvidia_api" and r.status_code in (402,429) and len(self._credentials.keys)>1:
                    try: delay=max(1,float(r.headers.get("Retry-After","60")))
                    except ValueError:
                        try: delay=max(1,parsedate_to_datetime(r.headers["Retry-After"]).timestamp()-time.time())
                        except (ValueError,TypeError,KeyError): delay=60
                    self._credentials.defer(credential_index,delay)
                return None, CallRecord(**rec, ok=False, status_code=r.status_code, latency_ms=ms,
                                        error=f"HTTP {r.status_code}")
            data = r.json()
            u = data.get("usage") or {}
            pt, ct = int(u.get("prompt_tokens") or 0), int(u.get("completion_tokens") or 0)
            ch = data["choices"][0]
            return ch["message"].get("content") or "", CallRecord(
                **rec, ok=True, status_code=200, finish_reason=ch.get("finish_reason"), latency_ms=ms,
                prompt_tokens=pt, completion_tokens=ct, request_id=data.get("id"),
                est_cost_usd=est_cost_usd(data.get("model") or model, pt, ct))
        except (httpx.HTTPError, KeyError, ValueError, IndexError) as e:
            ms = int((time.perf_counter() - t0) * 1000)
            return None, CallRecord(**rec, ok=False, latency_ms=ms, error=type(e).__name__)


def parse_json(text: str) -> dict[str, Any]:
    """The first {...} object in a reply. Raises ValueError if there is none."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in the reply")
    return json.loads(text[start:end + 1])


def client_from_settings(budget: TierTokens, transport: httpx.BaseTransport | None = None) -> ChatClient | None:
    """Nebius Token Factory when its key is set, else the NVIDIA API catalog, else None."""
    if settings.NEBIUS_API_KEY:
        return ChatClient(provider="nebius_token_factory", base_url=settings.NEBIUS_BASE_URL,
                          api_key=settings.NEBIUS_API_KEY, budget=budget, timeout_s=settings.NEBIUS_TIMEOUT_S,
                          models={"ultra": settings.NEBIUS_PLANNER_MODEL, "super": settings.NEBIUS_CODER_MODEL,
                                  "nano": settings.NEBIUS_FAST_MODEL}, transport=transport)
    if settings.NVIDIA_API_KEY:
        extra = {"chat_template_kwargs": {"enable_thinking": bool(settings.NVIDIA_ENABLE_THINKING)}}
        return ChatClient(provider="nvidia_api", base_url=settings.NVIDIA_BASE_URL, api_key=settings.NVIDIA_API_KEY,
                          budget=budget, timeout_s=settings.NVIDIA_TIMEOUT_S, extra_body=extra,
                          api_keys=[settings.NVIDIA_API_KEY_2,settings.NVIDIA_API_KEY_3,settings.NVIDIA_API_KEY_4],
                          models={"ultra": settings.NVIDIA_PLANNER_MODEL, "super": settings.NVIDIA_CODER_MODEL,
                                  "nano": settings.NVIDIA_FAST_MODEL}, transport=transport)
    return None
