"""Tenant-scoped repair API. Queues durable work; execution is a separate stub worker."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.autopilot.routes import autopilot_tenant
from app.repair.contracts import RepoContract
from app.repair import service

router = APIRouter(prefix="/api/v1/v5", tags=["repair"])


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    contract: RepoContract
    base_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    repo_snapshot_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target: str = Field(min_length=3, max_length=500)


class CancelTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, ge=0)


def get_service():
    return service.default_service


async def call(awaitable):
    try:
        return await awaitable
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except service.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/tasks", status_code=201)
async def create(body: CreateTask, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.create(tenant, **body.model_dump(exclude={"contract"}), contract=body.contract))


@router.get("/tasks")
async def tasks(limit: int = Query(100, ge=1, le=100), offset: int = Query(0, ge=0),
                tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.list_tasks(tenant, limit=limit, offset=offset))


@router.get("/tasks/{task_id}")
async def task(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.get(task_id, tenant))


@router.get("/tasks/{task_id}/events")
@router.get("/events")
async def events(task_id: str, after: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=500),
                 tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.events(task_id, tenant, after=after, limit=limit))


@router.get("/tasks/{task_id}/candidates")
@router.get("/candidates")
async def candidates(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.candidates(task_id, tenant))


@router.get("/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def diff(candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    result = await call(db.candidate(candidate_id, tenant))
    return result["diff"]


@router.get("/tasks/{task_id}/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def task_diff(task_id: str, candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    result = await call(db.candidate(candidate_id, tenant, task_id=task_id))
    return result["diff"]


@router.get("/candidates/{candidate_id}/receipt")
async def receipt(candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant))


@router.get("/tasks/{task_id}/candidates/{candidate_id}/receipt")
async def task_receipt(task_id: str, candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant, task_id=task_id))


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, body: CancelTask | None = None,
                 tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.cancel(task_id, tenant, expected_version=body.version if body else None))
