"""Six bounded planner-only probes using actual isolated failure reproductions.

No patch, label access, repair success rate or holdout result is claimed. Inference
requires explicit opt-in; keys are supplied only through process settings.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"backend"))
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource.__call__=lambda self:{}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allow-model-calls",action="store_true");parser.add_argument("--out",required=True)
    parser.add_argument("--max-runs",type=int,default=6)
    args=parser.parse_args()
    if not args.allow_model_calls or not 1<=args.max_runs<=6:parser.error("explicit opt-in and 1..6 run cap required")
    from app.config import settings
    from app.repair import contracts as c,snapshots
    from app.repair.agent import prompts
    from app.repair.agent.loop import RepairAgent
    from app.repair.agent.llm import client_from_settings
    from app.repair.oracle.code import measure_baseline
    from app.repair.sandbox.factory import make_runner
    settings.REPAIR_FIXTURES_DIR=str(ROOT/"fixtures/repos")
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    runner=make_runner()
    if not runner.isolated:parser.error("isolated runner required")
    samples=snapshots.samples()[:args.max_runs]
    for sample in samples:
        dest=out/(sample["name"]+".json")
        if dest.exists():continue
        staged=snapshots.stage_sample("hypothesis-evaluation",sample["name"])
        frozen=snapshots.freeze(staged["id"],"hypothesis-evaluation");rc=frozen["contract"]
        ecc=c.ECC.build(task_id=str(uuid.uuid4()),base_commit_sha=frozen["sha"],repo_snapshot_digest=frozen["digest"],contract=rc,target=sample["task"])
        client=client_from_settings(c.TierTokens(ultra=12000,super=1024,nano=1000))
        if client is None:parser.error("configure an authorized provider")
        client.extra_body={**client.extra_body,"seed":47}
        class Baseline:
            def baseline(self):return measure_baseline(runner,frozen["snapshot_dir"],rc)
        agent=RepairAgent(runner,client,Baseline(),base_dir=frozen["snapshot_dir"],contract=rc,ecc=ecc,research_enabled=False)
        env=runner.prepare(frozen["snapshot_dir"],python=rc.python,install=rc.install,timeout_s=900)
        try:
            _,excerpt,listing=agent._reproduce(env)
            brief=agent._brief(excerpt,listing)
            hypotheses=agent._hypotheses(brief)
            agent._probe(hypotheses)
            paths=set(listing.splitlines())
            row={"kind":"genuine-planner-only","fixture":sample["name"],"provider":client.provider,
                "model_mapping":client.models,"seed":47,"prompt_version":prompts.PROMPT_VERSION,"prompt_hash":prompts.prompt_hash(),
                "base_sha":frozen["sha"],"snapshot_digest":frozen["digest"],"image":runner.environment_image(env),
                "brief_sha256":hashlib.sha256(brief.encode()).hexdigest(),"failure_excerpt":excerpt,
                "source_context":agent._planner_context,"hypotheses":[h.model_dump(mode="json") for h in hypotheses],
                "planner_error":agent.report.planner_error,"calls":[call.model_dump(mode="json") for call in client.calls],
                "all_cited_paths_exist":all(path in paths for h in hypotheses for path in h.files),
                "limitations":["Literal probe results are not proof of a causal explanation.","This is planning quality evidence, not an autonomous repair or comparison-arm trial."]}
            dest.write_text(json.dumps(row,indent=2));print(json.dumps({"fixture":sample["name"],"planner_error":row["planner_error"],"hypotheses":len(hypotheses)}),flush=True)
        finally:runner.discard(env)


if __name__=="__main__":main()
