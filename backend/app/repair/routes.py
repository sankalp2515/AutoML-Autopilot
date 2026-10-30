"""v5 repair API (tenant-scoped). Execution happens in the worker; grading in the verifier.

    Intake     POST /repos (url+ref | sample) · POST /repos/upload (zip) · GET /repos/{id}
               PUT /repos/{id}/contract · POST /repos/{id}/discover · GET /samples
    Labels     POST /packs (holdout labels CSV) · DELETE /packs/{id}
    Tasks      POST /tasks · GET /tasks · GET /tasks/{id} · /events · /candidates · /report
               /candidates/{cid}/diff · /candidates/{cid}/receipt · POST /tasks/{id}/cancel
    Delivery   GET /tasks/{id}/patch · POST /tasks/{id}/pr · POST /tasks/{id}/publish
    Replays    GET /replays · GET /replays/{id}
    Ops        GET /status · GET /ready · GET /metrics[?format=prometheus]
    Accounts   GET /auth/github · POST /auth/github/callback · GET /me · POST /workspaces
               GET /workspaces/{id}/members · PUT /workspaces/{id}/members/{login}   (BE-23)
    GitHub App POST /github/webhook · GET|POST /github/links · DELETE /github/links/{owner}/{name} (BE-24)

Reads need any workspace role; anything that stages, uploads, runs or delivers needs member.
"""
from __future__ import annotations

import asyncio

import hashlib
import json

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from app.repair import budgets
from app.repair import accounts, delivery, github_app, ops, service, snapshots
from app.autopilot.routes import autopilot_tenant  # noqa: F401 - re-exported for dependency overrides
from app.repair.accounts import Actor, get_service
from app.repair.intake import IntakeError
from app.repair.sandbox.factory import make_runner
from app.repair.security import UnsafeSource

router = APIRouter(prefix="/api/v1/v5", tags=["repair"])


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
    budget: budgets.TaskBudget | None = None


class CancelTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int | None = Field(default=None, ge=0)


class Callback(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(min_length=4, max_length=200)
    state: str = Field(min_length=10, max_length=2000)


class NewWorkspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)


class MemberRole(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str | None = Field(default=None, pattern=r"^(owner|admin|member|viewer)$")   # None removes


class Link(BaseModel):
    model_config = ConfigDict(extra="forbid")
    repo: str = Field(pattern=r"^[\w.-]+/[\w.-]+$", max_length=200)
    auto_pr: bool = True


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


@router.get("/metrics")
async def metrics(format: str = Query("json", pattern="^(json|prometheus)$"), db=Depends(get_service)):
    m = await ops.metrics(db)
    return PlainTextResponse(ops.prometheus(m)) if format == "prometheus" else m


@router.get("/ready")
async def ready(db=Depends(get_service)):
    return await ops.readiness(db)


# ── intake ────────────────────────────────────────────────────────────────────
@router.get("/samples")
async def samples():
    return {"samples": await asyncio.to_thread(snapshots.samples)}


@router.post("/repos", status_code=201)
async def stage(body: StageRepo, tenant=Depends(accounts.writer)):
    if body.sample:
        return await blocking(snapshots.stage_sample, tenant, body.sample)
    if not body.url:
        raise HTTPException(422, "give a repository URL or a sample name")
    return await blocking(snapshots.stage_url, tenant, body.url, body.ref)


@router.post("/repos/upload", status_code=201)
async def stage_upload(file: UploadFile = File(...), tenant=Depends(accounts.writer)):
    data = await file.read(snapshots.settings.REPAIR_MAX_REPO_MB * 1024 * 1024 + 1)
    return await blocking(snapshots.stage_zip, tenant, data, file.filename or "upload.zip")


@router.get("/repos/{repo_id}")
async def repo(repo_id: str, tenant=Depends(accounts.reader)):
    return await blocking(snapshots.describe, repo_id, tenant)


@router.put("/repos/{repo_id}/contract")
async def save_contract(repo_id: str, body: SaveContract, tenant=Depends(accounts.writer)):
    return await blocking(snapshots.save_contract, repo_id, tenant, body.yaml, body.fail_to_pass)


@router.post("/repos/{repo_id}/discover")
async def discover(repo_id: str, tenant=Depends(accounts.writer)):
    return await blocking(snapshots.discover, repo_id, tenant, make_runner())


# ── evaluation packs ──────────────────────────────────────────────────────────
@router.post("/packs", status_code=201)
async def create_pack(file: UploadFile = File(...), id_column: str = Form(...), target: str = Form(...),
                      positive_label: str = Form("1"), segment_column: str | None = Form(None),
                      tenant=Depends(accounts.writer)):
    data = await file.read(20 * 1024 * 1024 + 1)
    return await blocking(snapshots.create_pack, tenant, data, id_column=id_column, target=target,
                          positive_label=positive_label, segment_column=segment_column or None)


@router.delete("/packs/{pack_id}", status_code=204)
async def delete_pack(pack_id: str, tenant=Depends(accounts.writer)):
    await blocking(snapshots.delete_pack, pack_id, tenant)


@router.post("/budget/estimate")
async def budget_estimate(body: budgets.TaskBudget, tenant=Depends(accounts.writer), db=Depends(get_service)):
    status=await ops.status(db)
    return budgets.estimate(body,models=status.get("models"))


# ── tasks ─────────────────────────────────────────────────────────────────────
async def _create_task(db, tenant: str, repo_id: str, target: str, *, budget: dict, mode: str = "full",
                       fail_to_pass: list[str] | None = None, pack_id: str | None = None, extra: dict | None = None, requested_budget: budgets.TaskBudget | None = None):
    info = await blocking(snapshots.describe, repo_id, tenant)
    if fail_to_pass:
        await blocking(snapshots.save_contract, repo_id, tenant, info["contract_yaml"], fail_to_pass)
    frozen = await blocking(snapshots.freeze, repo_id, tenant)
    sealed = f"fixture:{frozen['sample']}" if frozen["sample"] else None
    if pack_id:
        if snapshots.pack_owner(pack_id) != tenant:
            raise HTTPException(404, "evaluation pack not found")
        sealed = f"pack:{pack_id}"
    src = frozen["source"]
    spec = {"snapshot_dir": frozen["snapshot_dir"], "staging_id": repo_id, "sealed_ref": sealed,
            "mode": mode, "sample": frozen["sample"], "source": src.get("kind"),
            "repo_name": src.get("url") or src.get("sample") or src.get("name"),
            "upstream_sha": frozen["upstream_sha"], "budget": budget, **(extra or {})}
    return await call(db.create(tenant, contract=frozen["contract"], base_commit_sha=frozen["sha"],
                                repo_snapshot_digest=frozen["digest"], target=target, spec=spec, limits=budgets.limits_for(requested_budget)))


@router.post("/tasks", status_code=201)
async def create(body: CreateTask, actor: Actor = Depends(accounts.actor), db=Depends(get_service)):
    actor.need("member")
    try:
        budget = await ops.admit_live_run(db, actor.tenant, body.invite_code, trusted=actor.account_id is not None)
    except ops.Denied as e:
        raise HTTPException(403, str(e)) from e
    return await _create_task(db, actor.tenant, body.repo_id, body.target, budget=budget, mode=body.mode,
                              fail_to_pass=body.fail_to_pass, pack_id=body.pack_id, requested_budget=body.budget)


@router.get("/tasks")
async def tasks(limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0),
                tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.list_tasks(tenant, limit=limit, offset=offset))


