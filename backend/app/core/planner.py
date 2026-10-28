"""P1.7 v1 — the Strategist plans the pipeline (justified skips + iteration budget).

Doctrine (docs/SPEC_platform.md / SPEC_dynamic_pipeline.md): the planner chooses
from a CLOSED vocabulary of decisions on the EXISTING static graph. It can only:
  - skip feature_engineer (e.g. 500+ features already, or tiny data)
  - skip tuner (tiny data — tuning is noise)
  - set max_iterations for THIS dataset (0..hard cap)

Invariants enforced here (never trusted to the LLM):
  - eda / preprocessor / model_selector / evaluator / trust_gate / exporter NEVER skip
  - an invalid/absent plan → the full fixed pipeline (zero regression risk)
"""
from __future__ import annotations

from typing import Any

# Hard bounds — the plan is clamped to these regardless of what the LLM returns.
_MAX_ITER_CEILING = 3


def default_plan(max_iterations: int) -> dict[str, Any]:
    return {
        "skip_feature_engineer": False,
        "skip_tuner": False,
        "max_iterations": int(max_iterations),
        "rationale": "Default full pipeline.",
        "source": "default",
    }


def validate_plan(obj: Any, default_max_iterations: int) -> dict[str, Any]:
    """Coerce the LLM's plan JSON into a safe plan. Anything malformed falls back
    to the corresponding default — the pipeline can never be broken by a bad plan."""
    base = default_plan(default_max_iterations)
    if not isinstance(obj, dict):
        return base
    plan = obj.get("plan") if isinstance(obj.get("plan"), dict) else obj

    def _bool(key: str) -> bool:
        v = plan.get(key)
        return v if isinstance(v, bool) else False

    out = dict(base)
    out["skip_feature_engineer"] = _bool("skip_feature_engineer")
    out["skip_tuner"] = _bool("skip_tuner")
    try:
        mi = int(plan.get("max_iterations", default_max_iterations))
        out["max_iterations"] = max(0, min(_MAX_ITER_CEILING, mi))
    except (TypeError, ValueError):
        out["max_iterations"] = int(default_max_iterations)
    r = plan.get("rationale")
    out["rationale"] = str(r)[:400] if isinstance(r, str) and r.strip() else "Plan applied."
    out["source"] = "strategist"
    return out


def plan_summary(plan: dict[str, Any]) -> str:
    skips = [name for name, on in (("feature engineering", plan.get("skip_feature_engineer")),
                                   ("tuning", plan.get("skip_tuner"))) if on]
    parts = [f"max {plan.get('max_iterations')} improvement iteration(s)"]
    if skips:
        parts.append("skipping " + " + ".join(skips))
    else:
        parts.append("all steps")
    return "; ".join(parts)
