"""Application service: persistence, background execution, receipts.

The engine is synchronous (CPU-bound sklearn work); it runs in a worker thread
and persists every event back on the event loop. A process-wide registry keeps
at most one active driver per investigation, so a double-clicked "Next" or a
retried POST cannot run the same stage twice.
"""
from __future__ import annotations

import asyncio
import copy
import json
from functools import lru_cache
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError

from app.autopilot import engine, guard, research, scenarios
from app.autopilot.models import AutopilotEvent, AutopilotInvestigation
from app.autopilot.planner import NemotronPlanner
from app.autopilot.schemas import TERMINAL, CreateInvestigation, Status, utcnow
from app.config import settings
from app.core.logging import get_logger
from app.database import AsyncSessionLocal

_log = get_logger("autopilot")
_active: dict[str, asyncio.Task] = {}
_slots: dict[int, asyncio.Semaphore] = {}
MAX_CONCURRENT_DRIVERS = 2          # CPU-bound investigations run at most this many at once
MAX_ACTIVE_PER_TENANT = 5           # unfinished investigations a tenant may hold


def _slot() -> asyncio.Semaphore:
    """One semaphore per event loop (a loop-bound primitive must not leak across loops)."""
    loop = asyncio.get_running_loop()
    return _slots.setdefault(id(loop), asyncio.Semaphore(MAX_CONCURRENT_DRIVERS))


async def active_count(tenant: str) -> int:
    from sqlalchemy import func as sa_func
    async with AsyncSessionLocal() as db:
        return int((await db.execute(select(sa_func.count()).select_from(AutopilotInvestigation).where(
            AutopilotInvestigation.tenant_id == tenant,
            AutopilotInvestigation.status.not_in([s.value for s in TERMINAL])))).scalar_one())

async def expire_idle(tenant: str) -> int:
    """Cancel step-mode runs nobody has advanced for AUTOPILOT_IDLE_EXPIRY_MIN minutes,
    so an abandoned tab cannot hold a slot forever. Auto runs are left alone."""
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=settings.AUTOPILOT_IDLE_EXPIRY_MIN)
    n = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.tenant_id == tenant,
            AutopilotInvestigation.status.not_in([s.value for s in TERMINAL])))).scalars().all()
        for r in rows:
            st = dict(r.state)
            seen = r.updated_at or r.created_at
            if seen is not None and seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            if st.get("config", {}).get("auto_run", True) or r.id in _active or seen is None or seen > cutoff:
                continue
            st["status"] = Status.CANCELLED.value
            st["decision"] = {"decision": "CANCELLED", "reason_codes": ["E_IDLE_EXPIRED"],
                              "summary": "Step-mode investigation closed after "
                                         f"{settings.AUTOPILOT_IDLE_EXPIRY_MIN} idle minutes; nothing was changed.",
                              "next_action": "Start a new investigation.", "incumbent_changed": False,
                              "at": utcnow()}
            r.state, r.status, r.decision = st, st["status"], "CANCELLED"
            n += 1
        await db.commit()
    return n


LIMITATIONS = [
    "Belief weights are an uncalibrated heuristic; information gain is exact only given them.",
    "Catalog incidents use synthetic loan data or a disclosed injected fault on UCI Adult, not customer outages.",
    "Approval records a reviewer decision only; Autopilot never changes a live model.",
    "Runner isolation is a killable child process running vetted templates; there is no generated code.",
]


@lru_cache
def scenario_catalog() -> list[dict[str, Any]]:
    return scenarios.catalog()


def providers() -> dict[str, Any]:
    return {
        "tavily": {"configured": bool(settings.TAVILY_API_KEY), "endpoint": settings.TAVILY_BASE_URL},
        "nebius_token_factory": {"configured": bool(settings.NEBIUS_API_KEY),
                                 "model": settings.NEBIUS_PLANNER_MODEL,
                                 "fast_model": settings.NEBIUS_FAST_MODEL,
                                 "status": "active" if settings.NEBIUS_API_KEY else "on_hold"},
        "nvidia_api": {"configured": bool(settings.NVIDIA_API_KEY),
                       "model": settings.NVIDIA_PLANNER_MODEL, "fast_model": settings.NVIDIA_FAST_MODEL,
                       "status": ("standby" if settings.NEBIUS_API_KEY else "active")
                       if settings.NVIDIA_API_KEY else "not_configured"},
        "planner_provider": _planner_provider(),
        "runner": settings.AUTOPILOT_RUNNER,
    }


