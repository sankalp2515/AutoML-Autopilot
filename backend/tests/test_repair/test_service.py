"""C4 persistence checks run identically on SQLite and an explicitly isolated Postgres DB."""
import asyncio
from dataclasses import replace
import hashlib
import os
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base
from app.repair import contracts as c, models as m
from app.repair.service import Conflict, NotFound, RepairService

TABLES = [m.Task.__table__, m.Candidate.__table__, m.Attempt.__table__, m.Receipt.__table__, m.Event.__table__]


def repo_contract():
    return c.RepoContract.model_validate({
        "version": 1, "name": "small-repo", "commands": {"test": "pytest -q tests"},
        "task": {"type": "restore"},
        "policy": {"allowed_edit_roots": ["src/", "tests/"], "immutable": ["data/"]},
    })


@pytest_asyncio.fixture(params=["sqlite", "postgres"])
async def store(request, tmp_path):
    if request.param == "postgres":
        url = os.environ.get("REPAIR_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("set REPAIR_TEST_POSTGRES_URL to a disposable database")
    else:
        url = f"sqlite+aiosqlite:///{tmp_path / 'repair.db'}"
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=TABLES))
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    clock = [1_000_000.0]
    service = RepairService(sessions, clock=lambda: clock[0])
    yield service, clock
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.drop_all(sync, tables=TABLES))
    await engine.dispose()


async def create(service, tenant="tenant-a"):
    return await service.create(tenant, contract=repo_contract(), base_commit_sha="a" * 40,
                                repo_snapshot_digest="sha256:" + "b" * 64, target="Restore training")


def candidate(task):
    diff = "--- a/src/a.py\n+++ b/src/a.py\n@@ -1 +1 @@\n-old\n+new\n"
    return c.Candidate(candidate_id=str(uuid.uuid4()), task_id=task["id"], diff=diff,
                       diff_digest="sha256:" + hashlib.sha256(diff.encode()).hexdigest())


def receipt(task, cand):
    return c.CodeReceipt(task_id=task["id"], candidate_id=cand.candidate_id,
                          contract_digest=task["contract_digest"], diff_digest=cand.diff_digest,
                          checks=[], decision=c.Decision.VERIFIED, runner="local-container").sealed()


async def counts(service):
    async with service.sessions() as db:
        return [len((await db.execute(select(model))).scalars().all())
                for model in (m.Candidate, m.Attempt, m.Receipt, m.Event)]


async def test_durable_stub_stops_at_mapping(store):
    service, _ = store
    task = await create(service)
    assert await service.work_once(owner="worker-a")
    result = await service.get(task["id"], "tenant-a")
    assert result["status"] == "MAPPING" and result["engine"] == "stub"
    assert not await service.work_once(owner="worker-b")
    events = await service.events(task["id"], "tenant-a")
    assert [event["seq"] for event in events] == [1, 2, 3]
    assert all(event["data"].get("executed") is False for event in events[1:])


async def test_lease_can_have_only_one_owner(store):
    service, _ = store
    task = await create(service)
    outcomes = await asyncio.gather(
        service.claim(task["id"], "tenant-a", "a"),
        service.claim(task["id"], "tenant-a", "b"), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in outcomes) == 1
    assert sum(isinstance(result, Conflict) for result in outcomes) == 1


async def test_stale_worker_cannot_write_any_record(store):
    service, clock = store
    task = await create(service)
    old = await service.claim(task["id"], "tenant-a", "worker-a", lease_seconds=5)
    clock[0] += 6
    # A restarted process can reuse its worker name. The fence must protect it
    # independently of the owner string and even if a stale writer reads version.
    current = await service.claim(task["id"], "tenant-a", "worker-a")
    cand = candidate(task)
    current = await service.save_candidate(current, cand)
    before = await counts(service)
    # Even a worker that has read the latest version cannot revive an old fence.
    old = replace(old, version=current.version)
    with pytest.raises(Conflict):
        await service.transition(old, c.TaskStatus.SNAPSHOTTING, event_type="repo.snapshotted")
    with pytest.raises(Conflict):
        await service.save_candidate(old, candidate(task))
    with pytest.raises(Conflict):
        await service.save_attempt(old, cand.candidate_id, {"exit_code": 0})
    with pytest.raises(Conflict):
        await service.save_receipt(old, receipt(task, cand))
    with pytest.raises(Conflict):
        await service.renew(old)
    with pytest.raises(Conflict):
        await service.release(old)
    assert await counts(service) == before
    assert (await service.get(task["id"], "tenant-a"))["fencing_token"] == current.fencing_token


async def test_expired_lease_alone_denies_writes(store):
    service, clock = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker-a", lease_seconds=5)
    clock[0] += 5
    with pytest.raises(Conflict):
        await service.transition(lease, c.TaskStatus.SNAPSHOTTING, event_type="repo.snapshotted")
    with pytest.raises(Conflict):
        await service.renew(lease)


