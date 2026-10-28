"""Investigation worker: the execution plane, run as its own service.

    python -m app.autopilot.worker

The API only validates and queues (AUTOPILOT_EXECUTION=worker). This process:

1. Claims a waiting investigation with a time-bound **lease**, by compare-and-swap on
   (id, lease_owner, lease_until), so two workers can never run the same investigation.
2. Runs it (to the end, or one stage for step mode) through the same engine the API
   uses in dev, renewing the lease while it works. Experiments run in killable child
   processes; verification goes to the separate verifier service.
3. Fails safe on crashes: a started investigation whose lease expired was abandoned by
   a dead worker. It is marked FAILED / E_INTERRUPTED, never resumed half-way and never
   reported as a success. One that never started is simply claimed again.
4. Writes a heartbeat with provider status, so the API (which holds no provider keys)
   can show what is live and whether a worker is online.

Delivery is at-least-once with idempotent claiming; there is exactly one accepted
terminal result per investigation because only the lease holder may write state.
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select, update

from app.autopilot import service
from app.autopilot.models import AutopilotInvestigation, AutopilotWorker
from app.autopilot.schemas import TERMINAL, Status, utcnow
from app.config import settings
from app.core.logging import get_logger, setup_logging
from app.database import AsyncSessionLocal, init_db

_log = get_logger("autopilot.worker")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
_TERMINAL = [s.value for s in TERMINAL]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


ALIVE_FILE = "/tmp/autopilot-worker-alive"      # container healthcheck reads its mtime


async def heartbeat(active: int) -> None:
    try:
        with open(ALIVE_FILE, "w", encoding="utf-8") as f:
            f.write(WORKER_ID)
    except OSError:
        pass
    async with AsyncSessionLocal() as db:
        row = await db.get(AutopilotWorker, WORKER_ID)
        info = {"providers": service.providers(), "active": active, "pid": os.getpid(),
                "concurrency": settings.AUTOPILOT_WORKER_CONCURRENCY,
                "verifier": "service" if settings.AUTOPILOT_VERIFIER_URL else "local-process"}
        if row is None:
            db.add(AutopilotWorker(worker_id=WORKER_ID, last_seen=_now(), info=info))
        else:
            row.last_seen, row.info = _now(), info
        await db.commit()


async def fail_abandoned() -> int:
    """Started investigations whose lease expired: their worker died mid-run."""
    n = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.status.not_in(_TERMINAL),
            AutopilotInvestigation.status != Status.CREATED.value,
            AutopilotInvestigation.lease_owner.is_not(None),
            AutopilotInvestigation.lease_until < _now()))).scalars().all()
        for r in rows:
            if not r.auto_run:
                # Step mode: the stage that was running is lost, but the investigation stays
                # open for its owner; release the lease and drop the pending request.
                r.lease_owner, r.lease_until, r.steps_requested = None, None, 0
                continue
            st = dict(r.state)
            st["status"] = Status.FAILED.value
            st["decision"] = {"decision": "FAILED", "reason_codes": ["E_INTERRUPTED"],
                              "summary": "The worker running this investigation stopped; no result is reported.",
                              "next_action": "Start a new investigation.", "incumbent_changed": False,
                              "at": utcnow()}
            r.state, r.status, r.decision = st, st["status"], "FAILED"
            r.lease_owner, r.lease_until = None, None
            n += 1
        await db.commit()
    if n:
        _log.warning("autopilot_abandoned_failed_safe", count=n)
    return n


async def claim() -> tuple[str, str, bool] | None:
    """Claim the oldest waiting investigation. Returns (run_id, tenant, single_step)."""
    now = _now()
    async with AsyncSessionLocal() as db:
        candidates = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.status.not_in(_TERMINAL),
            or_(AutopilotInvestigation.lease_owner.is_(None), AutopilotInvestigation.lease_until < now),
            or_(AutopilotInvestigation.auto_run.is_(True), AutopilotInvestigation.steps_requested > 0))
            .order_by(AutopilotInvestigation.created_at).limit(5))).scalars().all()
        for c in candidates:
            if c.auto_run and c.status != Status.CREATED.value:
                continue                      # started elsewhere; fail_abandoned() decides
            prev_owner, prev_until = c.lease_owner, c.lease_until
            res = await db.execute(update(AutopilotInvestigation).where(
                AutopilotInvestigation.id == c.id,
                AutopilotInvestigation.lease_owner.is_(None) if prev_owner is None
                else AutopilotInvestigation.lease_owner == prev_owner,
                AutopilotInvestigation.lease_until.is_(None) if prev_until is None
                else AutopilotInvestigation.lease_until == prev_until,
            ).values(lease_owner=WORKER_ID, lease_until=now + timedelta(seconds=settings.AUTOPILOT_LEASE_S)))
            await db.commit()
            if res.rowcount == 1:             # compare-and-swap won
                return c.id, c.tenant_id, not c.auto_run
    return None


async def _renew(run_id: str, stop: asyncio.Event) -> None:
    every = max(5, settings.AUTOPILOT_LEASE_S // 3)
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            async with AsyncSessionLocal() as db:
                await db.execute(update(AutopilotInvestigation).where(
                    AutopilotInvestigation.id == run_id, AutopilotInvestigation.lease_owner == WORKER_ID)
                    .values(lease_until=_now() + timedelta(seconds=settings.AUTOPILOT_LEASE_S)))
                await db.commit()


async def execute(run_id: str, tenant: str, single_step: bool) -> None:
    stop = asyncio.Event()
    renew = asyncio.create_task(_renew(run_id, stop))
    try:
        _log.info("autopilot_claimed", run_id=run_id, single_step=single_step)
        await service.drive(run_id, tenant, single_step=single_step, wait=True)
    except Exception as e:  # noqa: BLE001 — the engine already fails safe; never crash the worker
        _log.error("autopilot_execute_failed", run_id=run_id, error=str(e)[:300])
    finally:
        stop.set()
        await renew
        async with AsyncSessionLocal() as db:
            row = await db.get(AutopilotInvestigation, run_id)
            if row is not None and row.lease_owner == WORKER_ID:
                row.lease_owner, row.lease_until = None, None
                if single_step:
                    row.steps_requested = max(0, (row.steps_requested or 0) - 1)
                await db.commit()


async def run_forever(poll_s: float = 1.0, once: bool = False) -> None:
    running: set[asyncio.Task] = set()
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stopping.set)
        except (NotImplementedError, RuntimeError):     # Windows dev shells
            pass
    last_beat = 0.0
    while not stopping.is_set():
        if loop.time() - last_beat > 10:
            await heartbeat(len(running))
            await fail_abandoned()
            last_beat = loop.time()
        while len(running) < settings.AUTOPILOT_WORKER_CONCURRENCY:
            job = await claim()
            if job is None:
                break
            t = asyncio.create_task(execute(*job))
            running.add(t)
            t.add_done_callback(running.discard)
        if once:
            break
        try:
            await asyncio.wait_for(stopping.wait(), timeout=poll_s)
        except asyncio.TimeoutError:
            pass
    if running:                                   # graceful: finish what we hold
        await asyncio.gather(*running, return_exceptions=True)


async def _main() -> None:
    setup_logging()
    await init_db()
    _log.info("autopilot_worker_started", worker_id=WORKER_ID,
              concurrency=settings.AUTOPILOT_WORKER_CONCURRENCY, lease_s=settings.AUTOPILOT_LEASE_S)
    await run_forever()


if __name__ == "__main__":
    asyncio.run(_main())
