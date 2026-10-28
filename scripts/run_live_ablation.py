"""Run the live planner and Tavily ablations with the keys in the repo-root .env.

    python scripts/run_live_ablation.py --seeds 5     # ~20 min, about $0.20 of model calls

Writes docs/benchmarks/autopilot_ablation_live.json. See backend/app/autopilot/ablation.py.
"""
import runpy
import sys
from pathlib import Path

DEV = Path(__file__).resolve().parent / "dev_autopilot_backend.py"
# Reuse the dev server's environment setup (backend on sys.path, root .env loaded).
exec(DEV.read_text(encoding="utf-8").split("if __name__")[0], {"__file__": str(DEV)})

if __name__ == "__main__":
    sys.argv = [sys.argv[0], *sys.argv[1:]]
    runpy.run_module("app.autopilot.ablation", run_name="__main__")
