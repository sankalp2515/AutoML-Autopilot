"""Admission-time ceilings. Requests can reduce server limits, never raise them."""
from pydantic import BaseModel, ConfigDict, Field
from app.repair.contracts import Limits, TierTokens
from app.autopilot.pricing import PRICES_PER_M, SOURCE

class TaskBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_candidates: int = Field(default=2, ge=1, le=2)
    coder_tokens: int = Field(default=160000, ge=1024, le=160000)

def limits_for(request: TaskBudget | None = None) -> Limits:
    b = request or TaskBudget()
    return Limits(max_candidates=b.max_candidates, model_tokens=TierTokens(super=b.coder_tokens))

def estimate(request: TaskBudget | None = None, models: dict | None = None) -> dict:
    limits = limits_for(request)
    tokens = limits.model_tokens.model_dump()
    # Total tokens can be prompt or completion. Use the more expensive rate;
    # this estimates nominal limits, not a provider-enforced invoice maximum.
    def rate(tier):
        name=str((models or {}).get(tier,"")).lower()
        found=next((rates for key,rates in PRICES_PER_M.items() if key in name),None)
        return max(found) if found else max(max(rates) for rates in PRICES_PER_M.values())
    amount = sum(tokens[t] * rate(t) / 1_000_000 for t in tokens)
    return {"limits": limits.model_dump(), "estimated_inference_usd": round(amount,6),
            "pricing_source": SOURCE, "model_mapping":models or {}, "kind":"nominal-token-ceiling estimate",
            "limitations":"Excludes compute, storage and research. Prompt usage arrives after each call and may overshoot by one request per candidate. Provider billing is authoritative.",
            "policy": {"max_candidates":2,"coder_tokens_min":1024,"coder_tokens_max":160000}}
