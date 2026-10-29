"""v5 repair API (tenant-scoped). Execution happens in the worker; grading in the verifier.

    Intake     POST /repos (url+ref | sample) · POST /repos/upload (zip) · GET /repos/{id}
               PUT /repos/{id}/contract · POST /repos/{id}/discover · GET /samples
    Labels     POST /packs (holdout labels CSV) · DELETE /packs/{id}
    Tasks      POST /tasks · GET /tasks · GET /tasks/{id} · /events · /candidates · /report
               /candidates/{cid}/diff · /candidates/{cid}/receipt · POST /tasks/{id}/cancel
    Delivery   GET /tasks/{id}/patch · POST /tasks/{id}/pr · POST /tasks/{id}/publish
    Replays    GET /replays · GET /replays/{id}
    Ops        GET /status · GET /ready
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.autopilot.routes import autopilot_tenant
from app.repair import delivery, ops, service, snapshots
from app.repair.intake import IntakeError
from app.repair.sandbox.factory import make_runner
from app.repair.security import UnsafeSource

router = APIRouter(prefix="/api/v1/v5", tags=["repair"])


def get_service():
    return service.default_service


async def call(awaitable):
    try:
        return await awaitable
    except service.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except service.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc


async def blocking(fn, *a, **kw):
    try:
        return await asyncio.to_thread(fn, *a, **kw)
    except (snapshots.StageError, IntakeError, UnsafeSource) as exc:
        raise HTTPException(422, str(exc)) from exc


# ── models ────────────────────────────────────────────────────────────────────
class StageRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str | None = Field(default=None, max_length=300)
    ref: str = Field(default="main", max_length=200)
    sample: str | None = Field(default=None, max_length=64)


class SaveContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    yaml: str = Field(min_length=10, max_length=20_000)
    fail_to_pass: list[str] | None = Field(default=None, max_length=50)


class CreateTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo_id: str = Field(pattern=r"^[0-9a-f]{12}$")
    target: str = Field(min_length=3, max_length=500)
    fail_to_pass: list[str] | None = Field(default=None, max_length=50)
    pack_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{12}$")
    mode: str = Field(default="full", pattern=r"^(full|generic)$")
    invite_code: str | None = Field(default=None, max_length=100)


class CancelTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, ge=0)


class OpenPR(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=10, max_length=300)       # used once, never stored or logged
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$")
    base_branch: str = Field(default="main", max_length=100)
    title: str | None = Field(default=None, max_length=200)


# ── ops ───────────────────────────────────────────────────────────────────────
@router.get("/status")
async def status(db=Depends(get_service)):
    return await ops.status(db)


@router.get("/ready")
async def ready(db=Depends(get_service)):
    return await ops.readiness(db)


# ── intake ────────────────────────────────────────────────────────────────────
@router.get("/samples")
async def samples():
    return {"samples": await asyncio.to_thread(snapshots.samples)}


@router.post("/repos", status_code=201)
async def stage(body: StageRepo, tenant=Depends(autopilot_tenant)):
    if body.sample:
        return await blocking(snapshots.stage_sample, tenant, body.sample)
    if not body.url:
        raise HTTPException(422, "give a repository URL or a sample name")
    return await blocking(snapshots.stage_url, tenant, body.url, body.ref)


@router.post("/repos/upload", status_code=201)
async def stage_upload(file: UploadFile = File(...), tenant=Depends(autopilot_tenant)):
    data = await file.read(snapshots.settings.REPAIR_MAX_REPO_MB * 1024 * 1024 + 1)
    return await blocking(snapshots.stage_zip, tenant, data, file.filename or "upload.zip")


@router.get("/repos/{repo_id}")
async def repo(repo_id: str, tenant=Depends(autopilot_tenant)):
    return await blocking(snapshots.describe, repo_id, tenant)


@router.put("/repos/{repo_id}/contract")
async def save_contract(repo_id: str, body: SaveContract, tenant=Depends(autopilot_tenant)):
    return await blocking(snapshots.save_contract, repo_id, tenant, body.yaml, body.fail_to_pass)


@router.post("/repos/{repo_id}/discover")
async def discover(repo_id: str, tenant=Depends(autopilot_tenant)):
    return await blocking(snapshots.discover, repo_id, tenant, make_runner())


# ── evaluation packs ──────────────────────────────────────────────────────────
@router.post("/packs", status_code=201)
async def create_pack(file: UploadFile = File(...), id_column: str = Form(...), target: str = Form(...),
                      positive_label: str = Form("1"), segment_column: str | None = Form(None),
                      tenant=Depends(autopilot_tenant)):
    data = await file.read(20 * 1024 * 1024 + 1)
    return await blocking(snapshots.create_pack, tenant, data, id_column=id_column, target=target,
                          positive_label=positive_label, segment_column=segment_column or None)


@router.delete("/packs/{pack_id}", status_code=204)
async def delete_pack(pack_id: str, tenant=Depends(autopilot_tenant)):
    await blocking(snapshots.delete_pack, pack_id, tenant)


# ── tasks ─────────────────────────────────────────────────────────────────────
@router.post("/tasks", status_code=201)
async def create(body: CreateTask, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    try:
        budget = await ops.admit_live_run(db, tenant, body.invite_code)
    except ops.Denied as e:
        raise HTTPException(403, str(e)) from e
    info = await blocking(snapshots.describe, body.repo_id, tenant)
    if body.fail_to_pass:
        await blocking(snapshots.save_contract, body.repo_id, tenant, info["contract_yaml"], body.fail_to_pass)
    frozen = await blocking(snapshots.freeze, body.repo_id, tenant)
    sealed = f"fixture:{frozen['sample']}" if frozen["sample"] else None
    if body.pack_id:
        if snapshots.pack_owner(body.pack_id) != tenant:
            raise HTTPException(404, "evaluation pack not found")
        sealed = f"pack:{body.pack_id}"
    src = frozen["source"]
    spec = {"snapshot_dir": frozen["snapshot_dir"], "staging_id": body.repo_id, "sealed_ref": sealed,
            "mode": body.mode, "sample": frozen["sample"], "source": src.get("kind"),
            "repo_name": src.get("url") or src.get("sample") or src.get("name"),
            "upstream_sha": frozen["upstream_sha"], "budget": budget}
    task = await call(db.create(tenant, contract=frozen["contract"], base_commit_sha=frozen["sha"],
                                repo_snapshot_digest=frozen["digest"], target=body.target, spec=spec))
    return task


@router.get("/tasks")
async def tasks(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.list_tasks(tenant, limit=limit, offset=offset))


@router.get("/tasks/{task_id}")
async def task(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.get(task_id, tenant))


@router.get("/tasks/{task_id}/events")
async def events(task_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                 tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.events(task_id, tenant, after=after, limit=limit))


@router.get("/tasks/{task_id}/report")
async def report(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    rep = await call(db.report(task_id, tenant))
    if rep is None:
        raise HTTPException(409, "the run has not finished")
    return rep


@router.get("/tasks/{task_id}/candidates")
async def candidates(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.candidates(task_id, tenant))


@router.get("/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def diff(candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return (await call(db.candidate(candidate_id, tenant)))["diff"]


@router.get("/candidates/{candidate_id}/receipt")
async def receipt(candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant))


@router.get("/tasks/{task_id}/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def task_diff(task_id: str, candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return (await call(db.candidate(candidate_id, tenant, task_id=task_id)))["diff"]


@router.get("/tasks/{task_id}/candidates/{candidate_id}/receipt")
async def task_receipt(task_id: str, candidate_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant, task_id=task_id))


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, body: CancelTask | None = None,
                 tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    return await call(db.cancel(task_id, tenant, expected_version=body.version if body else None))


# ── delivery ──────────────────────────────────────────────────────────────────
async def _finalist(db, task_id, tenant):
    rep = await call(db.report(task_id, tenant))
    if not rep or not rep.get("finalist") or rep.get("decision") != "VERIFIED":
        raise HTTPException(409, "only a verified repair can be delivered")
    fin = next(r for r in rep["candidates"] if r["candidate"]["candidate_id"] == rep["finalist"])
    return rep, fin["candidate"]["diff"]


@router.get("/tasks/{task_id}/patch", response_class=PlainTextResponse)
async def patch(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    _, diff = await _finalist(db, task_id, tenant)
    return PlainTextResponse(diff, headers={"Content-Disposition": f'attachment; filename="autopilot-{task_id[:8]}.patch"'})


@router.post("/tasks/{task_id}/pr")
async def open_pr(task_id: str, body: OpenPR, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    rep, diff = await _finalist(db, task_id, tenant)
    row = await call(db.row(task_id, tenant))
    t = await call(db.get(task_id, tenant))
    spec = row["spec"]
    if spec.get("source") != "url":
        raise HTTPException(409, "pull requests need a repository that was added by URL")
    try:
        return await asyncio.to_thread(
            delivery.create_draft_pr, token=body.token, repo=body.repo, base_sha=spec["upstream_sha"],
            base_branch=body.base_branch, snapshot_dir=spec["snapshot_dir"], diff=diff,
            title=body.title or f"AutoML Autopilot: {t['target'][:80]}", body=delivery.pr_body(t, rep))
    except delivery.DeliveryError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/tasks/{task_id}/publish")
async def publish(task_id: str, tenant=Depends(autopilot_tenant), db=Depends(get_service)):
    rep = await call(db.report(task_id, tenant))
    if rep is None:
        raise HTTPException(409, "the run has not finished")
    t = await call(db.get(task_id, tenant))
    return {"replay_id": await asyncio.to_thread(ops.publish_replay, task_id, rep, t)}


# ── replays ───────────────────────────────────────────────────────────────────
@router.get("/replays")
async def replays():
    return {"replays": await asyncio.to_thread(ops.list_replays)}


@router.get("/replays/{replay_id}")
async def replay(replay_id: str):
    try:
        return await asyncio.to_thread(ops.get_replay, replay_id)
    except FileNotFoundError as e:
        raise HTTPException(404, "replay not found") from e
