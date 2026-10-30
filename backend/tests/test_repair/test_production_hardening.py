"""Cross-process admission and verifier invariants; no provider calls."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from dataclasses import replace
import pytest
from app.repair import contracts as c
from app.repair.admission import AdmissionDenied
from app.repair.oracle.ledger import DatabaseLedger, BoundLedger
from app.repair.service import Conflict
from tests.test_repair.test_service import store,repo_contract,candidate
from tests.test_repair.test_backend_v5 import svc as full_store


def request():
    return dict(contract=repo_contract(),base_commit_sha="a"*40,repo_snapshot_digest="sha256:repo",target="Restore service")


def policy(**override):
    return {"tenant_tasks":100,"cap_usd":1.,"cap_tokens":100,"reserve_usd":.5,"reserve_tokens":20,**override}


def test_verifier_cache_retention_never_erases_permits(tmp_path,monkeypatch):
    from app.config import settings
    from app.repair.oracle.grading import cleanup_cache,_publish_cache
    monkeypatch.setattr(settings,"REPAIR_VERIFIER_STATE_DIR",str(tmp_path))
    cache=tmp_path/"cache";cache.mkdir()
    old=cache/("a"*36+".tests.json")
    _publish_cache(old,'{"parsed":true}')
    os.utime(old,(1,1))
    permit=tmp_path/"ledger";permit.mkdir();(permit/"keep").write_text("forever")
    unknown=cache/"operator-note";unknown.write_text("keep")
    assert cleanup_cache()==1
    assert not old.exists()
    assert (permit/"keep").is_file() and unknown.is_file()


async def test_heartbeat_continues_during_busy_run(tmp_path,monkeypatch):
    from app.repair import service,operations
    class Busy:
        beats=0
        async def heartbeat(self,*args):self.beats+=1
        async def reap_expired(self):pass
        async def work_once(self,**kwargs):await asyncio.sleep(2)
    busy=Busy();monkeypatch.setattr(service,"default_service",busy)
    monkeypatch.setattr(operations,"heartbeat_path",lambda:tmp_path/"beat")
    task=asyncio.create_task(service.worker_loop(heartbeat_seconds=.02))
    try:
        await asyncio.sleep(.15)
        assert busy.beats>=3 and (tmp_path/"beat").is_file()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task


async def test_metrics_do_not_turn_unknown_cost_into_zero():
    from app.repair import ops
    class Store:
        async def recent(self,*args):return [("REVIEW_READY",0,{"decision":"VERIFIED","seconds":1,"usage":{}})]
    result=await ops.metrics(Store())
    assert result["cost_usd"] is None and result["unknown_cost_runs"]==1
    assert result["known_cost_usd"]==0


async def test_parallel_global_reservation_cannot_oversubscribe(store):
    svc,_=store
    rows=await asyncio.gather(*(svc.create(f"tenant-{n}",**request(),admission_policy=policy()) for n in range(8)),return_exceptions=True)
    assert sum(isinstance(r,dict) for r in rows)==2
    assert sum(isinstance(r,AdmissionDenied) for r in rows)==6


async def test_lifetime_credit_cap_survives_daily_window_and_restart(store):
    from app.repair.service import RepairService
    svc,clock=store
    options=policy(cap_usd=10,cap_tokens=1000,total_cap_usd=1)
    first=await svc.create("one",**request(),admission_policy=options,idempotency_key="same")
    clock[0]+=90000
    restarted=RepairService(svc.sessions,clock=lambda:clock[0])
    assert (await restarted.create("one",**request(),admission_policy=options,idempotency_key="same"))["id"]==first["id"]
    await restarted.create("two",**request(),admission_policy=options)
    with pytest.raises(AdmissionDenied,match="hackathon inference allowance"):
        await restarted.create("three",**request(),admission_policy=options)


async def test_parallel_lifetime_reservations_cannot_overspend(store):
    svc,_=store
    rows=await asyncio.gather(*(svc.create(f"judge-{n}",**request(),admission_policy=policy(
        cap_usd=10,cap_tokens=1000,total_cap_usd=1)) for n in range(8)),return_exceptions=True)
    assert sum(isinstance(row,dict) for row in rows)==2
    assert sum(isinstance(row,AdmissionDenied) for row in rows)==6


async def test_judge_cooldown_retries_and_shared_slots(store):
    from app.repair.admission import AdmissionLimited
    from app.repair import contracts as c
    svc,clock=store
    options=policy(cap_usd=10,cap_tokens=1000,cooldown_s=30,max_active=1)
    task=await svc.create("judge",**request(),admission_policy=options,idempotency_key="repair")
    assert (await svc.create("judge",**request(),admission_policy=options,idempotency_key="repair"))["id"]==task["id"]
    with pytest.raises(AdmissionLimited,match="wait") as error:
        await svc.create("judge",**request(),admission_policy=options)
    assert error.value.retry_after==31
    with pytest.raises(AdmissionLimited,match="slots"):
        await svc.create("other",**request(),admission_policy=options)
    await svc.cancel(task["id"],"judge")
    clock[0]+=31
    await svc.create("judge",**request(),admission_policy=options)


async def test_judge_only_invite_cannot_be_bypassed_by_signin(monkeypatch):
    from app.repair import ops
    from app.config import settings
    monkeypatch.setattr(settings,"REPAIR_LIVE_ENABLED",True)
    monkeypatch.setattr(settings,"REPAIR_JUDGE_ONLY",True)
    monkeypatch.setattr(settings,"REPAIR_INVITE_CODES","judge-code-for-test")
    for trusted in (True,False):
        with pytest.raises(ops.Denied,match="invited judges"):
            await ops.admit_live_run(None,"judge",None,trusted=trusted,skip_capacity=True)
        assert await ops.admit_live_run(None,"judge","judge-code-for-test",trusted=trusted,skip_capacity=True)
    monkeypatch.setattr(settings,"REPAIR_INVITE_CODES","")
    with pytest.raises(ops.Denied,match="invited judges"):
        await ops.admit_live_run(None,"judge",None,trusted=True,skip_capacity=True)


async def test_admission_429_includes_retry_after():
    from app.repair.routes import call
    from app.repair.admission import AdmissionLimited
    from fastapi import HTTPException
    async def limited():raise AdmissionLimited("wait",30)
    with pytest.raises(HTTPException) as error:await call(limited())
    assert error.value.status_code==429 and error.value.headers=={"Retry-After":"30"}


async def test_concurrent_idempotency_is_scoped_and_payload_bound(store):
    svc,_=store
    rows=await asyncio.gather(*(svc.create("one",**request(),admission_policy=policy(tenant_tasks=1),idempotency_key="retry") for _ in range(6)))
    assert len({r["id"] for r in rows})==1
    with pytest.raises(Conflict):
        await svc.create("one",**{**request(),"target":"A different task"},idempotency_key="retry")
    other=await svc.create("two",**request(),admission_policy=policy(),idempotency_key="retry")
    assert other["id"]!=rows[0]["id"]


async def test_token_limit_and_unknown_usage_fail_closed(store):
    svc,_=store
    await svc.create("one",**request(),admission_policy=policy(cap_tokens=20))
    with pytest.raises(AdmissionDenied,match="token"):
        await svc.create("two",**request(),admission_policy=policy(cap_tokens=20))


async def test_maintenance_blocks_new_admission_and_queued_jobs_expire(store):
    from app.repair import models as m
    svc,clock=store
    async with svc.sessions() as db,db.begin():db.add(m.AdmissionLock(id="maintenance",version=1))
    with pytest.raises(AdmissionDenied,match="maintenance"):
        await svc.create("one",**request(),admission_policy=policy())
    task=await svc.create("one",**request(),spec={"needs_verifier_admission":True})
    clock[0]+=86400
    assert await svc.reap_expired()==1
    assert (await svc.get(task["id"],"one"))["status"]=="FAILED"
    assert (await svc.events(task["id"],"one"))[-1]["data"]["reason"]=="E_QUEUE_TIMEOUT"


async def test_worker_token_cannot_register_contract_or_run_trusted_evaluator(monkeypatch):
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.repair.oracle import server as verifier
    from app.repair.sandbox import server as sandbox
    from app.config import settings
    from types import SimpleNamespace
    monkeypatch.setattr(settings,"REPAIR_VERIFIER_TOKEN","g"*64)
    monkeypatch.setattr(settings,"REPAIR_ADMISSION_TOKEN","a"*64)
    monkeypatch.setattr(settings,"REPAIR_SANDBOX_TOKEN","s"*64)
    monkeypatch.setattr(settings,"REPAIR_TRUSTED_SANDBOX_TOKEN","t"*64)
    admitted=[];monkeypatch.setattr(verifier,"_grader",SimpleNamespace(register=lambda ref:admitted.append(ref)))
    app=FastAPI();app.include_router(verifier.router)
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        body={"task_id":"test-task","ecc":{},"snapshot_dir":"unused"}
        assert (await client.post("/v5/oracle/admit",json=body,headers={"Authorization":"Bearer "+"g"*64})).status_code==401
        assert not admitted
        assert (await client.post("/v5/oracle/admit",json=body,headers={"Authorization":"Bearer "+"a"*64})).status_code==200
    async with AsyncClient(transport=ASGITransport(app=sandbox.app),base_url="http://test") as client:
        assert (await client.post("/trusted-run",json={"env_id":"env","argv":["python"],"timeout_s":1},
            headers={"Authorization":"Bearer "+"s"*64})).status_code==401


def test_expanded_archives_are_bounded_before_extraction(monkeypatch):
    import io,tarfile,base64
    from app.repair.sandbox.server import unpacked
    from app.config import settings
    from fastapi import HTTPException
    monkeypatch.setattr(settings,"REPAIR_MAX_REPO_MB",1)
    raw=io.BytesIO()
    with tarfile.open(fileobj=raw,mode="w:gz") as archive:
        data=b"a"*(1024*1024+1);member=tarfile.TarInfo("large.txt");member.size=len(data)
        archive.addfile(member,io.BytesIO(data))
    assert len(raw.getvalue())<10000
    with pytest.raises(HTTPException) as rejected:
        with unpacked(base64.b64encode(raw.getvalue()).decode()):pytest.fail("must refuse before yielding")
    assert rejected.value.status_code==413


def test_source_catalog_has_no_sealed_material(tmp_path,monkeypatch):
    import importlib.util
    from app.config import settings
    from app.repair import snapshots
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location("sources",root/"scripts/prepare_v5_sources.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    out=tmp_path/"source-only";assert module.prepare(out)==6
    assert not any(p.name in {"sealed","holdout_labels.csv","reference_fix.patch","wrong_fix.patch"} for p in out.rglob("*"))
    monkeypatch.setattr(settings,"REPAIR_FIXTURES_DIR",str(out))
    assert len(snapshots.samples())==6


async def test_unregistered_task_is_not_claimed_by_worker(store,monkeypatch):
    svc,_=store
    task=await svc.create("one",**request(),spec={"needs_verifier_admission":True})
    assert await svc.work_once(owner="w")==False
    await asyncio.gather(svc.mark_admitted(task["id"],"one"),svc.mark_admitted(task["id"],"one"))
    assert not (await svc.row(task["id"],"one"))["spec"]["needs_verifier_admission"]


async def test_candidate_checkpoint_preserves_code_and_stale_worker_fails(store):
    svc,_=store;task=await svc.create("one",**request());lease=await svc.claim(task["id"],"one","w")
    cand=candidate(task);lease=await svc.save_candidate(lease,cand,emit=False)
    stale=lease
    lease=await svc.checkpoint_candidate(lease,cand,{"eligible":False})
    with pytest.raises(Conflict):await svc.checkpoint_candidate(stale,cand,{"eligible":True})
    changed=cand.model_copy(update={"summary":"changed immutable summary"})
    with pytest.raises(Conflict):await svc.checkpoint_candidate(lease,changed,{})


async def test_engine_crash_keeps_checkpoint_but_never_verifies_task(store,tmp_path,monkeypatch):
    from app.repair import engine
    from app.repair.agent.loop import CandidateRun
    from types import SimpleNamespace
    import yaml
    svc,_=store;repo=tmp_path/"repo";repo.mkdir()
    (repo/"autopilot.yaml").write_text(yaml.safe_dump(repo_contract().model_dump(mode="json")))
    task=await svc.create("one",**request(),spec={"snapshot_dir":str(repo)})
    loop=asyncio.get_running_loop()
    class Agent:
        def __init__(self,*args,on_checkpoint,**kwargs):
            self.checkpoint=on_checkpoint;self.report=SimpleNamespace(mode="full",runner="fake")
        def run(self):
            cand=candidate(task)
            receipt=c.CodeReceipt(task_id=task["id"],candidate_id=cand.candidate_id,
                contract_digest=task["contract_digest"],diff_digest=cand.diff_digest,
                checks=[],decision=c.Decision.VERIFIED,runner="fake").sealed()
            self.checkpoint(CandidateRun(candidate=cand,code_receipt=receipt))
            saved=asyncio.run_coroutine_threadsafe(svc.candidates(task["id"],"one"),loop).result()
            assert len(saved)==1 and saved[0]["code_receipt"]["decision"]=="VERIFIED"
            raise RuntimeError("simulated crash after checkpoint")
    monkeypatch.setattr(engine,"RepairAgent",Agent)
    monkeypatch.setattr(engine,"client_from_settings",lambda _:object())
    monkeypatch.setattr(engine,"make_runner",lambda:SimpleNamespace(name="fake",isolated=True))
    monkeypatch.setattr(engine,"make_oracles",lambda *args:object())
    await engine.run_task(svc,task["id"],"one",owner="worker")
    assert (await svc.get(task["id"],"one"))["status"]=="FAILED"
    saved=await svc.candidates(task["id"],"one")
    assert len(saved)==1 and "code" in await svc.receipts(saved[0]["candidate_id"],"one")


def test_missing_provider_usage_stays_unknown_and_closes_tier():
    import httpx
    from app.repair.agent.llm import ChatClient,BudgetExceeded
    client=ChatClient(provider="nvidia_api",base_url="http://fake",api_key="test",models={"ultra":"ultra"},
        budget=c.TierTokens(),transport=httpx.MockTransport(lambda _:httpx.Response(200,json={
            "choices":[{"message":{"content":"{}"},"finish_reason":"stop"}]})))
    assert client.complete("ultra",[],max_tokens=100,purpose="test")=="{}"
    assert client.usage()["total_est_cost_usd"] is None
    with pytest.raises(BudgetExceeded):client.complete("ultra",[],max_tokens=100,purpose="test")


def test_compacted_library_trace_still_triggers_official_research():
    from app.repair.agent.research import library_signal
    signal=library_signal("TypeError: OneHotEncoder.__init__() got an unexpected keyword argument 'sparse'")
    assert signal["package"]=="scikit-learn" and "OneHotEncoder" in signal["symbols"]
    assert library_signal("ValueError: X has 8 features but StandardScaler expects 9") is None


async def test_http_retries_do_not_restage_or_double_reserve(full_store,tmp_path,monkeypatch):
    from fastapi import FastAPI
    from httpx import AsyncClient,ASGITransport
    from app.config import settings
    from app.repair import routes,accounts,snapshots
    monkeypatch.setattr(settings,"DATA_DIR",str(tmp_path/"data"))
    monkeypatch.setattr(settings,"REPAIR_VERIFIER_URL","")
    monkeypatch.setattr(settings,"REPAIR_LIVE_ENABLED",True)
    monkeypatch.setattr(settings,"REPAIR_INVITE_CODES","")
    monkeypatch.setattr(settings,"REPAIR_DAILY_TASKS_PER_TENANT",1)
    staged=snapshots.stage_sample("tenant","loan-scorer")
    app=FastAPI();app.include_router(routes.router)
    app.dependency_overrides[routes.get_service]=lambda:full_store
    app.dependency_overrides[accounts.actor]=lambda:accounts.Actor("tenant",None,"owner")
    body={"repo_id":staged["id"],"target":"Restore the pinned API"}
    rc=c.RepoContract.from_yaml(staged["contract_yaml"]);body["fail_to_pass"]=rc.task.fail_to_pass
    async with AsyncClient(transport=ASGITransport(app=app),base_url="http://test") as client:
        rows=await asyncio.gather(*(client.post("/api/v1/v5/tasks",json=body,headers={"Idempotency-Key":"one-request"}) for _ in range(3)))
        assert all(r.status_code==201 for r in rows),[r.text for r in rows]
        assert len({r.json()["id"] for r in rows})==1
        changed=await client.post("/api/v1/v5/tasks",json={**body,"target":"Different"},headers={"Idempotency-Key":"one-request"})
        assert changed.status_code==409


def test_verifier_admission_binds_original_snapshot_and_sealed_reference(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from app.config import settings
    from app.repair import snapshots
    from app.repair.oracle.grading import Grader,GradeRef,GradingError
    import uuid
    monkeypatch.setattr(settings,"DATA_DIR",str(tmp_path/"data"))
    monkeypatch.setattr(settings,"REPAIR_LEDGER_DATABASE_URL",f"sqlite+aiosqlite:///{tmp_path/'ledger.db'}")
    staged=snapshots.stage_sample("owner","loan-scorer");frozen=snapshots.freeze(staged["id"],"owner")
    ecc=c.ECC.build(task_id=str(uuid.uuid4()),base_commit_sha=frozen["sha"],repo_snapshot_digest=frozen["digest"],
        contract=frozen["contract"],target="Restore the pinned API",environment={"image":"sha256:"+"a"*64})
    ref=GradeRef(ecc.task_id,ecc.model_dump(mode="json"),frozen["snapshot_dir"],"fixture:loan-scorer")
    grader=Grader(SimpleNamespace(isolated=True))
    with pytest.raises(GradingError):grader._load(ref)
    grader.register(ref);grader._load(ref)
    with pytest.raises(GradingError):grader._load(replace(ref,sealed_ref="fixture:credit-approval"))
    (Path(frozen["snapshot_dir"])/"scorer/features.py").write_text("tampered=1\n")
    with pytest.raises(GradingError,match="snapshot"):grader._load(ref)


@pytest.mark.parametrize("backend",["sqlite","postgres"])
def test_independent_verifiers_share_atomic_diff_bound_holdout(tmp_path,backend):
    url=os.environ.get("REPAIR_TEST_LEDGER_URL") if backend=="postgres" else f"sqlite+aiosqlite:///{tmp_path/'ledger.db'}"
    if not url:pytest.skip("set REPAIR_TEST_LEDGER_URL to a disposable PostgreSQL database")
    import uuid
    task=str(uuid.uuid4())
    one,two=DatabaseLedger(url),DatabaseLedger(url);one.initialize();two.initialize()
    with pytest.raises(PermissionError):one.check(task,"digest")
    one.admit(task,"digest");two.check(task,"digest")
    with pytest.raises(PermissionError):two.admit(task,"changed")
    def attempt(n):
        try:BoundLedger(one if n%2 else two,"patch-A" if n%2 else "patch-B").consume(task,"c1");return n%2
        except PermissionError:return None
    with ThreadPoolExecutor(max_workers=8) as workers:results=list(workers.map(attempt,range(8)))
    assert len({r for r in results if r is not None})==1
    winner=next(r for r in results if r is not None)
    restarted=DatabaseLedger(url)
    BoundLedger(restarted,"patch-A" if winner else "patch-B").consume(task,"c1")
    with pytest.raises(PermissionError):BoundLedger(restarted,"patch-A" if winner else "patch-B").consume(task,"c2")
