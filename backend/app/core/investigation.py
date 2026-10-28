"""Deterministic scaffolding for the Hypothesis Loop (Phase 2).

The LLM does the reasoning (which hypotheses, which columns/params); everything
here — the test catalog the LLM must choose from, validation of its output, the
budget Conductor, the Critic's verdict mapping, and report aggregation — is pure
and unit-tested, so the agentic loop is grounded and predictable.
"""
from __future__ import annotations

from typing import Any

# Catalog the Strategist must pick from. Each entry: required params + the
# meaning of the primitive's `observed` value (so the LLM can set `expected`).
PRIMITIVES: dict[str, dict[str, Any]] = {
    "feature_ablation": {
        "params": ["column"],
        "observed": "CV score DROP when the feature is removed (higher = more essential).",
    },
    "single_feature_power": {
        "params": ["column"],
        "observed": "CV score using ONLY this feature (near full-model score = suspicious/leak).",
    },
    "slice_performance": {
        "params": ["column"],  # optional: op, value
        "observed": "slice score MINUS overall (negative = the model is worse on this cohort).",
    },
    "interaction_test": {
        "params": ["col_a", "col_b"],
        "observed": "CV score IMPROVEMENT from adding the a*b interaction.",
    },
    "shift_stress": {
        "params": [],  # optional: magnitude
        "observed": "relative score DROP under mild input noise (higher = more fragile).",
    },
    "target_correlation": {
        "params": ["column"],
        "observed": "absolute correlation of the feature with the target (≈1 = likely leak).",
    },
}

# Phase 2.5 — codegen escalation: for hypotheses NO bounded primitive covers,
# the Strategist may request "custom_code": the Experimenter writes a sandboxed
# test (with one repair retry) that must set RESULT = {"observed": <float>, ...}.
# Capped hard per round — primitives remain the default path.
CUSTOM_PRIMITIVE = "custom_code"
MAX_CUSTOM_PER_ROUND = 2

_DIRECTIONS = {">", "<", ">=", "<="}
_SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}

# Lean budget (the chosen default).
DEFAULT_BUDGET = {"max_hypotheses": 5, "max_followups": 1, "max_llm_calls": 8}


def catalog_text() -> str:
    lines = []
    for name, meta in PRIMITIVES.items():
        params = ", ".join(meta["params"]) or "(none)"
        lines.append(f'- "{name}" — params: {params}. observed = {meta["observed"]}')
    lines.append(
        f'- "{CUSTOM_PRIMITIVE}" — ESCALATION, only when NO primitive above can test the '
        'hypothesis. params: description (precise instructions for a small Python test; '
        'it must compute ONE number). observed = that number. Use sparingly (max '
        f'{MAX_CUSTOM_PER_ROUND} per round).'
    )
    return "\n".join(lines)


def parse_hypotheses(obj: Any) -> list[dict[str, Any]]:
    """Validate the Strategist's JSON into a clean hypothesis list. Anything
    malformed is dropped (never raises) so one bad item can't sink the run."""
    items = obj.get("hypotheses") if isinstance(obj, dict) else obj
    if not isinstance(items, list):
        return []
    out: list[dict[str, Any]] = []
    for i, h in enumerate(items):
        if not isinstance(h, dict):
            continue
        prim = h.get("primitive")
        params = h.get("params") or {}
        if prim == CUSTOM_PRIMITIVE:
            # Escalation path: needs a non-trivial natural-language test description.
            desc = params.get("description") if isinstance(params, dict) else None
            if not isinstance(desc, str) or len(desc.strip()) < 20:
                continue
        else:
            meta = PRIMITIVES.get(prim)
            if meta is None:
                continue
            if not isinstance(params, dict) or any(p not in params for p in meta["params"]):
                continue
        exp = h.get("expected") or {}
        if not isinstance(exp, dict) or exp.get("direction") not in _DIRECTIONS:
            continue
        try:
            threshold = float(exp.get("threshold"))
        except (TypeError, ValueError):
            continue
        out.append({
            "id": str(h.get("id") or f"h{i+1}"),
            "statement": str(h.get("statement", ""))[:300],
            "rationale": str(h.get("rationale", ""))[:300],
            "severity": h.get("severity") if h.get("severity") in _SEVERITY_RANK else "medium",
            "primitive": prim,
            "params": params,
            "expected": {"direction": exp["direction"], "threshold": threshold},
        })
    return out


def select(hypotheses: list[dict], budget: dict | None = None) -> list[dict]:
    """Conductor: rank by severity, cap to the budget."""
    b = budget or DEFAULT_BUDGET
    ranked = sorted(hypotheses, key=lambda h: -_SEVERITY_RANK.get(h["severity"], 2))
    # de-dupe identical (primitive, params); cap the codegen escalations hard
    seen, uniq, n_custom = set(), [], 0
    for h in ranked:
        key = (h["primitive"], tuple(sorted(h["params"].items())))
        if key in seen:
            continue
        if h["primitive"] == CUSTOM_PRIMITIVE:
            if n_custom >= MAX_CUSTOM_PER_ROUND:
                continue
            n_custom += 1
        seen.add(key)
        uniq.append(h)
    return uniq[: b["max_hypotheses"]]


def verdict(hypothesis: dict, observed: Any, error: str | None = None) -> dict[str, Any]:
    """Critic: compare the observed signal to the hypothesis's expectation."""
    if error or observed is None:
        return {"verdict": "inconclusive", "reason": error or "no result"}
    d = hypothesis["expected"]["direction"]
    t = hypothesis["expected"]["threshold"]
    o = float(observed)
    ok = {">": o > t, "<": o < t, ">=": o >= t, "<=": o <= t}[d]
    return {"verdict": "confirmed" if ok else "refuted",
            "observed": round(o, 4), "expected": f"{d} {t}"}


def aggregate(run_id: str, results: list[dict], budget_used: dict, truncated: bool = False) -> dict[str, Any]:
    n_conf = sum(1 for r in results if r["verdict"] == "confirmed")
    n_ref = sum(1 for r in results if r["verdict"] == "refuted")
    n_inc = sum(1 for r in results if r["verdict"] == "inconclusive")
    confirmed = [r for r in results if r["verdict"] == "confirmed"]
    if not results:
        summary = "No testable hypotheses were produced for this run."
    elif confirmed:
        head = "; ".join(r["statement"] for r in confirmed[:3] if r.get("statement"))
        summary = f"{n_conf} of {len(results)} hypotheses confirmed. Key findings: {head}"
    else:
        summary = f"No hypotheses confirmed ({n_ref} refuted, {n_inc} inconclusive) — the model held up to investigation."
    return {
        "schema_version": 1,
        "run_id": run_id,
        "n_hypotheses": len(results),
        "n_confirmed": n_conf,
        "n_refuted": n_ref,
        "n_inconclusive": n_inc,
        "truncated": truncated,
        "budget_used": budget_used,
        "summary": summary,
        "hypotheses": results,
    }
