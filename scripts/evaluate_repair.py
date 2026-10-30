"""Explicitly opt-in, bounded four-arm model study; no dotenv reads or implicit inference.
Plan and summarize are offline. Run requires --allow-model-calls, a dollar cap,
an isolated runner and a verifier service. Existing environment config supplies keys.
"""
import argparse
import json
from pathlib import Path
import sys
import uuid
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/"backend"))
from pydantic_settings.sources import DotEnvSettingsSource
DotEnvSettingsSource.__call__=lambda self: {}
from app.repair.evaluation import ARMS,run_slot,summarize,digest_file

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("command",choices=["plan","run","summarize"])
    p.add_argument("--fixtures",nargs="+",default=["churn-service","loan-scorer","refund-risk","credit-approval","fraud-batch","pricing-model"])
    p.add_argument("--seeds",nargs="+",type=int,default=[7,17,29]);p.add_argument("--out",required=True)
    p.add_argument("--allow-model-calls",action="store_true");p.add_argument("--max-usd",type=float)
    p.add_argument("--workers",type=int,default=1);p.add_argument("--worker-index",type=int,default=0)
    p.add_argument("--max-runs",type=int);p.add_argument("--arms",nargs="+",choices=list(ARMS),default=list(ARMS))
    args=p.parse_args()
    if not 1<=args.workers<=4 or not 0<=args.worker_index<args.workers:p.error("invalid worker partition")
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    plan={"schema_version":1,"fixtures":args.fixtures,"seeds":args.seeds,"arms":{a:ARMS[a] for a in args.arms},"slots":len(args.fixtures)*len(args.seeds)*len(args.arms),"dataset_seed":7,
          "model_seed_note":"Same requested sampling seed in each arm; provider determinism is not guaranteed.","artifact_kind":"plan-not-results"}
    plan_path=out/"plan.json"
    if args.command=="plan":plan_path.write_text(json.dumps(plan,indent=2));return
    if args.command=="summarize":
        saved=json.loads(plan_path.read_text());rows=[]
        for f in sorted(out.glob("*/result.json")):
            row=json.loads(f.read_text());report_path=f.parent/"report.json"
            if report_path.exists():
                report=json.loads(report_path.read_text())
                row["cost_complete"]=bool(report.get("calls")) and all(call.get("ok") for call in report["calls"])
            else:row["cost_complete"]=False
            rows.append(row)
        (out/"summary.json").write_text(json.dumps(summarize(rows,expected=saved["slots"]),indent=2));return
    if not args.allow_model_calls or not ((args.max_usd is not None and args.max_usd>0) or (args.max_runs is not None and args.max_runs>0)):p.error("run needs --allow-model-calls and a positive --max-usd or --max-runs")
    from app.config import settings
    from app.repair import contracts as c,snapshots
    from app.repair.agent.loop import RepairAgent
    from app.repair.agent.llm import client_from_settings
    from app.repair.agent.oracles import make_oracles
    from app.repair.oracle.grading import GradeRef
    from app.repair.sandbox.factory import make_runner
    from app.repair.budgets import estimate
    runner=make_runner()
    if not runner.isolated or not settings.REPAIR_VERIFIER_URL:p.error("requires isolated runner and verifier service")
    if plan_path.exists() and json.loads(plan_path.read_text())!=plan:p.error("existing plan differs; use a new directory")
    if not plan_path.exists():plan_path.write_text(json.dumps(plan,indent=2))
    rows=[];spent=0.;nominal=estimate()["estimated_inference_usd"]
    for index,fixture in enumerate(args.fixtures):
      if index%args.workers!=args.worker_index:continue
      for seed in args.seeds:
       for arm in args.arms:
        opts=ARMS[arm]
        dest=out/f"{fixture}-{seed}-{arm}";dest.mkdir(exist_ok=True)
        if (dest/"result.json").exists():
            row=json.loads((dest/"result.json").read_text());rows.append(row)
            if row.get("cost_usd") is None and args.max_usd is not None:p.error("cannot resume dollar-capped study with unknown cost; review prior slot")
            spent+=row["cost_usd"] or 0;continue
        if args.max_runs is not None and len(rows)>=args.max_runs:p.error("run count limit reached")
        if args.max_usd is not None and spent+nominal>args.max_usd:p.error("next nominal slot exceeds remaining cap; no call started")
        staged=snapshots.stage_sample("evaluation",fixture);frozen=snapshots.freeze(staged["id"],"evaluation")
        ecc=c.ECC.build(task_id=str(uuid.uuid4()),base_commit_sha=frozen["sha"],repo_snapshot_digest=frozen["digest"],contract=frozen["contract"],target=next(s["task"] for s in snapshots.samples() if s["name"]==fixture))
        llm=client_from_settings(ecc.limits.model_tokens)
        if llm is None:p.error("no configured provider")
        if args.max_usd is None and (llm.provider!="nvidia_api" or llm.base_url!="https://integrate.api.nvidia.com/v1"):p.error("count-only study is restricted to the NVIDIA developer endpoint")
        llm.extra_body={**llm.extra_body,"seed":seed}
        oracle=make_oracles(runner,GradeRef(ecc.task_id,ecc.model_dump(mode="json"),frozen["snapshot_dir"],"fixture:"+fixture))
        print(json.dumps({"fixture":fixture,"seed":seed,"arm":arm,"stage":"starting"}),flush=True)
        deadline=time.monotonic()+ecc.limits.wall_seconds
        agent=RepairAgent(runner,llm,oracle,should_stop=lambda:time.monotonic()>deadline,on_event=lambda ev: print(json.dumps({"fixture":fixture,"arm":arm,"event":ev["type"]}),flush=True) if ev["type"] in {"baseline.reproduced","hypothesis.proposed","candidate.frozen","candidate.verified","candidate.rejected"} else None,base_dir=frozen["snapshot_dir"],contract=frozen["contract"],ecc=ecc,**opts)
        row,report=run_slot(agent=agent,oracle=oracle,fixture=fixture,seed=seed,arm=arm)
        row["plan_sha256"]=digest_file(plan_path)
        if report:
            (dest/"report.json").write_text(json.dumps(report,indent=2));row["report_sha256"]=digest_file(dest/"report.json")
        (dest/"result.json").write_text(json.dumps(row,indent=2));rows.append(row)
        if args.workers==1:(out/"summary.json").write_text(json.dumps(summarize(rows,expected=plan["slots"]),indent=2))
        if row.get("cost_usd") is None and args.max_usd is not None:p.error("unknown slot cost; stopping dollar-capped study before further calls")
        spent+=row["cost_usd"] or 0
        print(json.dumps({"fixture":fixture,"arm":arm,"agent_decision":row.get("agent_decision"),"external_decision":row.get("external_decision")}),flush=True)
    # A token ceiling is not a provider-enforced dollar ceiling: documented prompt overshoot remains.

if __name__=="__main__":main()
