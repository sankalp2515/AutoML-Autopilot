"""HTTP API for AutoML Autopilot — mounted at /api/v1/autopilot.

Every investigation-scoped route filters by the caller's tenant (404 for other
tenants' runs). Responses never include server file paths or secrets.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response

from app.autopilot import guard, service
from app.autopilot.schemas import TERMINAL, CreateInvestigation, ReviewRequest, Status
from app.core.auth import PUBLIC_TENANT, current_tenant

router = APIRouter(prefix="/api/v1/autopilot", tags=["autopilot"])
BENCHMARK_FILE = Path(__file__).resolve().parents[3] / "docs" / "benchmarks" / "autopilot_benchmark.json"
ABLATION_FILE = BENCHMARK_FILE.with_name("autopilot_ablation_live.json")


async def autopilot_tenant(tenant: str = Depends(current_tenant),
                           x_autopilot_session: str | None = Header(default=None, max_length=64)) -> str:
    """Public mode: one tenant per browser session, so visitors never share run limits."""
    return guard.scoped_tenant(tenant, PUBLIC_TENANT, x_autopilot_session)


async def _load(investigation_id: str, tenant: str) -> dict:
    st = await service.get(investigation_id, tenant)
    if st is None:
        raise HTTPException(404, "Investigation not found")
    return st


@router.get("/health")
async def health_() -> JSONResponse:
    """Liveness of the Autopilot control plane: database reachable, and in worker mode
    whether a worker heartbeat is fresh. 503 only when the database is down."""
    try:
        w = await service.latest_worker()
    except Exception:  # noqa: BLE001
        return JSONResponse({"status": "down", "database": False}, status_code=503)
    body = {"status": "ok", "database": True, "execution": service.settings.AUTOPILOT_EXECUTION}
    if service.settings.AUTOPILOT_EXECUTION == "worker":
        body["worker_online"] = bool(w) and service.worker_fresh(w)
    return JSONResponse(body)


@router.get("/scenarios")
async def scenarios_() -> dict:
    return {"scenarios": service.scenario_catalog()}


@router.get("/providers")
async def providers_() -> dict:
    used = guard.usage(await service.usage_today())
    if service.settings.AUTOPILOT_EXECUTION != "worker":
        return {**service.providers(), "demo_usage": used, "execution": "inline"}
    # The API holds no provider keys in worker mode: report what the live worker said.
    w = await service.latest_worker()
    info = (w or {}).get("info") or {}
    return {**(info.get("providers") or service.providers()), "demo_usage": used, "execution": "worker",
            "worker": {"online": bool(w) and service.worker_fresh(w), "id": (w or {}).get("worker_id")}}


@router.post("/investigations", status_code=202)
async def create_(body: CreateInvestigation, request: Request, tenant: str = Depends(autopilot_tenant),
                  idempotency_key: str | None = Header(default=None, max_length=128)) -> JSONResponse:
    if body.scenario_id not in {s["scenario_id"] for s in service.scenario_catalog()}:
        raise HTTPException(422, f"unknown scenario_id {body.scenario_id!r}")
    if not idempotency_key or not await service.get_by_key(tenant, idempotency_key):
        await service.expire_idle(tenant)
        if await service.active_count(tenant) >= service.MAX_ACTIVE_PER_TENANT:
            raise HTTPException(429, "You have 5 investigations still open. Finish or cancel one "
                                     "(step-mode runs close themselves after 15 idle minutes).")
        client = tenant if tenant != PUBLIC_TENANT else (request.client.host if request.client else "unknown")
        if not guard.allow_create(client):
            raise HTTPException(429, "Demo limit: at most "
                                     f"{service.settings.AUTOPILOT_CREATES_PER_10MIN} new investigations "
                                     "per 10 minutes. Open a recent one meanwhile.")
    state, created = await service.create(tenant, body, idempotency_key)
    if created and body.auto_run and service.settings.AUTOPILOT_EXECUTION != "worker":
        await service.drive(state["run_id"], tenant)       # worker mode: a worker claims it
    return JSONResponse({"run_id": state["run_id"], "status": state["status"], "created": created},
                        status_code=202 if created else 200)


@router.get("/investigations")
async def list_(limit: int = Query(25, ge=1, le=100), tenant: str = Depends(autopilot_tenant)) -> dict:
    return {"investigations": await service.list_(tenant, limit)}


@router.get("/investigations/{investigation_id}")
async def get_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {**s, **await service.dispatch_info(investigation_id)}


@router.get("/investigations/{investigation_id}/events")
async def events_(investigation_id: str, after: int = Query(0, ge=0),
                  tenant: str = Depends(autopilot_tenant)) -> dict:
    await _load(investigation_id, tenant)
    return {"events": await service.events(investigation_id, tenant, after)}


@router.get("/investigations/{investigation_id}/evidence")
async def evidence_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {k: s[k] for k in ("findings", "hypotheses", "belief_history", "research")}


@router.get("/investigations/{investigation_id}/experiments")
async def experiments_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = service.public(await _load(investigation_id, tenant))
    return {k: s[k] for k in ("experiments", "rankings", "denials", "budget")}


@router.post("/investigations/{investigation_id}/next")
async def next_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = await _load(investigation_id, tenant)
    if Status(s["status"]) in TERMINAL:
        raise HTTPException(409, f"investigation is {s['status']}")
    if service.settings.AUTOPILOT_EXECUTION == "worker":
        await service.request_step(investigation_id, tenant)
    else:
        await service.drive(investigation_id, tenant, single_step=True)
    s = service.public(await _load(investigation_id, tenant))
    return {**s, **await service.dispatch_info(investigation_id)}


@router.post("/investigations/{investigation_id}/cancel")
async def cancel_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    s = await service.request_cancel(investigation_id, tenant)
    if s is None:
        raise HTTPException(404, "Investigation not found")
    return {"run_id": investigation_id, "status": s["status"], "cancel_requested": True}


@router.get("/investigations/{investigation_id}/receipt")
async def receipt_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> dict:
    return service.receipt(await _load(investigation_id, tenant))


@router.get("/investigations/{investigation_id}/export")
async def export_(investigation_id: str, tenant: str = Depends(autopilot_tenant)) -> Response:
    body = json.dumps(service.receipt(await _load(investigation_id, tenant)), indent=2, default=str)
    return Response(body, media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="autopilot-receipt-{investigation_id[:8]}.json"'})


@router.post("/investigations/{investigation_id}/review")
async def review_(investigation_id: str, body: ReviewRequest, tenant: str = Depends(autopilot_tenant)) -> dict:
    try:
        s = await service.review(investigation_id, tenant, body.action, body.reviewer, body.note)
    except KeyError:
        raise HTTPException(404, "Investigation not found") from None
    except PermissionError as e:
        raise HTTPException(409, str(e)) from None
    return {"run_id": investigation_id, "status": s["status"], "review": s["review"]}


@router.get("/benchmark")
async def benchmark_() -> dict:
    if not BENCHMARK_FILE.exists():
        return {"available": False,
                "how_to_run": "cd backend && python -m app.autopilot.benchmark --seeds 5"}
    return {"available": True, **json.loads(BENCHMARK_FILE.read_text(encoding="utf-8"))}


@router.get("/ablation")
async def ablation_() -> dict:
    """Live planner (rules vs Nemotron) and Tavily on/off ablation, if it has been run."""
    if not ABLATION_FILE.exists():
        return {"available": False, "how_to_run": "python scripts/run_live_ablation.py --seeds 5"}
    data = json.loads(ABLATION_FILE.read_text(encoding="utf-8"))
    return {"available": True, **{k: v for k, v in data.items() if k not in ("rows", "tavily_rows")}}
