"""Stakeholder summary written by a small, cheap Nemotron model.

The planner (Ultra) does the reasoning; this call (Lightning or Nano) only turns the
verified decision into two or three plain sentences for a manager. The output is
checked like any other model output: every number in it must appear in the facts it
was given. A summary that introduces a number is rejected and the UI shows the
deterministic decision text instead.
"""
from __future__ import annotations

import json
import re
import time
import uuid
from typing import Any

import httpx

from app.autopilot.pricing import est_cost_usd
from app.autopilot.schemas import ProviderCall

PROMPT = (
    "You write incident summaries for an engineering manager. Using ONLY the JSON facts, write 2-3 short "
    "sentences: what happened, what the evidence showed, and what happens next. Do not add numbers, "
    "metrics, causes or recommendations that are not in the facts. 'change_from_fix' is how much the "
    "proposed fix changes the score on held-out data, not the size of the original incident. "
    "Approval only records a decision. Plain text, no markdown."
)
_NUM = re.compile(r"(?<![A-Za-z])[-+]?\d+(?:\.\d+)?")


def facts(state: dict[str, Any]) -> dict[str, Any]:
    d = state.get("decision") or {}
    v = state.get("verification") or {}
    sup = [h for h in state.get("hypotheses", []) if h.get("status") == "SUPPORTED"]
    out: dict[str, Any] = {
        "incident": (state.get("scenario") or {}).get("title"),
        "decision": d.get("decision"), "decision_summary": d.get("summary"), "next_action": d.get("next_action"),
        "supported_cause": sup[0]["cause"] if sup else None,
        "tests_run": [e["template_id"] for e in state.get("experiments", [])],
        "incumbent_changed": d.get("incumbent_changed", False),
    }
    if v:
        out["failed_checks"] = [c["check"] for c in v.get("checks", []) if c.get("status") != "PASS"]
        m = v.get("metrics") or {}
        delta, ft, metric = m.get("delta"), m.get("final_test"), v.get("metric")
        if delta and ft:
            # Unambiguous names: the model must not confuse the fix's gain with the original drop.
            out["holdout_evaluation"] = {
                "metric": metric,
                "current_model_score": (ft.get("incumbent") or {}).get(metric),
                "candidate_fix_score": (ft.get("candidate") or {}).get(metric),
                "change_from_fix": delta.get("delta"),
                "change_95ci": [delta.get("ci_low"), delta.get("ci_high")],
            }
    return out


def grounded(text: str, fact_blob: str) -> bool:
    """Every number in the text must occur in the facts (as written, or without a sign)."""
    have = set(_NUM.findall(fact_blob)) | {n.lstrip("+-") for n in _NUM.findall(fact_blob)}
    return all(n in have or n.lstrip("+-") in have for n in _NUM.findall(text))


class Narrator:
    def __init__(self, api_key: str, base_url: str, model: str, provider: str, timeout_s: float = 30.0,
                 transport: httpx.BaseTransport | None = None, extra_body: dict[str, Any] | None = None):
        self.api_key, self.base_url, self.model = api_key, base_url.rstrip("/"), model
        self.provider, self.timeout_s, self._transport = provider, timeout_s, transport
        self.extra_body = extra_body or {}

    def summarize(self, state: dict[str, Any]) -> tuple[dict[str, Any], ProviderCall]:
        f = facts(state)
        blob = json.dumps(f)
        corr = f"local-{uuid.uuid4().hex[:12]}"
        base = dict(provider=self.provider, operation="chat.completions", correlation_id=corr, model=self.model)
        t0 = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                r = c.post(f"{self.base_url}/chat/completions",
                           headers={"Authorization": f"Bearer {self.api_key}"},
                           json={"model": self.model, "temperature": 0, "max_tokens": 400,
                                 "messages": [{"role": "system", "content": PROMPT},
                                              {"role": "user", "content": blob}], **self.extra_body})
            ms = int((time.perf_counter() - t0) * 1000)
            if r.status_code != 200:
                return ({"status": "UNAVAILABLE", "text": None},
                        ProviderCall(**base, ok=False, status_code=r.status_code, latency_ms=ms,
                                     error=f"HTTP {r.status_code}"))
            body = r.json()
            u = body.get("usage") or {}
            model = body.get("model") or self.model
            text = re.sub(r"\s+", " ", (body["choices"][0]["message"].get("content") or "")).strip()
            call = ProviderCall(**{**base, "model": model}, request_id=body.get("id"), ok=bool(text),
                                status_code=200, latency_ms=ms, prompt_tokens=u.get("prompt_tokens"),
                                completion_tokens=u.get("completion_tokens"),
                                est_cost_usd=est_cost_usd(model, u.get("prompt_tokens"), u.get("completion_tokens")),
                                error=None if text else "empty reply")
            if not text:
                return {"status": "UNAVAILABLE", "text": None}, call
            if not grounded(text, blob):
                return {"status": "REJECTED_UNGROUNDED", "text": None,
                        "reason": "the summary contained a number that is not in the verified facts"}, call
            return {"status": "OK", "text": text[:800]}, call
        except (httpx.HTTPError, KeyError, ValueError) as e:
            ms = int((time.perf_counter() - t0) * 1000)
            return {"status": "UNAVAILABLE", "text": None}, ProviderCall(**base, ok=False, latency_ms=ms,
                                                                         error=type(e).__name__)