def _tavily():
    try:
        return research.TavilyClient(settings.TAVILY_API_KEY, settings.TAVILY_BASE_URL, settings.TAVILY_TIMEOUT_S)
    except research.ResearchUnavailable:
        return None


def _narrator():
    from app.autopilot.narrator import Narrator
    if settings.NEBIUS_API_KEY:
        return Narrator(settings.NEBIUS_API_KEY, settings.NEBIUS_BASE_URL, settings.NEBIUS_FAST_MODEL,
                        "nebius_token_factory", settings.NEBIUS_TIMEOUT_S)
    if settings.NVIDIA_API_KEY:
        return Narrator(settings.NVIDIA_API_KEY, settings.NVIDIA_BASE_URL, settings.NVIDIA_FAST_MODEL,
                        "nvidia_api", settings.NVIDIA_TIMEOUT_S,
                        extra_body={"chat_template_kwargs": {"enable_thinking": False}})
    return None


def _planner_provider() -> str | None:
    if settings.NEBIUS_API_KEY:
        return "nebius_token_factory"
    if settings.NVIDIA_API_KEY:
        return "nvidia_api"
    return None


def _nemotron():
    # Same model family, explicit attribution: the receipt names the provider that answered.
    if settings.NEBIUS_API_KEY:
        return NemotronPlanner(settings.NEBIUS_API_KEY, settings.NEBIUS_BASE_URL, settings.NEBIUS_PLANNER_MODEL,
                               settings.NEBIUS_TIMEOUT_S, provider="nebius_token_factory")
    if settings.NVIDIA_API_KEY:
        return NemotronPlanner(settings.NVIDIA_API_KEY, settings.NVIDIA_BASE_URL, settings.NVIDIA_PLANNER_MODEL,
                               settings.NVIDIA_TIMEOUT_S, provider="nvidia_api",
                               extra_body={"chat_template_kwargs": {"enable_thinking": settings.NVIDIA_ENABLE_THINKING}})
    return None


# ── persistence ──────────────────────────────────────────────────────────────
async def get_by_key(tenant: str, idem_key: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.tenant_id == tenant,
            AutopilotInvestigation.idempotency_key == idem_key))).scalar_one_or_none()
        return row.state if row else None


async def create(tenant: str, body: CreateInvestigation, idem_key: str | None) -> tuple[dict, bool]:
    async with AsyncSessionLocal() as db:
        if idem_key:
            row = (await db.execute(select(AutopilotInvestigation).where(
                AutopilotInvestigation.tenant_id == tenant,
                AutopilotInvestigation.idempotency_key == idem_key))).scalar_one_or_none()
            if row:
                return row.state, False
        run_id = engine.new_run_id()
        state = engine.new_state(run_id, body.model_dump(), settings)
        db.add(AutopilotInvestigation(id=run_id, tenant_id=tenant, idempotency_key=idem_key,
                                      scenario_id=body.scenario_id, status=state["status"], state=state,
                                      auto_run=body.auto_run))
        try:
            await db.commit()
        except IntegrityError:            # concurrent duplicate with the same key
            await db.rollback()
            row = (await db.execute(select(AutopilotInvestigation).where(
                AutopilotInvestigation.tenant_id == tenant,
                AutopilotInvestigation.idempotency_key == idem_key))).scalar_one()
            return row.state, False
        return state, True


async def get(run_id: str, tenant: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.id == run_id, AutopilotInvestigation.tenant_id == tenant))).scalar_one_or_none()
        return row.state if row else None


async def shared_get(run_id: str) -> dict | None:
    """Only called after a signed capability has been validated."""
    async with AsyncSessionLocal() as db:
        row = await db.get(AutopilotInvestigation, run_id)
        return public(row.state) if row and Status(row.status) in TERMINAL else None


