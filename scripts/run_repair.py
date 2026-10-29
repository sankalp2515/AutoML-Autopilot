"""Run the v5 repair agent on a fixture with live Nemotron calls and save the report.

    python scripts/run_repair.py fixtures/repos/churn-service --mode full --out runs/churn.json

Reads provider keys from the repository's .env (never printed). Uses the unisolated
process runner, so run it only on the bundled fixtures.
"""
import argparse
import json
import os
import sys
import tempfile
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

    from app.repair import contracts as c
    from app.repair.agent.llm import client_from_settings
    from app.repair.agent.loop import RepairAgent
    from app.repair.oracle.ml import FileHoldoutLedger
    from app.repair.sandbox.process import ProcessRunner

    fix = (ROOT / a.fixture).resolve()
    repo, sealed = fix / "repo", fix / "sealed"
    contract = c.RepoContract.from_yaml((repo / "autopilot.yaml").read_text(encoding="utf-8"))
    manifest = c.SealedManifest.from_yaml((sealed / "manifest.yaml").read_text(encoding="utf-8"))
    task_id = a.task_id or f"{contract.name}-{a.mode}-{os.getpid()}"
    ecc = c.ECC.build(task_id=task_id, base_commit_sha="0" * 40, repo_snapshot_digest="sha256:fixture",
                      contract=contract, target=(fix / "TASK.txt").read_text(encoding="utf-8").strip()
                      if (fix / "TASK.txt").is_file() else "Make the failing tests pass without breaking the model.")
    llm = client_from_settings(ecc.limits.model_tokens)
    if llm is None:
        print("no provider key configured", file=sys.stderr)
        return 2
    tmp = Path(tempfile.mkdtemp(prefix="ap-repair-"))
    agent = RepairAgent(ProcessRunner(str(tmp / "envs"), allow_unisolated=True), llm, base_dir=str(repo),
                        contract=contract, ecc=ecc, sealed_dir=str(sealed), manifest=manifest,
                        ledger=FileHoldoutLedger(str(tmp / "ledger")), mode=a.mode,
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
