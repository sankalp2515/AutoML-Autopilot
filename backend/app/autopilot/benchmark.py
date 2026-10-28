"""Equal-budget benchmark of experiment-selection policies against the hidden oracle.

    cd backend && python -m app.autopilot.benchmark --seeds 5

Arms (identical scenarios, seeds, splits, budget, templates and verifier):
  retrain_first  — what a classic AutoML loop does on an alert: retrain a fresh
                   model from the deployed config, then verify it. (Approximates the
                   existing AutoML Orchestrator's response; it is not that pipeline.)
  fixed_order    — a troubleshooting checklist: diagnostics in a fixed order.
  random         — uniformly random among policy-eligible diagnostics (seeded).
  autopilot      — information gain per cost (this product), deterministic planner.
Arms that need an unavailable provider (Nemotron planner, live Tavily) are listed
as NOT RUN with the reason — never simulated.

Seeds default to 100+ so they differ from the seed (7) used while developing.
The oracle is read only here, after each run has finished.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from app.autopilot import engine, scenarios
from app.autopilot.schemas import Status

SELECTION_SCENARIOS = ["S01", "S02", "S03", "S04", "S05", "S07", "S12", "T01"]
REVIEW_SCENARIOS = ["S08", "S09", "S10"]
FIXED_ORDER = ["CONTRACT_DIAGNOSTIC", "SHIFT_AND_LABEL_CHECK", "THRESHOLD_STUDY", "TRAIN_SERVE_PARITY",
               "SEGMENT_FAILURE_AUDIT", "DEPENDENCY_API_PROBE"]
_RUNNABLE = {"eligible", "does not discriminate between live hypotheses"}


class FixedOrder(engine.Investigator):
    def _choose(self, ranking):
        by = {c.template_id: c for c in ranking}
        for tid in FIXED_ORDER:
            c = by.get(tid)
            if c and c.reason in _RUNNABLE:
                return c
        return None


class RandomEligible(engine.Investigator):
    def _choose(self, ranking):
        pool = [c for c in ranking if c.kind == "diagnostic" and c.reason in _RUNNABLE]
        rng = random.Random(f"{self.s['config']['seed']}-{len(self.s['experiments'])}")
        return rng.choice(pool) if pool else None


class RetrainFirst(engine.Investigator):
    def _investigate(self):
        self.s["pending"] = {"template_id": "MODEL_FAMILY_CHALLENGER",
                             "parameters": {"model": "hist_gradient_boosting"},
                             "hypothesis_ids": ["REVIEW"], "source_refs": [], "rationale": "retrain on alert"}
        self.s["correction"] = {"template_id": "MODEL_FAMILY_CHALLENGER", "for_hypothesis": None,
                                "cause": None, "mode": "improve"}
        self.move(Status.TESTING_CORRECTION, "retrain-first baseline")


ARMS = {"retrain_first": RetrainFirst, "fixed_order": FixedOrder, "random": RandomEligible,
        "autopilot": engine.Investigator}


def run_one(cls, sid: str, seed: int, root: str) -> dict[str, Any]:
    S = SimpleNamespace(AUTOPILOT_EXPERIMENT_TIMEOUT_S=120, AUTOPILOT_RUNNER="inline",
                        AUTOPILOT_MAX_EXTRACTS_PER_RUN=3)
    cfg = dict(scenario_id=sid, seed=seed, planner="deterministic", research="auto", max_experiments=4,
               max_cost_units=24.0, auto_run=True)
    st = engine.new_state(engine.new_run_id(), cfg, S)
    t0 = time.perf_counter()
    cls(st, root, S).run_to_end()
    wall = time.perf_counter() - t0
    dec = st["decision"]["decision"]
    orc = scenarios.oracle(sid)
    return {"scenario": sid, "seed": seed, "decision": dec, "correct": dec in orc["expected"],
            "false_promotion": dec == "CORRECTION_VERIFIED" and "CORRECTION_VERIFIED" not in orc["expected"],
            "experiments": len(st["experiments"]),
            "diagnostics": sum(1 for e in st["experiments"] if e["kind"] == "diagnostic"),
            "retrains": sum(1 for e in st["experiments"] if e["template_id"] in
                            ("MODEL_FAMILY_CHALLENGER", "CLASS_WEIGHT_CHALLENGER")),
            "cost_units": st["budget"]["cost_units_used"], "cpu_seconds": round(st["budget"]["cpu_seconds"], 3),
            "wall_seconds": round(wall, 3), "holdout_uses": len(st["final_test_accesses"]),
            "templates": [e["template_id"] for e in st["experiments"]]}


def summarize(rows: list[dict]) -> dict[str, Any]:
    n = len(rows)
    med = lambda k: statistics.median(r[k] for r in rows) if rows else None  # noqa: E731
    return {"runs": n, "correct": sum(r["correct"] for r in rows),
            "accuracy": round(sum(r["correct"] for r in rows) / n, 3) if n else None,
            "false_promotions": sum(r["false_promotion"] for r in rows),
            "median_experiments": med("experiments"), "mean_experiments": round(statistics.mean(
                r["experiments"] for r in rows), 3) if rows else None,
            "median_cost_units": med("cost_units"), "total_cost_units": round(sum(r["cost_units"] for r in rows), 2),
            "median_cpu_seconds": med("cpu_seconds"), "total_retrains": sum(r["retrains"] for r in rows),
            "holdout_uses": sum(r["holdout_uses"] for r in rows)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--first-seed", type=int, default=100)
    ap.add_argument("--out", default=str(Path(__file__).resolve().parents[3] / "docs" / "benchmarks"
                                         / "autopilot_benchmark.json"))
    a = ap.parse_args()
    seeds = list(range(a.first_seed, a.first_seed + a.seeds))
    root = tempfile.mkdtemp(prefix="autopilot-bench-")
    rows: dict[str, list[dict]] = {k: [] for k in ARMS}
    for arm, cls in ARMS.items():
        for sid in SELECTION_SCENARIOS:
            for seed in seeds:
                rows[arm].append(run_one(cls, sid, seed, root))
        print(arm, summarize(rows[arm]), flush=True)
    review = [run_one(engine.Investigator, sid, seed, root) for sid in REVIEW_SCENARIOS for seed in seeds]
    per_scenario = {arm: {sid: summarize([r for r in rs if r["scenario"] == sid]) for sid in SELECTION_SCENARIOS}
                    for arm, rs in rows.items()}
    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "seeds": seeds,
        "budget": {"max_experiments": 4, "max_cost_units": 24.0},
        "scenarios": SELECTION_SCENARIOS, "arms": {arm: summarize(rs) for arm, rs in rows.items()},
        "per_scenario": per_scenario,
        "verifier_must_fail": {"scenarios": REVIEW_SCENARIOS, **summarize(review),
                               "decisions": sorted({r["decision"] for r in review})},
        "not_run": {"nemotron_planner": "live model arm not benchmarked: 40+ paid calls at 10-30 s each; "
                                        "live runs of S02-S12 and T01 are recorded in docs/AUTOPILOT.md",
                    "tavily_on_vs_off_live": "benchmark stays offline and reproducible; the live T01 path is "
                                             "verified manually and by contract tests T01-T10"},
        "notes": ["Cost units are the templates' planning estimates; cpu_seconds are measured.",
                  "Correct = terminal decision is in the oracle's accepted set for that scenario.",
                  "T01 accepts DIAGNOSIS_SUPPORTED without live research (policy forbids guessing renames)."],
        "rows": rows, "review_rows": review,
    }
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("wrote", a.out)


if __name__ == "__main__":
    main()
