"""Budget, streaming and evaluation acceptance without provider calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from app.repair import contracts as c
from app.repair.budgets import TaskBudget, limits_for, estimate
from app.repair.evaluation import summarize, run_slot, ARMS
from tests.test_repair.test_service import store, repo_contract
from tests.test_repair.test_backend_v5 import svc as full_store


def test_budget_can_only_reduce_ceiling_and_estimate_discloses_limits():
    from pydantic import ValidationError
    for b in [{"max_candidates":3},{"coder_tokens":160001},{"coder_tokens":0},{"shell":"bad"}]:
        with pytest.raises(ValidationError):TaskBudget(**b)
    limits=limits_for(TaskBudget(max_candidates=1,coder_tokens=4096))
    assert limits.max_candidates==1 and limits.model_tokens.super==4096
    assert estimate(TaskBudget(coder_tokens=4096))["estimated_inference_usd"] < estimate()["estimated_inference_usd"]
    assert "overshoot" in estimate()["limitations"]


async def test_requested_budget_bound_into_admission_digest(store):
    svc,_=store
    task=await svc.create("budget-tenant",contract=repo_contract(),base_commit_sha="1"*40,repo_snapshot_digest="sha256:test",target="Restore service",limits=limits_for(TaskBudget(max_candidates=1,coder_tokens=2048)))
    row=await svc.row(task["id"],"budget-tenant")
    ecc=c.ECC.model_validate(row["ecc"])
    assert ecc.intact() and ecc.limits.model_tokens.super==2048 and ecc.limits.max_candidates==1


def test_comparison_denominators_failures_missing_costs_and_intervals():
    rows=[{"fixture":"one","seed":7,"arm":"A","accepted":True,"external_decision":"REJECTED","cost_usd":.2},
          {"fixture":"two","seed":7,"arm":"A","accepted":False,"external_decision":"INCONCLUSIVE","cost_usd":None},
          {"fixture":"one","seed":7,"arm":"D","accepted":True,"external_decision":"VERIFIED","cost_usd":.3}]
    result=summarize(rows,expected=8);a,d=result["arms"][:2]
    assert not result["complete"] and a["attempted"]==2 and a["false_accept_rate"]==1 and a["cost_usd"] is None
    assert d["verified_rate"]==1 and d["cost_per_verified"]==.3 and d["verified_ci95"][0]<1
    with pytest.raises(ValueError):summarize(rows+[rows[0]])


def test_no_ml_arm_is_still_externally_graded_without_feedback_to_agent():
    calls=[]
    class Agent:
        ecc=SimpleNamespace(limits=c.Limits(),repo_snapshot_digest="sha256:repo")
        def run(self):
            calls.append("agent-finished")
            return SimpleNamespace(finalist="c1",decision=c.Decision.VERIFIED,
                candidates=[SimpleNamespace(candidate=SimpleNamespace(candidate_id="c1",diff="patch"),code_receipt=SimpleNamespace(decision=c.Decision.VERIFIED))],final_ml_receipt=None,
                usage={"total_est_cost_usd":.1},seconds=2,task_id="t",provider="scripted",models={},prompt_hash="p",runner="fake",model_dump=lambda **k:{"decision":"VERIFIED"})
    class Oracle:
        def ml(self,*a,**k):calls.append("external-grade");return SimpleNamespace(decision=c.Decision.REJECTED)
    row,_=run_slot(agent=Agent(),oracle=Oracle(),fixture="x",seed=7,arm="D-ML")
    assert row["accepted"] and row["external_decision"]=="REJECTED" and calls==["agent-finished","external-grade"]
    assert ARMS["D-ML"]["internal_ml"] is False and ARMS["D-TAV"]["research_enabled"] is False


def test_mlops_sources_pinned_and_export_surface_separates_solutions():
    script=Path(__file__).resolve().parents[3]/"scripts/evaluate_mlops_subset.py"
    spec=importlib.util.spec_from_file_location("subset",script);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    manifest=mod.validate_sources()
    assert len(manifest["tasks"])==2 and len(manifest["commit"])==40
    for row in manifest["tasks"]:
        root=mod.CORPUS/row["id"]/"repo"
        assert not any(p.name in {"oracle","test_f2p.py","test_p2p.py","expected_changes.patch","metadata.json"} for p in root.rglob("*"))


def test_publication_refuses_scripted_replay_and_incomplete_comparison(tmp_path):
    root=Path(__file__).resolve().parents[3]
    def load(name):
        spec=importlib.util.spec_from_file_location(name,root/"scripts"/(name+".py"))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        return module
    replay=load("publish_repair_replay")
    source=tmp_path/"scripted.json"
    source.write_text(json.dumps({"provider":"scripted","calls":[{"ok":True}]}))
    with pytest.raises(ValueError,match="actual successful model"):
        replay.publish(source,"not_real","churn-service")
    evidence=load("publish_repair_evidence")
    source.write_text(json.dumps({"kind":"autonomous-comparison","complete":False,"rows":[{"provider":"nvidia_api"}]}))
    with pytest.raises(ValueError,match="complete measured"):
        evidence.build(source)


async def test_budget_estimate_api_rejects_expansion_and_keeps_actor_scope(full_store):
    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from app.repair import routes
    svc=full_store;app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[routes.get_service]=lambda:svc
    app.dependency_overrides[routes.autopilot_tenant]=lambda:"budget-tenant"
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        r=await client.post("/api/v1/v5/budget/estimate",json={"max_candidates":1,"coder_tokens":4096})
        assert r.status_code==200 and r.json()["limits"]["max_candidates"]==1
        assert (await client.post("/api/v1/v5/budget/estimate",json={"max_candidates":4})).status_code==422


def test_authorized_nvidia_credentials_back_off_independently_and_never_enter_receipts():
    import httpx
    from app.repair.agent.llm import ChatClient
    seen=[]
    def handler(req):
        key=req.headers["Authorization"];seen.append(key)
        if key=="Bearer first-secret":return httpx.Response(429,headers={"Retry-After":"120"})
        return httpx.Response(200,json={"choices":[{"message":{"content":"{}"},"finish_reason":"stop"}],"usage":{"prompt_tokens":10,"completion_tokens":2}})
    client=ChatClient(provider="nvidia_api",base_url="https://integrate.api.nvidia.com/v1",api_key="first-secret",api_keys=["second-secret"],models={"ultra":"ultra","super":"ultra","nano":"ultra"},budget=c.TierTokens(),transport=httpx.MockTransport(handler),retry_pause_s=0)
    child=client.scoped("code:c1",4096)
    assert child.complete("super",[],max_tokens=64,purpose="code:c1")=="{}"
    assert client.complete("ultra",[],max_tokens=64,purpose="plan")=="{}"
    assert seen==["Bearer first-secret","Bearer second-secret","Bearer second-secret"]
    serialized=json.dumps([r.model_dump() for r in client.calls])
    assert "first-secret" not in serialized and "second-secret" not in serialized


def test_live_progress_preview_is_bounded_and_does_not_freeze_candidate(tmp_path):
    from app.repair.agent.loop import RepairAgent, CandidateRun, Step
    base=tmp_path/"base";work=tmp_path/"work";base.mkdir();work.mkdir()
    (base/"x.py").write_text("old=1\n");(work/"x.py").write_text("new='"+"x"*70000+"'\n")
    events=[]
    agent=SimpleNamespace(base_dir=str(base),roots=["x.py"],llm=SimpleNamespace(calls=[],usage=lambda:{}),report=SimpleNamespace(events=events),_event=lambda typ,actor,**data:events.append({"type":typ,**data}))
    run=CandidateRun(candidate=c.Candidate(task_id="t",candidate_id="c1"),steps=[Step(n=1,action="apply_edit",ok=True)])
    RepairAgent._progress(agent,run,SimpleNamespace(dir=work))
    assert events[0]["type"]=="candidate.progress" and events[0]["diff_truncated"] and len(events[0]["diff"])<=64000
    assert events[1]["tool_calls"]==1 and run.candidate.status=="EXPLORING"


def test_optional_inventory_never_exposes_host_paths_or_starts_candidate_code(tmp_path):
    import threading
    from app.repair.sandbox.local import LocalContainerRunner
    from app.repair.sandbox.mirror import MirroredEnv
    from types import SimpleNamespace
    from app.repair.sandbox.base import SandboxError
    root=tmp_path/"environment";work=root/"work";work.mkdir(parents=True);(work/"x.py").write_text("x=1\n")
    (work/".env").write_text("private")
    (work/"sealed").mkdir();(work/"sealed/labels.csv").write_text("private")
    runner=LocalContainerRunner(tmp_path/"runner")
    runner._environments["env"]=SimpleNamespace(root=root,lock=threading.RLock())
    assert runner.inventory("env")==["x.py"]
    with pytest.raises(SandboxError):runner.inventory("sibling")
    admitted=tmp_path/"admitted";admitted.mkdir();(admitted/"x.py").write_text("x=1\n")
    mirror=MirroredEnv(runner,"env",str(admitted))
    try:
        assert mirror.pull()==1 and (mirror.dir/"x.py").read_text()=="x=1\n"
        assert not (mirror.dir/".env").exists() and not (mirror.dir/"sealed/labels.csv").exists()
    finally:mirror.close()


def test_remote_inventory_capability_and_older_service_fallback():
    import httpx
    from app.repair.sandbox.remote import RemoteRunner
    old=RemoteRunner("http://sandbox","test",transport=httpx.MockTransport(lambda r:httpx.Response(404)))
    assert old.inventory("env") is None
    new=RemoteRunner("http://sandbox","test",transport=httpx.MockTransport(lambda r:httpx.Response(200,json={"paths":["src/a.py"]})))
    assert new.inventory("env")==["src/a.py"]