@router.get("/tasks/{task_id}")
async def task(task_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.get(task_id, tenant))


@router.get("/tasks/{task_id}/events")
async def events(task_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500),
                 tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.events(task_id, tenant, after=after, limit=limit))


@router.get("/tasks/{task_id}/report")
async def report(task_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    rep = await call(db.report(task_id, tenant))
    if rep is None:
        raise HTTPException(409, "the run has not finished")
    return rep


@router.get("/tasks/{task_id}/candidates")
async def candidates(task_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.candidates(task_id, tenant))


@router.get("/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def diff(candidate_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return (await call(db.candidate(candidate_id, tenant)))["diff"]


@router.get("/candidates/{candidate_id}/receipt")
async def receipt(candidate_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant))


@router.get("/tasks/{task_id}/candidates/{candidate_id}/diff", response_class=PlainTextResponse)
async def task_diff(task_id: str, candidate_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return (await call(db.candidate(candidate_id, tenant, task_id=task_id)))["diff"]


@router.get("/tasks/{task_id}/candidates/{candidate_id}/receipt")
async def task_receipt(task_id: str, candidate_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    return await call(db.receipts(candidate_id, tenant, task_id=task_id))


@router.post("/tasks/{task_id}/cancel")
async def cancel(task_id: str, body: CancelTask | None = None,
                 tenant=Depends(accounts.writer), db=Depends(get_service)):
    return await call(db.cancel(task_id, tenant, expected_version=body.version if body else None))


# ── delivery ──────────────────────────────────────────────────────────────────
async def _finalist(db, task_id, tenant):
    rep = await call(db.report(task_id, tenant))
    if not rep or not rep.get("finalist") or rep.get("decision") != "VERIFIED":
        raise HTTPException(409, "only a verified repair can be delivered")
    fin = next(r for r in rep["candidates"] if r["candidate"]["candidate_id"] == rep["finalist"])
    return rep, fin["candidate"]["diff"]


@router.get("/tasks/{task_id}/patch", response_class=PlainTextResponse)
async def patch(task_id: str, tenant=Depends(accounts.reader), db=Depends(get_service)):
    _, diff = await _finalist(db, task_id, tenant)
    return PlainTextResponse(diff, headers={"Content-Disposition": f'attachment; filename="autopilot-{task_id[:8]}.patch"'})


@router.post("/tasks/{task_id}/pr")
async def open_pr(task_id: str, body: OpenPR, tenant=Depends(accounts.writer), db=Depends(get_service)):
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
async def publish(task_id: str, tenant=Depends(accounts.writer), db=Depends(get_service)):
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


# ── accounts and teams (BE-23) ────────────────────────────────────────────────
def _account_error(e: Exception) -> HTTPException:
    return HTTPException(403 if isinstance(e, accounts.Forbidden) else 422, str(e))


@router.get("/auth/github")
async def github_sign_in():
    try:
        return accounts.oauth_start()
    except accounts.AccountError as e:
        raise HTTPException(503, str(e)) from e


@router.post("/auth/github/callback")
async def github_callback(body: Callback, db=Depends(get_service)):
    try:
        profile = await asyncio.to_thread(accounts.github_profile, body.code, body.state)
    except accounts.AccountError as e:
        raise HTTPException(401, str(e)) from e
    return await accounts.sign_in(db, profile)


@router.get("/me")
async def whoami(actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    return {**(await accounts.me(db, actor.account_id)), "current_workspace": actor.workspace_id, "role": actor.role}


@router.post("/workspaces", status_code=201)
async def new_workspace(body: NewWorkspace, actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    return await accounts.create_workspace(db, actor.account_id, body.name)


def _same_workspace(actor: Actor, workspace_id: str) -> None:
    if actor.workspace_id != workspace_id:
        raise HTTPException(404, "workspace not found")     # send X-Workspace for the workspace you manage


@router.get("/workspaces/{workspace_id}/members")
async def list_members(workspace_id: str, actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    _same_workspace(actor, workspace_id)
    return {"members": await accounts.members(db, workspace_id)}


@router.put("/workspaces/{workspace_id}/members/{login}")
async def set_member(workspace_id: str, login: str, body: MemberRole, actor: Actor = Depends(accounts.signed_in),
                     db=Depends(get_service)):
    _same_workspace(actor, workspace_id)
    try:
        return await accounts.set_member(db, workspace_id, actor.role, login, body.role)
    except (accounts.AccountError, accounts.Forbidden) as e:
        raise _account_error(e) from e


# ── GitHub App (BE-24) ────────────────────────────────────────────────────────
@router.get("/github/links")
async def list_links(actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    return {"links": await github_app.links(db, actor.workspace_id)}


@router.post("/github/links", status_code=201)
async def add_link(body: Link, actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    actor.need("admin")
    try:
        return await github_app.save_link(db, actor.workspace_id, actor.account_id, body.repo, body.auto_pr)
    except github_app.AppError as e:
        raise HTTPException(409, str(e)) from e


@router.delete("/github/links/{owner}/{name}", status_code=204)
async def remove_link(owner: str, name: str, actor: Actor = Depends(accounts.signed_in), db=Depends(get_service)):
    actor.need("admin")
    try:
        await github_app.delete_link(db, actor.workspace_id, f"{owner}/{name}")
    except github_app.AppError as e:
        raise HTTPException(404, str(e)) from e


@router.post("/github/webhook", status_code=202)
async def github_webhook(request: Request, background: BackgroundTasks, db=Depends(get_service),
                         x_github_event: str = Header(default="", max_length=40),
                         x_github_delivery: str = Header(default="", max_length=64),
                         x_hub_signature_256: str | None = Header(default=None, max_length=100)):
    secret = github_app.settings.GITHUB_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(503, "GitHub App webhooks are not configured")
    body = await request.body()
    if len(body) > 5 * 1024 * 1024 or not github_app.verify_signature(secret, body, x_hub_signature_256):
        raise HTTPException(401, "bad signature")
    if x_github_event == "ping":
        return {"ok": True}
    try:
        payload = json.loads(body)
    except ValueError as e:
        raise HTTPException(400, "body is not JSON") from e
    run = github_app.failed_run(x_github_event, payload)
    repo = run["repo"] if run else ((payload.get("repository") or {}).get("full_name") or "").lower() or None
    delivery_id = x_github_delivery or "sha-" + hashlib.sha256(body).hexdigest()[:40]
    if run is None:
        return {"accepted": False, "reason": "not a failed CI run"}
    link = await github_app.link_for(db, run)
    if link is None:
        return {"accepted": False, "reason": "repository is not linked to a workspace"}
    if not await github_app.record_delivery(db, delivery_id, x_github_event, repo, "queued"):
        return {"accepted": False, "reason": "duplicate delivery"}

    async def create(tenant, repo_id, target, extra):
        return await _create_task(db, tenant, repo_id, target, budget={"source": "github"}, extra=extra)

    background.add_task(github_app.handle_failure, db, delivery_id, run, link, create)
    return {"accepted": True, "delivery": delivery_id}