async def delete_upload(tenant: str, upload_id: str) -> None:
    """Delete raw files, investigation workspaces and derived records together.

    Active runs are refused so a worker cannot recreate files after deletion.
    """
    import shutil
    from pathlib import Path
    from app.autopilot import uploads
    uploads.get(tenant, upload_id)
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.tenant_id == tenant,
            AutopilotInvestigation.scenario_id == f"upload:{upload_id}"))).scalars().all()
        if any(Status(r.status) not in TERMINAL or r.lease_owner or r.id in _active for r in rows):
            raise PermissionError("Finish or cancel investigations using this upload before deleting it")
        for r in rows:
            await db.execute(delete(AutopilotEvent).where(AutopilotEvent.investigation_id == r.id))
            await db.delete(r)
        await db.commit()
        for r in rows:
            root = (Path(settings.DATA_DIR) / "autopilot").resolve()
            path = (root / r.id).resolve()
            if path != root and path.is_relative_to(root):
                shutil.rmtree(path, ignore_errors=True)
    uploads.delete(tenant, upload_id)


async def cleanup_uploads() -> int:
    from app.autopilot import uploads
    count = 0
    for tenant, uid in uploads.expired():
        try:
            await delete_upload(tenant, uid)
            count += 1
        except (PermissionError, KeyError):
            continue
    return count


async def list_(tenant: str, limit: int) -> list[dict]:
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation).where(AutopilotInvestigation.tenant_id == tenant)
                                 .order_by(AutopilotInvestigation.created_at.desc()).limit(limit))).scalars().all()
        return [{"run_id": r.id, "scenario_id": r.scenario_id, "status": r.status, "decision": r.decision,
                 "title": (r.state.get("scenario") or {}).get("title"), "created_at": r.state.get("created_at"),
                 "planner": (r.state.get("planner") or {}).get("used"),
                 "research": (r.state.get("research") or {}).get("state")} for r in rows]


async def events(run_id: str, tenant: str, after: int) -> list[dict]:
    if await get(run_id, tenant) is None:
        return []
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotEvent).where(AutopilotEvent.investigation_id == run_id,
                                                              AutopilotEvent.seq > after)
                                 .order_by(AutopilotEvent.seq))).scalars().all()
        return [{"seq": r.seq, "stage": r.stage, "kind": r.kind, "summary": r.summary, "data": r.data,
                 "at": r.created_at.isoformat() if r.created_at else None} for r in rows]


async def _persist(state: dict, ev: dict | None) -> None:
    async with AsyncSessionLocal() as db:
        dec = (state.get("decision") or {}).get("decision")
        await db.execute(update(AutopilotInvestigation).where(AutopilotInvestigation.id == state["run_id"])
                         .values(state=state, status=state["status"], decision=dec))
        if ev:
            db.add(AutopilotEvent(investigation_id=state["run_id"], seq=ev["seq"], stage=ev["stage"],
                                  kind=ev["kind"], summary=ev["summary"][:2000], data=ev["data"]))
        await db.commit()


async def _cancel_flag(run_id: str) -> bool:
    async with AsyncSessionLocal() as db:
        return bool((await db.execute(select(AutopilotInvestigation.cancel_requested)
                                       .where(AutopilotInvestigation.id == run_id))).scalar_one_or_none())


async def request_cancel(run_id: str, tenant: str) -> dict | None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.id == run_id, AutopilotInvestigation.tenant_id == tenant))).scalar_one_or_none()
        if row is None:
            return None
        if Status(row.status) in TERMINAL:
            return row.state
        row.cancel_requested = True
        await db.commit()
    if run_id not in _active:            # nothing is driving it: cancel synchronously
        await drive(run_id, tenant, single_step=True)
    return await get(run_id, tenant)


# ── execution ────────────────────────────────────────────────────────────────
def _jsonable(state: dict) -> dict:
    return json.loads(json.dumps(state, default=str))


