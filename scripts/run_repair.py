"""Run the v5 repair agent on a fixture with live Nemotron calls and save the report.

    python scripts/run_repair.py fixtures/repos/churn-service --mode full --out runs/churn.json

Reads provider keys from the repository's .env (never printed). Goes through the same intake
and freeze as the API, and uses the Docker runner unless REPAIR_RUNNER=process.
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def load_env() -> None:
    env = ROOT / ".env"
    if env.is_file():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture")
    ap.add_argument("--mode", choices=["full", "generic"], default="full")
    ap.add_argument("--out", required=True)
    ap.add_argument("--task-id", default=None)
    a = ap.parse_args()
    load_env()

    from app.repair import contracts as c, snapshots
    from app.repair.agent.llm import client_from_settings
    from app.repair.agent.loop import RepairAgent
    from app.repair.agent.oracles import LocalOracles
    from app.repair.oracle.grading import GradeRef
    from app.repair.sandbox.factory import local_runner

    # Same path as the API: stage the bundled sample, freeze it with the platform intake
    # (real pinned commit SHA + snapshot digest), then grade with the fixture's sealed bundle.
    fix = (ROOT / a.fixture).resolve()
    tenant = f"script-{os.getpid()}"
    staged = snapshots.stage_sample(tenant, fix.name)
    frozen = snapshots.freeze(staged["id"], tenant)
    contract = frozen["contract"]
    task_id = a.task_id or f"{contract.name}-{a.mode}-{os.getpid()}"
    ecc = c.ECC.build(task_id=task_id, base_commit_sha=frozen["sha"], repo_snapshot_digest=frozen["digest"],
                      contract=contract, target=(fix / "TASK.txt").read_text(encoding="utf-8").strip()
                      if (fix / "TASK.txt").is_file() else "Make the failing tests pass without breaking the model.")
    llm = client_from_settings(ecc.limits.model_tokens)
    if llm is None:
        print("no provider key configured", file=sys.stderr)
        return 2
    runner = local_runner()          # Docker by default; REPAIR_RUNNER=process only for trusted fixtures
    ref = GradeRef(task_id, ecc.model_dump(mode="json"), frozen["snapshot_dir"], f"fixture:{fix.name}")
    agent = RepairAgent(runner, llm, LocalOracles(runner, ref), base_dir=frozen["snapshot_dir"],
                        contract=contract, ecc=ecc, mode=a.mode,
                        on_event=lambda e: print(f"[{e['seq']:>3}] {e['type']:<22} {e.get('candidate_id', '')} "
                                                 f"{e.get('cause', e.get('path', e.get('rule', '')))}"[:160], flush=True))
    rep = agent.run()
    out = ROOT / a.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(rep.model_dump_json(indent=2), encoding="utf-8")
    print(json.dumps({"decision": rep.decision.value, "reasons": rep.reasons, "finalist": rep.finalist,
                      "candidates": [{"id": r.candidate.candidate_id, "hyp": r.candidate.hypothesis_id,
                                      "files": r.candidate.files_changed, "steps": len(r.steps),
                                      "stop": r.stop_reason, "eligible": r.eligible,
                                      "code": r.code_receipt.decision.value if r.code_receipt else None,
                                      "screen": r.screen_receipt.decision.value if r.screen_receipt else None}
                                     for r in rep.candidates],
                      "usage": rep.usage, "seconds": rep.seconds}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
