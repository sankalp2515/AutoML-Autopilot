"""Durable execution of a repair task in the worker (BE-01).

The worker claims a fenced lease, walks the task through its states while the agent runs in
a thread, persists every agent event as it happens, and stores candidates, receipts and the
report at the end. A lease renewer also watches for cancellation. Any failure ends the task
FAILED with a reason; a crash is handled by `RepairService.reap_expired`. A task is never
left half-verified.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time

from app.repair import contracts as c
from app.repair.agent.llm import client_from_settings
from app.repair.agent.loop import Cancelled, RepairAgent
from app.repair.agent.oracles import make_oracles
from app.repair.oracle.grading import GradeRef
from app.repair.sandbox.factory import make_runner
from app.repair.service import Conflict

log = logging.getLogger("repair.engine")
S = c.TaskStatus
PATH = [S.SNAPSHOTTING, S.MAPPING, S.INVESTIGATING, S.DIAGNOSING, S.PATCHING, S.CODE_VERIFYING,
        S.ML_VERIFYING, S.SELECTING]
EVENT_STAGE = {"hypothesis.proposed": S.DIAGNOSING, "candidate.created": S.PATCHING,
               "code_oracle.passed": S.CODE_VERIFYING, "code_oracle.rejected": S.CODE_VERIFYING,
               "ml_oracle.passed": S.ML_VERIFYING, "ml_oracle.rejected": S.ML_VERIFYING,
               "candidate.frozen": S.SELECTING}


class _Lease:
    def __init__(self, svc, lease):
        self.svc, self.lease, self.status = svc, lease, None
        self.lock = asyncio.Lock()
        self.lost = threading.Event()

    async def write(self, fn):
        async with self.lock:
            if self.lost.is_set():
                raise Conflict("lease lost")
            try:
                self.lease = await fn(self.lease)
            except Conflict:
                self.lost.set()
                raise

    async def advance(self, target: c.TaskStatus) -> None:
        """Walk forward along PATH to `target` using only allowed transitions."""
        while self.status != target:
            idx = PATH.index(self.status) if self.status in PATH else -1
            nxt = next((s for s in PATH[idx + 1:PATH.index(target) + 1] if c.can_move(self.status, s)), None)
            if nxt is None:
                return
            await self.write(lambda le, n=nxt: self.svc.set_fields(le, dst=n))
            self.status = nxt


async def run_task(svc, task_id: str, tenant: str, *, owner: str) -> None:
    row = await svc.row(task_id, tenant)
    lease = await svc.claim(task_id, tenant, owner, lease_seconds=90)
    h = _Lease(svc, lease)
    h.status = S(row["status"])
    loop = asyncio.get_running_loop()
    spec, ecc = row["spec"], c.ECC.model_validate(row["ecc"])
    stop = threading.Event()

    async def renewer():
        while not stop.is_set():
            await asyncio.sleep(20)
            try:
                await h.write(lambda le: svc.renew(le, lease_seconds=90))
                if (await svc.row(task_id, tenant))["cancel_requested"]:
                    h.lost.set()
            except Exception:  # noqa: BLE001 - lost authority or DB hiccup: stop the agent
                h.lost.set()

    persist_lock = asyncio.Lock()
    saved_candidates=set()
    saved_receipts=set()

    async def checkpoint(run,final_ml=None,verdict=None):
        async with persist_lock:
            extra={"steps":[s.model_dump(mode="json") for s in run.steps],"stop_reason":run.stop_reason,
                   "eligible":run.eligible,"diff_lines":run.diff_lines,"tokens":run.tokens,
                   "skipped_files":run.skipped_files,
                   "code_receipt":run.code_receipt.model_dump(mode="json") if run.code_receipt else None,
                   "screen_receipt":run.screen_receipt.model_dump(mode="json") if run.screen_receipt else None}
            cid=run.candidate.candidate_id
            if cid not in saved_candidates:
                await h.write(lambda le:svc.save_candidate(le,run.candidate,emit=False,extra=extra))
                saved_candidates.add(cid)
            else:
                await h.write(lambda le:svc.checkpoint_candidate(le,run.candidate,extra))
            for kind,receipt in [("code",run.code_receipt),("ml_screen",run.screen_receipt),("ml",final_ml),("combined",verdict)]:
                if receipt is not None and (cid,kind) not in saved_receipts:
                    await h.write(lambda le,r=receipt,k=kind:svc.save_receipt(le,r,emit=False,screen=k=="ml_screen"))
                    saved_receipts.add((cid,kind))

    def on_checkpoint(run,final_ml=None,verdict=None):
        asyncio.run_coroutine_threadsafe(checkpoint(run,final_ml,verdict),loop).result(timeout=60)

    async def persist(ev: dict) -> None:
        typ = ev.get("type")
        if typ in ("task.created",):
            return
        async with persist_lock:     # candidates run in parallel; stage moves must be ordered
            stage = EVENT_STAGE.get(typ)
            if stage and PATH.index(stage) > (PATH.index(h.status) if h.status in PATH else -1):
                await h.advance(stage)
            await _append(typ, ev)

    async def _append(typ: str, ev: dict) -> None:
        data = {k: v for k, v in ev.items() if k not in ("seq", "type", "actor", "at", "provenance")}
        if typ in {"candidate.progress", "usage.updated"}:
            # Keep the frozen Event enum: telemetry is a versioned diagnostic payload.
            data.update(kind="telemetry", telemetry_type=typ, telemetry_version=1)
            typ = "diagnostic.executed"
        await h.write(lambda le: svc.append_event(le, typ, actor=ev.get("actor", "platform"), data=data,
                                                  provenance=ev.get("provenance", "OBSERVED")))

    def on_event(ev: dict) -> None:
        if h.lost.is_set():
            return
        try:
            asyncio.run_coroutine_threadsafe(persist(ev), loop).result(timeout=60)
        except Exception as e:  # noqa: BLE001
            log.warning("event persist failed task=%s type=%s err=%s", task_id, ev.get("type"), type(e).__name__)

    renew_task = asyncio.create_task(renewer())
    try:
        if h.status is S.CREATED:
            await h.write(lambda le: svc.set_fields(le, dst=S.SNAPSHOTTING, event_type="repo.snapshotted",
                                                     data={"snapshot": spec.get("snapshot_id"), "sha": ecc.base_commit_sha}))
            h.status = S.SNAPSHOTTING
        await h.write(lambda le: svc.set_fields(le, dst=S.MAPPING, event_type="repo.mapped",
                                                 data={"runner": "sandbox"}))
        h.status = S.MAPPING
        await h.write(lambda le: svc.set_fields(le, dst=S.INVESTIGATING))
        h.status = S.INVESTIGATING

        llm = client_from_settings(ecc.limits.model_tokens)
        if llm is None:
            raise RuntimeError("no model provider configured")
        runner = make_runner()
        from app.repair.sandbox.pinned import pinned
        runner=pinned(runner,ecc)
        oracles = make_oracles(runner, GradeRef(task_id, row["ecc"], spec["snapshot_dir"], spec.get("sealed_ref")))
        contract = c.RepoContract.from_yaml(open(f"{spec['snapshot_dir']}/autopilot.yaml", encoding="utf-8").read())
        deadline=time.monotonic()+ecc.limits.wall_seconds
        def should_stop():
            if time.monotonic()>deadline:raise TimeoutError("task wall-clock allowance exhausted")
            return h.lost.is_set()
        agent = RepairAgent(runner, llm, oracles, base_dir=spec["snapshot_dir"], contract=contract, ecc=ecc,
                            mode=spec.get("mode", "full"), on_event=on_event, should_stop=should_stop,
                            on_checkpoint=on_checkpoint)
        log.info("repair start task=%s mode=%s runner=%s", task_id, agent.report.mode, runner.name)
        report = await asyncio.to_thread(agent.run)
        log.info(json.dumps({"event": "repair.done", "task_id": task_id, "tenant": tenant,
                             "decision": report.decision.value, "seconds": report.seconds,
                             "model_calls": len(report.calls), "cost_usd": report.usage.get("total_est_cost_usd"),
                             "candidates": len(report.candidates), "runner": report.runner, "image": report.image}))

        for run in report.candidates:
            await checkpoint(run,report.final_ml_receipt if run.candidate.candidate_id==report.finalist else None,
                             report.verdict if run.candidate.candidate_id==report.finalist else None)

        await h.advance(S.SELECTING)
        dst = {c.Decision.VERIFIED: S.REVIEW_READY, c.Decision.REJECTED: S.REJECTED}.get(report.decision, S.INCONCLUSIVE)
        if not c.can_move(h.status, dst):        # e.g. stopped before any candidate reached a checker
            await h.advance(S.CODE_VERIFYING)
            await h.advance(S.SELECTING)
        await h.write(lambda le: svc.set_fields(le, dst=dst, report=report.model_dump(mode="json")))
        h.status = dst
    except Cancelled:
        log.info("repair cancelled task=%s", task_id)
    except asyncio.CancelledError:
        h.lost.set()
        raise
    except Conflict:
        log.info("repair lost authority task=%s", task_id)
    except Exception as e:  # noqa: BLE001 - every failure is recorded, never hidden
        log.exception("repair failed task=%s", task_id)
        msg = f"{type(e).__name__}: {str(e)[:300]}"
        h.lost.clear()                      # record the failure even if a stage write was refused
        try:
            await h.write(lambda le: svc.set_fields(le, dst=S.FAILED, event_type="task.failed",
                                                     data={"reason": msg}, spec={**spec, "error": msg}))
        except Exception:  # noqa: BLE001 - reap_expired will mark the orphan FAILED
            log.warning("could not record failure task=%s", task_id)
    finally:
        stop.set()
        renew_task.cancel()
        try:
            await h.write(lambda le: svc.release(le))
        except Exception:  # noqa: BLE001 - cancel or a newer worker already revoked authority
            pass