async def test_version_cas_allows_one_effect_and_one_event(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    outcomes = await asyncio.gather(service.save_candidate(lease, candidate(task)),
                                    service.save_candidate(lease, candidate(task)), return_exceptions=True)
    assert sum(isinstance(outcome, Conflict) for outcome in outcomes) == 1
    assert (await counts(service))[0] == 1
    assert [event["seq"] for event in await service.events(task["id"], "tenant-a")] == [1, 2]


async def test_invalid_transition_is_atomic(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    with pytest.raises(Conflict):
        await service.transition(lease, c.TaskStatus.REVIEW_READY, event_type="candidate.verified")
    assert (await service.get(task["id"], "tenant-a"))["status"] == "CREATED"
    assert len(await service.events(task["id"], "tenant-a")) == 1


async def test_cancel_fences_worker_and_is_idempotent(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cancelled = await service.cancel(task["id"], "tenant-a", expected_version=lease.version)
    assert cancelled["status"] == "CANCELLED"
    assert cancelled["fencing_token"] > lease.fencing_token
    assert await service.cancel(task["id"], "tenant-a") == cancelled
    with pytest.raises(Conflict):
        await service.transition(lease, c.TaskStatus.SNAPSHOTTING, event_type="repo.snapshotted")
    with pytest.raises(Conflict):
        await service.claim(task["id"], "tenant-a", "other")
    assert [event["type"] for event in await service.events(task["id"], "tenant-a")] == [
        "task.created", "task.cancelled"]


async def test_cancel_with_wrong_version_does_not_mutate(store):
    service, _ = store
    task = await create(service)
    with pytest.raises(Conflict):
        await service.cancel(task["id"], "tenant-a", expected_version=42)
    assert (await service.get(task["id"], "tenant-a"))["version"] == 0


async def test_child_data_and_receipts_are_bound_and_immutable(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cand = candidate(task)
    lease = await service.save_candidate(lease, cand)
    lease = await service.save_attempt(lease, cand.candidate_id, {"exit_code": 0})
    code = receipt(task, cand)
    lease = await service.save_receipt(lease, code)
    combined = c.VerifiedChangeReceipt.combine(code, None, False)
    lease = await service.save_receipt(lease, combined)
    stored = await service.receipts(cand.candidate_id, "tenant-a")
    assert stored["combined"]["decision"] == "VERIFIED"
    with pytest.raises(Conflict):
        await service.save_receipt(lease, code)
    with pytest.raises(Conflict):
        await service.save_candidate(lease, cand)
    assert (await counts(service))[:3] == [1, 1, 2]


async def test_receipt_and_diff_tampering_is_rejected(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cand = candidate(task)
    with pytest.raises(ValueError, match="diff digest"):
        await service.save_candidate(lease, cand.model_copy(update={"diff": "tampered"}))
    lease = await service.save_candidate(lease, cand)
    code = receipt(task, cand)
    with pytest.raises(ValueError, match="receipt digest"):
        await service.save_receipt(lease, code.model_copy(update={"decision": c.Decision.REJECTED}))
    with pytest.raises(ValueError, match="provenance"):
        await service.save_receipt(lease, code.model_copy(update={"contract_digest": "wrong"}))
    assert (await counts(service))[2] == 0


async def test_tenant_scopes_every_read_and_cancel(store):
    service, _ = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cand = candidate(task)
    await service.save_candidate(lease, cand)
    assert await service.list_tasks("tenant-b") == []
    for action in (service.get(task["id"], "tenant-b"), service.events(task["id"], "tenant-b"),
                   service.candidates(task["id"], "tenant-b"), service.candidate(cand.candidate_id, "tenant-b"),
                   service.receipts(cand.candidate_id, "tenant-b"), service.cancel(task["id"], "tenant-b")):
        with pytest.raises(NotFound):
            await action


async def test_event_cursor_is_resumable(store):
    service, _ = store
    task = await create(service)
    await service.run_stub(task["id"], "tenant-a")
    first = await service.events(task["id"], "tenant-a", limit=2)
    rest = await service.events(task["id"], "tenant-a", after=first[-1]["seq"])
    assert [item["seq"] for item in first + rest] == [1, 2, 3]
    assert await service.events(task["id"], "tenant-a", after=3) == []


async def test_contract_allows_review_ready_to_pr_drafted_only(store):
    service, clock = store
    task = await create(service)
    lease = await service.claim(task["id"], "tenant-a", "worker")
    for status, event in [("SNAPSHOTTING", "repo.snapshotted"), ("MAPPING", "repo.mapped"),
                          ("INVESTIGATING", "hypothesis.proposed"), ("PATCHING", "patch.applied"),
                          ("CODE_VERIFYING", "tests.passed"), ("SELECTING", "code_oracle.passed"),
                          ("REVIEW_READY", "candidate.verified")]:
        lease = await service.transition(lease, c.TaskStatus(status), event_type=event)
    clock[0] += 31
    lease = await service.claim(task["id"], "tenant-a", "delivery-worker")
    await service.transition(lease, c.TaskStatus.PR_DRAFTED, event_type="pr.drafted")
    with pytest.raises(Conflict):
        await service.cancel(task["id"], "tenant-a")
    with pytest.raises(Conflict):
        await service.claim(task["id"], "tenant-a", "late-worker")


async def test_combined_receipt_cannot_bypass_missing_ml(store):
    service, _ = store
    values = repo_contract().model_dump(mode="json")
    values["task"]["ml_invariants"] = ["holdout_non_inferior"]
    values["commands"].update(train="python train.py", predict="python predict.py")
    values["data"] = {"train": "train.csv", "holdout_features": "holdout.csv",
                      "id_column": "customer_id", "target": "churned"}
    task = await service.create("tenant-a", contract=c.RepoContract.model_validate(values),
                                base_commit_sha="a" * 40, repo_snapshot_digest="sha256:" + "b" * 64,
                                target="Restore predictions")
    lease = await service.claim(task["id"], "tenant-a", "worker")
    cand = candidate(task)
    lease = await service.save_candidate(lease, cand)
    code = receipt(task, cand)
    lease = await service.save_receipt(lease, code)
    with pytest.raises(ValueError, match="contradicts"):
        await service.save_receipt(lease, c.VerifiedChangeReceipt.combine(code, None, False))
    await service.save_receipt(lease, c.VerifiedChangeReceipt.combine(code, None, True))
    assert (await service.receipts(cand.candidate_id, "tenant-a"))["combined"]["decision"] == "INCONCLUSIVE"
