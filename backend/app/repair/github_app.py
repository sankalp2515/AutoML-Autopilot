"""GitHub App (BE-24): "CI failed → open a repair task", and a draft PR when it is verified.

Flow
    1. A workspace admin links a repository (`POST /github/links`) and installs the App on it.
    2. GitHub sends `workflow_run` (or `check_suite`) "completed" with a failing conclusion to
       `POST /github/webhook`. The api checks the HMAC signature (GITHUB_WEBHOOK_SECRET),
       records the delivery id once (redelivery is a no-op) and answers 202 immediately.
    3. In the background it stages the failing commit, requires the repository's own valid
       `autopilot.yaml` (a webhook never edits a contract), admits the run against the
       workspace's caps and creates a repair task in that workspace.
    4. When the worker finishes a task whose decision is VERIFIED and the link allows it, the
       worker opens a DRAFT pull request onto the failing branch with an installation token.
       Nothing is ever merged.

Only the api holds the webhook secret; only the repair-worker holds the App private key.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Any

import httpx
import jwt
from sqlalchemy import select

from app.config import settings
from app.repair import models as m

API = "https://api.github.com"
FAILED = {"failure", "timed_out"}


class AppError(RuntimeError):
    pass


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    want = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, header[7:])


def failed_run(event: str, p: dict) -> dict | None:
    """The failing commit described by a webhook payload, or None if there is nothing to repair."""
    repo = p.get("repository") or {}
    if event == "workflow_run":
        run = p.get("workflow_run") or {}
        if p.get("action") != "completed" or run.get("conclusion") not in FAILED:
            return None
        sha, branch, title, url = run.get("head_sha"), run.get("head_branch"), run.get("name"), run.get("html_url")
    elif event == "check_suite":
        suite = p.get("check_suite") or {}
        if p.get("action") != "completed" or suite.get("conclusion") not in FAILED:
            return None
        sha, branch, title, url = suite.get("head_sha"), suite.get("head_branch"), "checks", None
    else:
        return None
    if not (repo.get("full_name") and repo.get("clone_url") and sha and branch):
        return None
    return {"repo": repo["full_name"].lower(), "clone_url": repo["clone_url"], "sha": sha, "branch": branch,
            "workflow": title or "CI", "run_url": url, "installation_id": (p.get("installation") or {}).get("id")}


# ── links ─────────────────────────────────────────────────────────────────────
def _link_json(r: m.GitHubLink) -> dict:
    return {"repo": r.repo, "workspace_id": r.workspace_id, "installed": r.installation_id is not None,
            "auto_pr": r.auto_pr, "created_at": r.created_at}


async def save_link(svc, workspace_id: str, account_id: str, repo: str, auto_pr: bool) -> dict:
    repo = repo.lower()
    async with svc.sessions() as db, db.begin():
        row = await db.get(m.GitHubLink, repo)
        if row is not None and row.workspace_id != workspace_id:
            raise AppError("this repository is linked to another workspace")
        if row is None:
            row = m.GitHubLink(repo=repo, workspace_id=workspace_id, auto_pr=auto_pr, created_by=account_id,
                               created_at=time.time())
            db.add(row)
        row.auto_pr = auto_pr
    return _link_json(row)


async def links(svc, workspace_id: str) -> list[dict]:
    async with svc.sessions() as db:
        rows = (await db.execute(select(m.GitHubLink).where(m.GitHubLink.workspace_id == workspace_id)
                                 .order_by(m.GitHubLink.repo))).scalars().all()
        deliveries = (await db.execute(select(m.WebhookDelivery).where(
            m.WebhookDelivery.repo.in_([r.repo for r in rows])).order_by(m.WebhookDelivery.at.desc()).limit(20)
        )).scalars().all() if rows else []
    return [{**_link_json(r), "recent": [{"event": d.event, "outcome": d.outcome, "task_id": d.task_id, "at": d.at}
                                         for d in deliveries if d.repo == r.repo][:5]} for r in rows]


async def delete_link(svc, workspace_id: str, repo: str) -> None:
    async with svc.sessions() as db, db.begin():
        row = await db.get(m.GitHubLink, repo.lower())
        if row is None or row.workspace_id != workspace_id:
            raise AppError("link not found")
        await db.delete(row)


# ── webhook ───────────────────────────────────────────────────────────────────
async def record_delivery(svc, delivery_id: str, event: str, repo: str | None, outcome: str) -> bool:
    """False if this delivery id was seen before (GitHub redelivers on timeouts)."""
    async with svc.sessions() as db, db.begin():
        if await db.get(m.WebhookDelivery, delivery_id) is not None:
            return False
        db.add(m.WebhookDelivery(id=delivery_id, event=event, repo=repo, outcome=outcome[:300], at=time.time()))
    return True


async def _outcome(svc, delivery_id: str, outcome: str, task_id: str | None = None) -> None:
    async with svc.sessions() as db, db.begin():
        row = await db.get(m.WebhookDelivery, delivery_id)
        if row is not None:
            row.outcome, row.task_id = outcome[:300], task_id


async def link_for(svc, run: dict) -> m.GitHubLink | None:
    async with svc.sessions() as db, db.begin():
        row = await db.get(m.GitHubLink, run["repo"])
        if row is None:
            return None
        if run.get("installation_id"):
            if row.installation_id is None:
                row.installation_id = int(run["installation_id"])
            elif row.installation_id != int(run["installation_id"]):
                return None                      # a different installation cannot drive this link
        return row


async def handle_failure(svc, delivery_id: str, run: dict, link: m.GitHubLink, create_task) -> None:
    """Background part of the webhook. `create_task(tenant, repo_id, target, extra_spec)` is the
    same task-creation path the UI uses, so caps, freezing and digests are identical."""
    import asyncio

    from app.repair import ops, snapshots
    tenant = f"ws:{link.workspace_id}"
    try:
        await ops.admit_live_run(svc, tenant, None, trusted=True)
        info = await asyncio.to_thread(snapshots.stage_url, tenant, run["clone_url"], run["sha"])
        if info.get("contract_source") != "repository" or info.get("contract_errors"):
            await _outcome(svc, delivery_id, "skipped: the repository has no valid autopilot.yaml")
            return
        extra = {"trigger": {"kind": "github", "workflow": run["workflow"], "run_url": run["run_url"],
                             "branch": run["branch"], "sha": run["sha"]}}
        if link.auto_pr and link.installation_id:
            extra["auto_pr"] = {"repo": run["repo"], "installation_id": link.installation_id,
                                "base_branch": run["branch"]}
        task = await create_task(tenant, info["id"], f"CI failed: {run['workflow']} on {run['branch']}", extra)
        await _outcome(svc, delivery_id, "task created", task["id"])
    except ops.Denied as e:
        await _outcome(svc, delivery_id, f"not admitted: {e}")
    except Exception as e:  # noqa: BLE001 - the outcome row is the only place this can be reported
        await _outcome(svc, delivery_id, f"failed: {type(e).__name__}: {str(e)[:200]}")


# ── draft PR with an installation token (repair-worker) ───────────────────────
def _private_key() -> str:
    return settings.GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")


def app_jwt(now: int | None = None) -> str:
    if not (settings.GITHUB_APP_ID and settings.GITHUB_APP_PRIVATE_KEY):
        raise AppError("the GitHub App is not configured")
    now = int(now or time.time())
    return jwt.encode({"iat": now - 60, "exp": now + 540, "iss": settings.GITHUB_APP_ID}, _private_key(),
                      algorithm="RS256")


def installation_token(installation_id: int, repo: str, transport: httpx.BaseTransport | None = None) -> str:
    """A one-hour token limited to this repository, contents + pull requests write."""
    h = {"Authorization": f"Bearer {app_jwt()}", "Accept": "application/vnd.github+json",
         "X-GitHub-Api-Version": "2022-11-28"}
    with httpx.Client(timeout=20, transport=transport) as c:
        r = c.post(f"{API}/app/installations/{int(installation_id)}/access_tokens", headers=h,
                   json={"repositories": [repo.split("/", 1)[1]],
                         "permissions": {"contents": "write", "pull_requests": "write"}})
    if r.status_code != 201:
        raise AppError(f"GitHub refused an installation token: HTTP {r.status_code}")
    return r.json()["token"]


async def deliver_if_requested(svc, task_id: str, tenant: str, transport: httpx.BaseTransport | None = None
                               ) -> dict[str, Any] | None:
    """After a run: open the draft PR a GitHub-triggered, VERIFIED task asked for. Idempotent."""
    import asyncio

    from app.repair import delivery
    row = await svc.row(task_id, tenant)
    spec, rep = row["spec"] or {}, row["report"] or {}
    want = spec.get("auto_pr")
    if not want or rep.get("decision") != "VERIFIED" or rep.get("pull_request"):
        return None
    fin = next(r for r in rep["candidates"] if r["candidate"]["candidate_id"] == rep["finalist"])
    task = await svc.get(task_id, tenant)

    def go() -> dict:
        tok = installation_token(want["installation_id"], want["repo"], transport)
        return delivery.create_draft_pr(token=tok, repo=want["repo"], base_sha=spec["upstream_sha"],
                                        base_branch=want["base_branch"], snapshot_dir=spec["snapshot_dir"],
                                        diff=fin["candidate"]["diff"], body=delivery.pr_body(task, rep),
                                        title=f"AutoML Autopilot: {task['target'][:80]}", transport=transport)
    try:
        pr = await asyncio.to_thread(go)
    except (AppError, delivery.DeliveryError) as e:
        pr = {"error": str(e)}
    await svc.annotate(task_id, tenant, "pull_request", pr)
    return pr
