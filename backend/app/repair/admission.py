"""Atomic queue admission, serialized across API replicas by a database row.

This caps estimated commitments, not a provider's invoice. Unknown/crashed usage
retains its reservation for the rolling window rather than becoming zero spend.
"""
import hashlib
import json
import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path
from sqlalchemy import select, update, func
from app.repair import models as m


class AdmissionDenied(PermissionError):
    pass


class AdmissionLimited(AdmissionDenied):
    def __init__(self,message,retry_after=60):
        super().__init__(message)
        self.retry_after=max(1,int(retry_after))


async def lock(db):
    dialect=db.bind.dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert
    else:
        raise AdmissionDenied("unsupported admission database")
    await db.execute(insert(m.AdmissionLock).values(id="global",version=0).on_conflict_do_nothing())
    await db.execute(update(m.AdmissionLock).where(m.AdmissionLock.id=="global")
                     .values(version=m.AdmissionLock.version+1))


@asynccontextmanager
async def request_lock(tenant,key):
    """Serialize repeated HTTP intents before staging mutates Git metadata.

    The shared artifact volume is the deployment's filesystem boundary; database
    uniqueness/reservation locking remains authoritative across API processes.
    """
    if not key:
        yield
        return
    from app.config import settings
    root=Path(settings.DATA_DIR)/"repair/request-locks";root.mkdir(parents=True,exist_ok=True)
    path=root/hashlib.sha256((tenant+"\0"+key).encode()).hexdigest()
    with path.open("a+b") as stream:
        if path.stat().st_size==0:stream.write(b"0");stream.flush()
        while True:
            try:
                stream.seek(0)
                if os.name=="nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:await asyncio.sleep(.05)
            except OSError as exc:
                if os.name!="nt" or exc.errno not in {13,11}:raise
                await asyncio.sleep(.05)
        try:yield
        finally:
            stream.seek(0)
            if os.name=="nt":msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def digest_request(*, contract, sha, snapshot, target, spec, limits):
    # Exclude time-dependent admission estimates; bind all task-affecting inputs.
    stable={k:v for k,v in (spec or {}).items() if k != "budget"}
    value=[contract.digest(),sha,snapshot,target,stable,limits.model_dump(mode="json")]
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":")).encode()).hexdigest()


async def reserve(db, *, task_id, tenant, now, policy):
    maintenance=await db.get(m.AdmissionLock,"maintenance")
    if maintenance is not None and maintenance.version:
        raise AdmissionDenied("Live-run admission is paused for maintenance. Existing evidence remains available.")
    rows=(await db.execute(select(m.Reservation).where(m.Reservation.created_at>=now-86400))).scalars().all()
    cap=policy.get("total_cap_usd",0)
    if cap:
        total=(await db.execute(select(func.coalesce(func.sum(m.Reservation.dollars),0)))).scalar_one()
        if total+policy["reserve_usd"]>cap+1e-9:
            raise AdmissionDenied("The reserved hackathon inference allowance is committed. Recorded runs and their receipts remain available. Contact the project owner through the submission testing instructions for live access.")
    recent=[r.created_at for r in rows if r.tenant_id==tenant]
    cooldown=policy.get("cooldown_s",0)
    if cooldown and recent and now-max(recent)<cooldown:
        wait=cooldown-(now-max(recent))
        raise AdmissionLimited(f"Please wait {int(wait)+1} seconds before starting another repair. This protects judge access; your existing run and evidence remain available.",int(wait)+1)
    maximum=policy.get("max_active",0)
    if maximum:
        from app.repair.contracts import TERMINAL_TASK
        active=(await db.execute(select(func.count()).select_from(m.Task).where(
            m.Task.id!=task_id,m.Task.status.notin_([state.value for state in TERMINAL_TASK])))).scalar_one()
        if active>=maximum:
            raise AdmissionLimited("The live repair slots are busy. Let an existing repair finish, then retry. Recorded evidence remains available.",30)
    if sum(r.tenant_id==tenant for r in rows)>=policy["tenant_tasks"]:
        raise AdmissionDenied("Daily live-run task limit reached.")
    if sum(r.dollars for r in rows)+policy["reserve_usd"]>policy["cap_usd"]+1e-9:
        raise AdmissionDenied("Today's live-run budget is used up. Watch a recorded run instead.")
    if sum(r.tokens for r in rows)+policy["reserve_tokens"]>policy["cap_tokens"]:
        raise AdmissionDenied("Today's live-run token allowance is used up.")
    db.add(m.Reservation(task_id=task_id,tenant_id=tenant,created_at=now,
                         dollars=policy["reserve_usd"],tokens=policy["reserve_tokens"]))


async def settle(db, task_id, report):
    calls=report.get("calls",[])
    # No calls can mean a missing ledger. Keep the original ceiling in that case.
    if not calls or not all(c.get("ok") and c.get("est_cost_usd") is not None for c in calls):
        return
    await lock(db)
    await db.execute(update(m.Reservation).where(m.Reservation.task_id==task_id).values(
        dollars=sum(c["est_cost_usd"] for c in calls),
        tokens=sum(c.get("prompt_tokens",0)+c.get("completion_tokens",0) for c in calls)))