async def drive(run_id: str, tenant: str, single_step: bool = False, wait: bool = False) -> None:
    if run_id in _active and not _active[run_id].done():
        return
    state = await get(run_id, tenant)
    if state is None or Status(state["status"]) in TERMINAL:
        return
    loop = asyncio.get_running_loop()

    def on_change(s: dict, ev: dict) -> None:
        snap = _jsonable(copy.deepcopy(s))
        asyncio.run_coroutine_threadsafe(_persist(snap, ev), loop).result(timeout=30)

    def cancelled() -> bool:
        return asyncio.run_coroutine_threadsafe(_cancel_flag(run_id), loop).result(timeout=30)

    left = guard.budget_left(await usage_today())
    inv = engine.Investigator(state, settings.DATA_DIR, settings, on_change, cancelled,
                              _tavily() if left["tavily"] else None,
                              _nemotron() if left["llm"] else None,
                              _narrator() if left["llm"] else None)
    if not left["llm"] and inv.s["planner"].get("note") is None and not inv.s["hypotheses"]:
        inv.s["planner"]["note"] = "Daily demo budget for model calls reached; rules planner used."

    async def _go() -> None:
        guard.enqueue(run_id)
        async with _slot():
            guard.dequeue(run_id)
            try:
                if single_step:
                    await asyncio.to_thread(inv.step)
                else:
                    await asyncio.to_thread(inv.run_to_end)
            except Exception as e:  # noqa: BLE001
                _log.error("autopilot_driver_failed", run_id=run_id, error=str(e)[:300])
            finally:
                guard.dequeue(run_id)
                await _persist(_jsonable(inv.s), None)

    task = asyncio.create_task(_go())
    _active[run_id] = task
    task.add_done_callback(lambda _t: _active.pop(run_id, None))
    if single_step or wait:
        await task


async def review(run_id: str, tenant: str, action: str, reviewer: str, note: str) -> dict:
    state = await get(run_id, tenant)
    if state is None:
        raise KeyError(run_id)
    if state["status"] != Status.REVIEW_READY.value:
        raise PermissionError(f"review requires REVIEW_READY, investigation is {state['status']}")
    if not state.get("verification") or state["verification"]["decision"] != "CORRECTION_VERIFIED":
        raise PermissionError("no verified receipt to review")
    state["review"] = {"action": action, "reviewer": reviewer, "note": note, "at": utcnow(),
                       "receipt_experiment": state["verification"].get("experiment_id"),
                       "effect": "decision recorded only; no model alias or registry was changed"}
    state["status"] = Status.APPROVED.value if action == "approve" else Status.REJECTED.value
    state["seq"] += 1
    ev = {"seq": state["seq"], "at": utcnow(), "stage": "REVIEW_READY", "kind": "review",
          "summary": f"{reviewer} {action}d the verified candidate (record only)", "data": {}}
    state["events"].append(ev)
    await _persist(state, ev)
    return state


async def recover_orphans() -> None:
    """In-process drivers do not survive a restart: mark them FAILED, never 'done'.
    In worker mode the API runs nothing; workers recover via lease expiry instead."""
    if settings.AUTOPILOT_EXECUTION == "worker":
        return
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.status.not_in([s.value for s in TERMINAL])))).scalars().all()
        for r in rows:
            st = dict(r.state)
            if st.get("config", {}).get("auto_run", True):
                st["status"] = Status.FAILED.value
                st["decision"] = {"decision": "FAILED", "reason_codes": ["E_INTERRUPTED"],
                                  "summary": "Interrupted by a server restart; no result reported.",
                                  "next_action": "Start a new investigation.", "incumbent_changed": False,
                                  "at": utcnow()}
                r.state, r.status, r.decision = st, st["status"], "FAILED"
        await db.commit()


# ── dispatch (worker mode) ───────────────────────────────────────────────────
WAITING = (Status.CREATED.value,)


async def request_step(run_id: str, tenant: str) -> dict | None:
    """Step mode in worker mode: ask a worker for exactly one more stage."""
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.id == run_id, AutopilotInvestigation.tenant_id == tenant))).scalar_one_or_none()
        if row is None:
            return None
        row.steps_requested = min((row.steps_requested or 0) + 1, 3)
        await db.commit()
        return row.state


async def dispatch_info(run_id: str) -> dict[str, Any]:
    """Queue position and pending-step flag for one investigation."""
    if settings.AUTOPILOT_EXECUTION != "worker":
        return {"queue_position": guard.queue_position(run_id), "step_pending": False}
    from sqlalchemy import func as sa_func, or_
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotInvestigation).where(
            AutopilotInvestigation.id == run_id))).scalar_one_or_none()
        if row is None:
            return {"queue_position": None, "step_pending": False}
        pos = None
        if row.lease_owner is None and row.status not in [s.value for s in TERMINAL] \
                and (row.auto_run or row.steps_requested):
            ahead = (await db.execute(select(sa_func.count()).select_from(AutopilotInvestigation).where(
                AutopilotInvestigation.lease_owner.is_(None),
                AutopilotInvestigation.status.not_in([s.value for s in TERMINAL]),
                or_(AutopilotInvestigation.auto_run.is_(True), AutopilotInvestigation.steps_requested > 0),
                AutopilotInvestigation.created_at < row.created_at))).scalar_one()
            pos = int(ahead) + 1
        return {"queue_position": pos, "step_pending": bool(row.steps_requested)}


