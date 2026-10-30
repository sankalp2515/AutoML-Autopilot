"""Estimated cost per model call, from Nebius Token Factory public list prices.

Source: nebius.com/services/token-factory/models/nvidia-nemotron-models-inference
(read 2026-10-28). USD per 1M tokens (input, output). Estimates only: the provider's
bill is authoritative. Calls through the NVIDIA API catalog are priced at the same
Nebius rates as a comparative equivalent so runs are comparable. That figure is
not an NVIDIA invoice; developer API access may be free and subject to quotas.
"""
from __future__ import annotations

PRICES_PER_M: dict[str, tuple[float, float]] = {
    "ultra": (1.00, 3.00),
    "super": (0.30, 0.90),
    "lightning": (0.06, 0.24),
    "nano": (0.06, 0.24),
}
SOURCE = "Nebius Token Factory list price, Oct 2026"


def est_cost_usd(model: str | None, prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    if not model or prompt_tokens is None:
        return None
    m = model.lower()
    for key, (pin, pout) in PRICES_PER_M.items():
        if key in m:
            return round((prompt_tokens * pin + (completion_tokens or 0) * pout) / 1_000_000, 6)
    return None
