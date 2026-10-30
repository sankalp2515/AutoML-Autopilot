"""Record an offline showcase and public-data benchmark, with no provider calls.

Run from the repository root. Synthetic and public-data reports stay separate.
"""
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.autopilot import engine, service  # noqa: E402
from app.autopilot.benchmark import ARMS, run_one, summarize  # noqa: E402


def main():
    out = ROOT / "docs" / "benchmarks"
    out.mkdir(parents=True, exist_ok=True)
    cfg = dict(scenario_id="S02", seed=7, planner="deterministic", research="off",
               auto_run=True, max_experiments=4, max_cost_units=24.0)
    settings = SimpleNamespace(AUTOPILOT_RUNNER="inline", AUTOPILOT_EXPERIMENT_TIMEOUT_S=120, AUTOPILOT_MAX_EXTRACTS_PER_RUN=3)
    with tempfile.TemporaryDirectory(prefix="autopilot-evidence-") as directory:
        state = engine.new_state(engine.new_run_id(), cfg, settings)
        engine.Investigator(state, directory, settings).run_to_end()
        assert state["decision"]["decision"] == "CORRECTION_VERIFIED", state.get("error")
        (out / "autopilot_showcase.json").write_text(json.dumps(service.public(state), indent=2), encoding="utf-8")
        rows = {name: [run_one(cls, "A01", seed, directory) for seed in range(100, 105)] for name, cls in ARMS.items()}
        report = {"dataset": "UCI Adult", "source": "https://doi.org/10.24432/C5XW20", "license": "CC BY 4.0",
                  "fault": "Injected age-unit conversion (years to decades) in a random current window; not a real production outage",
                  "seeds": list(range(100, 105)), "planner": "deterministic", "arms": {k: summarize(v) for k,v in rows.items()}, "rows": rows}
        (out / "adult_benchmark.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(json.dumps(report["arms"], indent=2))


if __name__ == "__main__":
    main()