async def usage_today() -> dict[str, int]:
    """Provider calls made today, counted from receipts in the database, so the daily
    budget holds across API and worker processes and survives restarts."""
    from datetime import datetime, timezone
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    llm = tav = 0
    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(AutopilotInvestigation.state).where(
            AutopilotInvestigation.created_at >= start))).scalars().all()
    for st in rows:
        llm += len((st.get("planner") or {}).get("calls", [])) + len(st.get("summary_calls") or [])
        tav += len((st.get("research") or {}).get("calls", []))
    return {"llm": llm, "tavily": tav}


def worker_fresh(w: dict) -> bool:
    from datetime import datetime, timedelta, timezone
    seen = w["last_seen"]
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - seen < timedelta(seconds=max(30, settings.AUTOPILOT_LEASE_S))


async def latest_worker() -> dict | None:
    from app.autopilot.models import AutopilotWorker
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(AutopilotWorker).order_by(AutopilotWorker.last_seen.desc())
                                .limit(1))).scalar_one_or_none()
        if row is None:
            return None
        return {"worker_id": row.worker_id, "last_seen": row.last_seen, "info": row.info}


# ── API views ────────────────────────────────────────────────────────────────
def public(state: dict) -> dict:
    """Strip server paths (privileged artifact locations) from anything returned."""
    s = copy.deepcopy(state)
    for e in s.get("experiments", []):
        cand = ((e.get("observation") or {}).get("candidate"))
        if cand and "model_path" in cand:
            cand["artifact"] = cand.pop("model_path").replace("\\", "/").rsplit("/", 1)[-1]
    s.pop("events", None)
    return s


def receipt(state: dict) -> dict:
    s = public(state)
    hyps = s.get("hypotheses", [])
    tests = [{"experiment_id": e["experiment_id"], "template_id": e["template_id"], "kind": e["kind"],
              "parameters": e["parameters"], "expected": e.get("expected_outcomes"),
              "observed": (e.get("observation") or {}).get("bucket"), "status": e["status"],
              "seconds": e.get("actual_seconds"), "signature": e["signature"],
              "output_sha256": e.get("output_sha256"), "source_refs": e.get("source_refs", [])}
             for e in s.get("experiments", [])]
    alternatives = []
    for r in s.get("rankings", []):
        alternatives.append({"step": r["step"], "considered": [
            {k: c[k] for k in ("template_id", "eligible", "reason", "expected_information_bits",
                               "estimated_cost_units", "score")} for c in r["candidates"]]})
    return {
        "receipt_version": 1, "run_id": s["run_id"], "generated_at": utcnow(),
        "incident": s.get("scenario"), "protocol": s.get("protocol"), "inputs_sha256": s.get("inputs"),
        "observed_symptoms": [{k: f[k] for k in ("finding_id", "kind", "title", "severity", "method",
                                                 "sample_count")} for f in s.get("findings", [])],
        "hypotheses": [{k: h[k] for k in ("hypothesis_id", "cause", "statement", "supporting_findings",
                                          "source_refs", "belief_weight", "calibration", "status", "proposed_by")}
                       for h in hyps],
        "belief_history": s.get("belief_history"), "tests": tests, "alternatives": alternatives,
        "policy_denials": s.get("denials"), "verification": s.get("verification"),
        "decision": s.get("decision"), "review": s.get("review"),
        "external_evidence": {"state": s["research"]["state"], "query": s["research"].get("query"),
                              "sources": [{k: e[k] for k in ("source_ref", "title", "url", "publisher",
                                                             "source_type", "fetched_at", "version_match",
                                                             "status", "caveats", "excerpt_sha256", "extracted")}
                                          for e in s["research"]["evidence"]],
                              "mapping": s["research"].get("mapping"),
                              "note": "External sources justify WHY a test was run; they never prove a diagnosis."},
        "provider_calls": s["research"].get("calls", []) + s["planner"].get("calls", []),
        "planner": {k: v for k, v in s["planner"].items() if k != "calls"},
        "cost": {**s["budget"], "final_test_accesses": s.get("final_test_accesses")},
        "limitations": LIMITATIONS,
    }
