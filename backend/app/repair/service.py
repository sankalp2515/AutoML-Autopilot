"""Transactional repair queue and fenced persistence; no model or repository execution.

Every worker write compares version, fencing token, owner and unexpired lease in
the database. Child records and their event are committed in the same transaction.
The durable stub worker stops at MAPPING, awaiting the agent integration card.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
import hashlib
import time
import uuid

from sqlalchemy import or_, select, update

from app.database import AsyncSessionLocal
from app.repair import contracts as c, models as m


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


@dataclass(frozen=True)
class Lease:
    task_id: str
    tenant_id: str
    owner: str
    version: int
    fencing_token: int


def task_json(task):
    # The ECC may contain grader digests, internal environment identifiers and
    # data provenance. Agent-facing contract projection is a separate boundary.
    return {"id": task.id, "status": task.status, "version": task.version,
            "fencing_token": task.fencing_token, "contract_digest": task.ecc["contract_digest"],
            "target": task.ecc["target"], "event_seq": task.event_seq,
            "cancel_requested": task.cancel_requested, "created_at": task.created_at,
            **_v5_fields(task)}


def _v5_fields(task):
    spec, rep = task.spec or {}, task.report or {}
    return {"engine": "v5", "mode": spec.get("mode", "full"), "sample": spec.get("sample"),
            "source": spec.get("source"), "repo_name": spec.get("repo_name"),
            "fail_to_pass": task.ecc["success"]["fail_to_pass"], "ml_invariants": task.ecc["success"]["ml_invariants"],
            "has_labels": bool(spec.get("sealed_ref")), "decision": rep.get("decision"), "reasons": rep.get("reasons"),
            "finalist": rep.get("finalist"), "cost_usd": (rep.get("usage") or {}).get("total_est_cost_usd"),
            "seconds": rep.get("seconds"), "error": spec.get("error")}


class RepairService:
    def __init__(self, sessions=AsyncSessionLocal, *, clock=time.time):
        self.sessions, self.clock = sessions, clock

    async def _task(self, db, task_id, tenant):
        task = (await db.execute(select(m.Task).where(m.Task.id == task_id,
                                                      m.Task.tenant_id == tenant))).scalar_one_or_none()
        if task is None:
            raise NotFound("Task not found")
        return task

    async def create(self, tenant, *, contract: c.RepoContract, base_commit_sha,
                     repo_snapshot_digest, target, spec: dict | None = None):
        if not 1 <= len(tenant) <= 128:
            raise ValueError("invalid tenant identity")
        task_id = str(uuid.uuid4())
        ecc = c.ECC.build(task_id=task_id, base_commit_sha=base_commit_sha,
                          repo_snapshot_digest=repo_snapshot_digest, contract=contract, target=target)
        async with self.sessions() as db, db.begin():
            task = m.Task(id=task_id, tenant_id=tenant, status=c.TaskStatus.CREATED.value,
                          version=0, fencing_token=0, ecc=ecc.model_dump(mode="json"),
                          event_seq=1, cancel_requested=False, created_at=self.clock(), spec=spec or {})
            db.add(task)
            await db.flush()
            event = c.Event(task_id=task_id, seq=1, type="task.created", actor="platform",
                            data={"engine": "v5", "contract_digest": ecc.contract_digest})
            db.add(m.Event(task_id=task_id, seq=1, payload=event.model_dump(mode="json")))
        return task_json(task)

    async def get(self, task_id, tenant):
        async with self.sessions() as db:
            return task_json(await self._task(db, task_id, tenant))

    async def list_tasks(self, tenant, *, limit=100, offset=0):
        async with self.sessions() as db:
            tasks = (await db.execute(select(m.Task).where(m.Task.tenant_id == tenant)
                     .order_by(m.Task.created_at, m.Task.id).offset(offset).limit(min(limit, 100)))).scalars()
            return [task_json(task) for task in tasks]

    async def events(self, task_id, tenant, *, after=0, limit=100):
        async with self.sessions() as db:
            await self._task(db, task_id, tenant)
            events = (await db.execute(select(m.Event).where(m.Event.task_id == task_id,
                      m.Event.seq > after).order_by(m.Event.seq).limit(min(limit, 500)))).scalars()
            return [event.payload for event in events]

    async def candidates(self, task_id, tenant):
        async with self.sessions() as db:
            await self._task(db, task_id, tenant)
            rows = (await db.execute(select(m.Candidate).where(m.Candidate.task_id == task_id)
                                    .order_by(m.Candidate.id))).scalars()
            return [dict(row.payload) for row in rows]

    async def candidate(self, candidate_id, tenant, *, task_id=None):
        async with self.sessions() as db:
            query = select(m.Candidate).join(m.Task).where(m.Candidate.id == candidate_id,
                                                           m.Task.tenant_id == tenant)
            if task_id is not None:
                query = query.where(m.Task.id == task_id)
            row = (await db.execute(query)).scalar_one_or_none()
            if row is None:
                raise NotFound("Candidate not found")
            return dict(row.payload)

    async def receipts(self, candidate_id, tenant, *, task_id=None):
        await self.candidate(candidate_id, tenant, task_id=task_id)
        async with self.sessions() as db:
            rows = (await db.execute(select(m.Receipt).where(m.Receipt.candidate_id == candidate_id)
                                    .order_by(m.Receipt.kind))).scalars()
            return {row.kind: row.payload for row in rows}

    async def claim(self, task_id, tenant, owner, *, lease_seconds=30):
        if not owner or len(owner) > 96 or not 1 <= lease_seconds <= 600:
            raise ValueError("invalid worker or lease duration")
        async with self.sessions() as db, db.begin():
            task = await self._task(db, task_id, tenant)
            if (c.TaskStatus(task.status) in c.TERMINAL_TASK and task.status != c.TaskStatus.REVIEW_READY.value) or task.cancel_requested:
                raise Conflict("Task is terminal")
            now = self.clock()
            result = await db.execute(update(m.Task).where(
                m.Task.id == task_id, m.Task.tenant_id == tenant, m.Task.version == task.version,
                m.Task.fencing_token == task.fencing_token,
                or_(m.Task.lease_owner.is_(None), m.Task.lease_until <= now),
                m.Task.cancel_requested.is_(False),
            ).values(lease_owner=owner, lease_until=now + lease_seconds,
                     fencing_token=m.Task.fencing_token + 1, version=m.Task.version + 1)
              .execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise Conflict("Lease already owned or changed")
            return Lease(task_id, tenant, owner, task.version + 1, task.fencing_token + 1)

    async def _write(self, db, lease, *, event_type=None, actor="platform", data=None,
                     changes=None, dst=None, provenance="OBSERVED"):
        task = await self._task(db, lease.task_id, lease.tenant_id)
        if task.cancel_requested or (c.TaskStatus(task.status) in c.TERMINAL_TASK
                and (dst is None or not c.can_move(c.TaskStatus(task.status), c.TaskStatus(dst)))):
            raise Conflict("Task is terminal")
        changes = dict(changes or {})
        if dst is not None:
            dst = c.TaskStatus(dst)
            if not c.can_move(c.TaskStatus(task.status), dst):
                raise Conflict(f"Invalid transition: {task.status} -> {dst.value}")
            changes["status"] = dst.value
        changes["version"] = m.Task.version + 1
        if event_type:
            changes["event_seq"] = m.Task.event_seq + 1
        result = await db.execute(update(m.Task).where(
            m.Task.id == lease.task_id, m.Task.tenant_id == lease.tenant_id,
            m.Task.version == lease.version, m.Task.fencing_token == lease.fencing_token,
            m.Task.lease_owner == lease.owner, m.Task.lease_until > self.clock(),
            m.Task.cancel_requested.is_(False), m.Task.status == task.status,
        ).values(**changes).execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise Conflict("Stale version, fencing token or expired lease; reacquire authority")
        if event_type:
            event = c.Event(task_id=task.id, seq=task.event_seq + 1, type=event_type, provenance=provenance,
                            actor=actor, data={**(data or {}), "fencing_token": lease.fencing_token})
            db.add(m.Event(task_id=task.id, seq=event.seq, payload=event.model_dump(mode="json")))
        return replace(lease, version=lease.version + 1)

    async def transition(self, lease, dst, *, event_type, data=None):
        async with self.sessions() as db, db.begin():
            return await self._write(db, lease, dst=dst, event_type=event_type, data=data)

    async def renew(self, lease, *, lease_seconds=30):
        if not 1 <= lease_seconds <= 600:
            raise ValueError("invalid lease duration")
        async with self.sessions() as db, db.begin():
            return await self._write(db, lease, changes={"lease_until": self.clock() + lease_seconds})

    async def release(self, lease):
        async with self.sessions() as db, db.begin():
            return await self._write(db, lease, changes={"lease_owner": None, "lease_until": None})

    async def cancel(self, task_id, tenant, *, expected_version=None):
        async with self.sessions() as db, db.begin():
            task = await self._task(db, task_id, tenant)
            if task.status == c.TaskStatus.CANCELLED.value:
                return task_json(task)
            if expected_version is not None and expected_version != task.version:
                raise Conflict("Version changed; reload the task")
            if not c.can_move(c.TaskStatus(task.status), c.TaskStatus.CANCELLED):
                raise Conflict("Task is already terminal")
            result = await db.execute(update(m.Task).where(
                m.Task.id == task_id, m.Task.tenant_id == tenant, m.Task.version == task.version,
                m.Task.fencing_token == task.fencing_token, m.Task.status == task.status,
            ).values(status=c.TaskStatus.CANCELLED.value, cancel_requested=True,
                     version=m.Task.version + 1, fencing_token=m.Task.fencing_token + 1,
                     lease_owner=None, lease_until=None, event_seq=m.Task.event_seq + 1)
              .execution_options(synchronize_session=False))
            if result.rowcount != 1:
                raise Conflict("Task changed during cancellation; reload it")
            event = c.Event(task_id=task_id, seq=task.event_seq + 1, type="task.cancelled",
                            actor="reviewer", data={"fencing_token": task.fencing_token + 1})
            db.add(m.Event(task_id=task_id, seq=event.seq, payload=event.model_dump(mode="json")))
            await db.refresh(task)
            return task_json(task)

    async def save_candidate(self, lease, candidate: c.Candidate, *, emit=True, extra: dict | None = None):
        if candidate.task_id != lease.task_id:
            raise ValueError("candidate task mismatch")
        if not candidate.candidate_id or len(candidate.candidate_id) > 36:
            raise ValueError("invalid candidate ID")
        actual = "sha256:" + hashlib.sha256(candidate.diff.encode()).hexdigest()
        if candidate.diff_digest != actual:
            raise ValueError("candidate diff digest mismatch")
        async with self.sessions() as db, db.begin():
            if candidate.parent:
                parent = await db.get(m.Candidate, candidate.parent)
                if parent is None or parent.task_id != lease.task_id:
                    raise ValueError("candidate parent mismatch")
            if await db.get(m.Candidate, candidate.candidate_id):
                raise Conflict("Candidate already exists; frozen records cannot be overwritten")
            next_lease = await self._write(db, lease, event_type="candidate.created" if emit else None,
                                            data={"candidate_id": candidate.candidate_id})
            db.add(m.Candidate(id=candidate.candidate_id, task_id=lease.task_id,
                               payload={**candidate.model_dump(mode="json"), **(extra or {})}, diff=candidate.diff))
            return next_lease

    async def save_attempt(self, lease, candidate_id, payload):
        async with self.sessions() as db, db.begin():
            candidate = await db.get(m.Candidate, candidate_id)
            if candidate is None or candidate.task_id != lease.task_id:
                raise NotFound("Candidate not found")
            next_lease = await self._write(db, lease, event_type="patch.applied",
                                            data={"candidate_id": candidate_id, "attempt": True})
            db.add(m.Attempt(id=str(uuid.uuid4()), task_id=lease.task_id, candidate_id=candidate_id,
                             fencing_token=lease.fencing_token, payload=payload))
            return next_lease

    async def save_receipt(self, lease, receipt: c.CodeReceipt | c.MLReceipt | c.VerifiedChangeReceipt, *, emit=True):
        if receipt.task_id != lease.task_id:
            raise ValueError("receipt task mismatch")
        async with self.sessions() as db, db.begin():
            task = await self._task(db, lease.task_id, lease.tenant_id)
            candidate = await db.get(m.Candidate, receipt.candidate_id)
            if candidate is None or candidate.task_id != lease.task_id:
                raise NotFound("Candidate not found")
            if receipt.contract_digest != task.ecc["contract_digest"] or receipt.diff_digest != candidate.payload["diff_digest"]:
                raise ValueError("receipt provenance mismatch")
            kind = getattr(receipt, "kind", "combined")
            payload = receipt.model_dump(mode="json")
            if kind != "combined":
                expected = c.canonical_digest({k: v for k, v in payload.items() if k != "receipt_digest"})
                if receipt.receipt_digest != expected:
                    raise ValueError("receipt digest mismatch")
            else:
                parts = (await db.execute(select(m.Receipt).where(
                    m.Receipt.candidate_id == receipt.candidate_id))).scalars()
                parts = {part.kind: part.payload for part in parts}
                if "code" not in parts:
                    raise ValueError("combined receipt requires stored code receipt")
                code = c.CodeReceipt.model_validate(parts["code"])
                ml = c.MLReceipt.model_validate(parts["ml"]) if "ml" in parts else None
                expected = c.VerifiedChangeReceipt.combine(code, ml, bool(task.ecc["success"]["ml_invariants"]))
                if payload != expected.model_copy(update={"created_at": receipt.created_at}).model_dump(mode="json"):
                    raise ValueError("combined receipt contradicts stored oracle receipts")
            present = (await db.execute(select(m.Receipt).where(m.Receipt.candidate_id == receipt.candidate_id,
                                                                m.Receipt.kind == kind))).scalar_one_or_none()
            if present:
                raise Conflict("Receipt already exists and is immutable")
            rejected = receipt.decision == c.Decision.REJECTED
            event_type = (f"{kind}_oracle.rejected" if rejected else f"{kind}_oracle.passed") if kind in ("code", "ml") else (
                "candidate.verified" if receipt.decision == c.Decision.VERIFIED else "candidate.rejected")
            # Non-passing outcomes never use a 'passed' event.
            if receipt.decision not in (c.Decision.REJECTED, c.Decision.VERIFIED) or not emit:
                event_type = None  # Frozen event schema has no receipt-recorded event.
            next_lease = await self._write(db, lease, event_type=event_type, actor="platform",
                                            data={"candidate_id": receipt.candidate_id, "decision": receipt.decision.value})
            db.add(m.Receipt(id=str(uuid.uuid4()), task_id=lease.task_id, candidate_id=receipt.candidate_id,
                             kind=kind, payload=payload))
            return next_lease

    async def append_event(self, lease, event_type, *, actor="platform", data=None, provenance="OBSERVED", dst=None):
        async with self.sessions() as db, db.begin():
            return await self._write(db, lease, event_type=event_type, actor=actor, data=data,
                                     provenance=provenance, dst=dst)

    async def set_fields(self, lease, *, dst=None, event_type=None, data=None, **changes):
        async with self.sessions() as db, db.begin():
            return await self._write(db, lease, dst=dst, event_type=event_type, data=data, changes=changes)

    async def row(self, task_id, tenant):
        async with self.sessions() as db:
            t = await self._task(db, task_id, tenant)
            return {"ecc": t.ecc, "spec": t.spec or {}, "status": t.status, "report": t.report,
                    "cancel_requested": t.cancel_requested}

    async def report(self, task_id, tenant):
        async with self.sessions() as db:
            return (await self._task(db, task_id, tenant)).report

    async def reap_expired(self, grace_s: float = 30.0) -> int:
        """A worker died: tasks whose lease expired mid-run end FAILED, never half-verified."""
        now = self.clock()
        live = [s.value for s in c.TaskStatus if s not in c.TERMINAL_TASK and s is not c.TaskStatus.CREATED]
        n = 0
        async with self.sessions() as db, db.begin():
            # Expired leases, and live tasks nobody holds (a worker released without a final state).
            rows = (await db.execute(select(m.Task).where(m.Task.status.in_(live), or_(
                m.Task.lease_owner.is_(None), m.Task.lease_until < now - grace_s)))).scalars().all()
            for t in rows:
                res = await db.execute(update(m.Task).where(m.Task.id == t.id, m.Task.version == t.version)
                                       .values(status=c.TaskStatus.FAILED.value, version=m.Task.version + 1,
                                               fencing_token=m.Task.fencing_token + 1, lease_owner=None,
                                               lease_until=None, event_seq=m.Task.event_seq + 1,
                                               spec={**(t.spec or {}), "error": "worker stopped (lease expired)"})
                                       .execution_options(synchronize_session=False))
                if res.rowcount == 1:
                    ev = c.Event(task_id=t.id, seq=t.event_seq + 1, type="task.failed", actor="platform",
                                 data={"reason": "E_INTERRUPTED"})
                    db.add(m.Event(task_id=t.id, seq=ev.seq, payload=ev.model_dump(mode="json")))
                    n += 1
        return n

    async def heartbeat(self, owner: str, info: dict | None = None) -> None:
        async with self.sessions() as db, db.begin():
            row = await db.get(m.RepairWorker, owner)
            if row is None:
                db.add(m.RepairWorker(id=owner, beat=self.clock(), info=info or {}))
            else:
                row.beat, row.info = self.clock(), info or row.info

    async def latest_worker(self) -> dict | None:
        async with self.sessions() as db:
            row = (await db.execute(select(m.RepairWorker).order_by(m.RepairWorker.beat.desc()).limit(1))).scalar_one_or_none()
            return {"beat": row.beat, **(row.info or {})} if row else None

    async def latest_beat(self):
        from sqlalchemy import func
        async with self.sessions() as db:
            return (await db.execute(select(func.max(m.RepairWorker.beat)))).scalar()

    async def since(self, ts: float):
        async with self.sessions() as db:
            return (await db.execute(select(m.Task.tenant_id, m.Task.status, m.Task.report)
                                     .where(m.Task.created_at >= ts))).all()

    async def active_specs(self) -> list[dict]:
        async with self.sessions() as db:
            return [sp or {} for sp in (await db.execute(select(m.Task.spec).where(
                m.Task.status.notin_([s.value for s in c.TERMINAL_TASK])))).scalars().all()]

    async def run_stub(self, task_id, tenant, *, owner="repair-stub"):
        lease = await self.claim(task_id, tenant, owner)
        try:
            task = await self.get(task_id, tenant)
            if task["status"] == c.TaskStatus.CREATED.value:
                lease = await self.transition(lease, c.TaskStatus.SNAPSHOTTING,
                                               event_type="repo.snapshotted", data={"stub": True, "executed": False})
            task = await self.get(task_id, tenant)
            if task["status"] == c.TaskStatus.SNAPSHOTTING.value:
                lease = await self.transition(lease, c.TaskStatus.MAPPING,
                                               event_type="repo.mapped", data={"stub": True, "executed": False})
        finally:
            try:
                await self.release(lease)
            except Conflict:
                pass  # Cancel or a newer worker already revoked this authority.
        return await self.get(task_id, tenant)

    async def work_once(self, *, owner):
        async with self.sessions() as db:
            rows = (await db.execute(select(m.Task.id, m.Task.tenant_id).where(
                m.Task.status.in_([c.TaskStatus.CREATED.value, c.TaskStatus.SNAPSHOTTING.value]),
                or_(m.Task.lease_owner.is_(None), m.Task.lease_until <= self.clock()),
                m.Task.cancel_requested.is_(False)).order_by(m.Task.created_at).limit(10))).all()
        for task_id, tenant in rows:
            try:
                from app.repair.engine import run_task
                await run_task(self, task_id, tenant, owner=owner)
                return True
            except Conflict:
                continue
        return False


default_service = RepairService()


async def worker_loop(owner="repair-worker", poll_seconds=1.0):
    from app.repair import ops
    last_clean = 0.0
    while True:
        await default_service.heartbeat(owner, {**ops.provider_config(), "tavily": bool(ops.settings.TAVILY_API_KEY)})
        await default_service.reap_expired()
        if time.time() - last_clean > 600:
            last_clean = time.time()
            try:
                await ops.cleanup(default_service)
            except Exception:  # noqa: BLE001 - cleanup must never stop the worker
                pass
        if not await default_service.work_once(owner=owner):
            await asyncio.sleep(poll_seconds)


if __name__ == "__main__":
    from app.database import init_db

    async def main():
        await init_db()
        await worker_loop(owner=f"repair-worker:{uuid.uuid4().hex[:12]}")

    asyncio.run(main())
