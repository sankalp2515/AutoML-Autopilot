"""Live prompt-regression check (manual / nightly, NOT CI).

Runs each agent's CURRENT system prompt against a fixed golden input via the live
LLM, then asserts the response satisfies its contract validator. If a prompt edit
degrades quality (model starts returning an invalid task_type, an invented model
class, an out-of-range plan...), this FAILS — that is the prompt-regression signal.

Run inside the backend container with LLM keys configured:
    docker exec automl-orchestrator-backend-1 python tools/record_golden.py

It does not overwrite fixtures automatically; it reports pass/fail per agent so a
human decides whether a prompt change is an improvement or a regression.
"""
import asyncio
import sys

sys.path.insert(0, "/app")
from tests.prompt_harness import validators as V  # noqa: E402
from app.core.llm import get_llm  # noqa: E402

# Minimal, representative golden inputs (system prompt is the agent's real one).
from app.agents.problem_framer import SYSTEM_PROMPT as PF_SYS  # noqa: E402

CASES = [
    ("problem_framer", PF_SYS,
     'Goal: predict subscription. Columns: ["age","job","balance","y"]. '
     'Sample: age=58 job=management y=no. Choose task_type, target_column, primary_metric.',
     {"columns": ["age", "job", "balance", "y"]}),
]


async def main() -> int:
    llm = get_llm()
    failures = 0
    for agent, system, user, ctx in CASES:
        try:
            resp = await llm.complete_json(system, user)
        except Exception as e:
            print(f"[{agent}] LLM call FAILED: {e}")
            failures += 1
            continue
        violations = V.VALIDATORS[agent](resp, ctx)
        if violations:
            print(f"[{agent}] CONTRACT VIOLATED (possible prompt regression):")
            for v in violations:
                print(f"    - {v}")
            failures += 1
        else:
            print(f"[{agent}] OK — response satisfies its contract")
    print(f"\n{len(CASES) - failures}/{len(CASES)} agents pass their live contract.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
