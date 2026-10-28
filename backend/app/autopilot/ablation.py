"""Live ablations with the real providers (needs NEBIUS_API_KEY or NVIDIA_API_KEY, and TAVILY_API_KEY).

    python scripts/run_live_ablation.py --seeds 5

A. Planner: rules vs Nemotron, same scenarios, seeds, budget, templates, verifier and
   live Tavily. Reports accuracy, false promotions, spend, model latency, tokens,
   estimated cost, how often the rule anchor had to add a cause the model omitted,
   and how often the model was unusable and the run fell back to rules.
B. Research: Tavily on vs off for the library-change incident (T01) with the same planner.

Kept separate from `benchmark.py` so the main benchmark stays offline and reproducible.
Whatever the outcome, it is written to the report: a losing arm is reported, not dropped.
"""
from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.autopilot import engine, scenarios, service
from app.autopilot.benchmark import SELECTION_SCENARIOS

OUT = Path(__file__).resolve().parents[3] / "docs" / "benchmarks" / "autopilot_ablation_live.json"


def _run(sid: str, seed: int, planner: str, research: str, root: str) -> dict[str, Any]:
    S = SimpleNamespace(AUTOPILOT_EXPERIMENT_TIMEOUT_S=120, AUTOPILOT_RUNNER="inline",
                        AUTOPILOT_MAX_EXTRACTS_PER_RUN=3, AUTOPILOT_MAX_SEARCHES_PER_RUN=2)
    cfg = dict(scenario_id=sid, seed=seed, planner=planner, research=research, max_experiments=4,
               max_cost_units=24.0, auto_run=True)
    st = engine.new_state(engine.new_run_id(), cfg, S)
    t0 = time.perf_counter()
    engine.Investigator(st, root, S, tavily=service._tavily() if research == "auto" else None,
                        nemotron=service._nemotron() if planner != "deterministic" else None).run_to_end()
    dec = st["decision"]["decision"]
    orc = scenarios.oracle(sid)
    calls = st["planner"]["calls"]
    return {"scenario": sid, "seed": seed, "planner": planner, "research": research, "decision": dec,
            "correct": dec in orc["expected"],
            "false_promotion": dec == "CORRECTION_VERIFIED" and "CORRECTION_VERIFIED" not in orc["expected"],
            "planner_used": st["planner"]["used"], "fallback": (st["planner"]["used"] or "").endswith("(fallback)"),
            "provider_failed": dec == "FAILED" and bool(calls) and not any(c.get("ok") for c in calls),
            "anchor_added": [h["cause"] for h in st["hypotheses"] if h.get("proposed_by") == "rule_anchor"],
            "experiments": len(st["experiments"]), "cost_units": st["budget"]["cost_units_used"],
            "model_calls": len(calls), "model_seconds": round(sum(c.get("latency_ms") or 0 for c in calls) / 1000, 2),
            "tokens": sum((c.get("prompt_tokens") or 0) + (c.get("completion_tokens") or 0) for c in calls),
            "est_cost_usd": round(sum(c.get("est_cost_usd") or 0 for c in calls), 6),
            "tavily_calls": len(st["research"]["calls"]), "research_state": st["research"]["state"],
            "wall_seconds": round(time.perf_counter() - t0, 2)}


def _summary(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    if not n:
        return {"runs": 0}
    return {"runs": n, "correct": sum(r["correct"] for r in rows),
            "false_promotions": sum(r["false_promotion"] for r in rows),
            "mean_experiments": round(statistics.mean(r["experiments"] for r in rows), 2),
            "total_cost_units": round(sum(r["cost_units"] for r in rows), 2),
            "model_calls": sum(r["model_calls"] for r in rows),
            "median_model_seconds": statistics.median(r["model_seconds"] for r in rows),
            "total_tokens": sum(r["tokens"] for r in rows),
            "est_cost_usd": round(sum(r["est_cost_usd"] for r in rows), 4),
            "runs_where_anchor_added_a_cause": sum(1 for r in rows if r["anchor_added"]),
            "runs_that_fell_back_to_rules": sum(r["fallback"] for r in rows),
            "runs_lost_to_provider_errors": sum(r.get("provider_failed", False) for r in rows),
            "correct_excluding_provider_errors": f'{sum(r["correct"] for r in rows)}/'
                                                 f'{n - sum(r.get("provider_failed", False) for r in rows)}'}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--first-seed", type=int, default=100)
    a = ap.parse_args()
    if service._nemotron() is None or service._tavily() is None:
        raise SystemExit("needs a Nemotron provider key and TAVILY_API_KEY")
    seeds = list(range(a.first_seed, a.first_seed + a.seeds))
    root = tempfile.mkdtemp(prefix="autopilot-ablation-")
    provider = service._planner_provider()
    planner_rows: dict[str, list[dict]] = {"rules": [], "nemotron": []}
    # "nemotron" forces the model (no rules fallback) so its own accuracy is measured;
    # provider outages are counted separately from wrong decisions.
    for arm, planner in (("rules", "deterministic"), ("nemotron", "nemotron")):
        for sid in SELECTION_SCENARIOS:
            for seed in seeds:
                planner_rows[arm].append(_run(sid, seed, planner, "auto", root))
                print(arm, sid, seed, planner_rows[arm][-1]["decision"], flush=True)
    tavily_rows = {"tavily_on": [r for r in planner_rows["nemotron"] if r["scenario"] == "T01"],
                   "tavily_off": [_run("T01", s, "nemotron", "off", root) for s in seeds]}
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seeds": seeds,
        "provider": provider, "planner_model": service._nemotron().model,
        "planner_ablation": {k: _summary(v) for k, v in planner_rows.items()},
        "planner_per_scenario": {k: {sid: _summary([r for r in v if r["scenario"] == sid])
                                     for sid in SELECTION_SCENARIOS} for k, v in planner_rows.items()},
        "tavily_ablation_T01": {k: {**_summary(v), "decisions": sorted({r["decision"] for r in v})}
                                for k, v in tavily_rows.items()},
        "notes": ["Same seeds, budget, templates and verifier in every arm; Tavily is live in arm A.",
                  "Correct = the terminal decision is in the hidden oracle's accepted set.",
                  "Costs are estimates at Nebius Token Factory list prices (autopilot/pricing.py).",
                  "The incidents are synthetic and were written by the authors; seeds are held out."],
        "rows": planner_rows, "tavily_rows": tavily_rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("planner_ablation", "tavily_ablation_T01")}, indent=2))
    print("wrote", OUT)


if __name__ == "__main__":
    main()
