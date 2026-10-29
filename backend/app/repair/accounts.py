"""Accounts and teams (BE-23): GitHub sign-in, workspaces and roles.

Identity only: the GitHub OAuth token is used once to read the profile and then dropped, so a
leaked database never holds GitHub credentials (draft PRs use a token the user gives per PR, or
the GitHub App). Sessions are short signed tokens (HS256, REPAIR_SESSION_SECRET).

Tenancy: a signed-in request acts inside one workspace (X-Workspace header, default the
account's personal workspace) and every v5 record is scoped to the tenant "ws:<id>".
Without a session token the caller stays the anonymous per-browser tenant, so the public demo
keeps working. Roles: viewer (read) < member (run tasks, stage repos) < admin (members,
GitHub links) < owner (everything; a workspace always keeps at least one owner).
"""
from __future__ import annotations

import secrets
import time
import uuid
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select

from app.autopilot.routes import autopilot_tenant
from app.config import settings
from app.repair import models as m

ROLES = {"viewer": 0, "member": 1, "admin": 2, "owner": 3}
_AUD, _STATE_AUD = "autopilot-session", "autopilot-oauth-state"


class AccountError(ValueError):
    pass


class Forbidden(PermissionError):
    pass


def enabled() -> bool:
    return bool(settings.REPAIR_SESSION_SECRET)


def _sign(claims: dict, seconds: int, aud: str) -> str:
    now = int(time.time())
    return jwt.encode({**claims, "aud": aud, "iat": now, "exp": now + seconds},
                      settings.REPAIR_SESSION_SECRET, algorithm="HS256")


def _read(token: str, aud: str) -> dict | None:
    if not enabled() or not token:
        return None
    try:
        return jwt.decode(token, settings.REPAIR_SESSION_SECRET, algorithms=["HS256"], audience=aud)
    except jwt.PyJWTError:
        return None


def issue_session(account_id: str, login: str) -> str:
    return _sign({"sub": account_id, "login": login}, settings.REPAIR_SESSION_HOURS * 3600, _AUD)


def read_session(token: str | None) -> dict | None:
    return _read(token or "", _AUD)


# ── GitHub OAuth ──────────────────────────────────────────────────────────────
def oauth_start() -> dict:
    if not (enabled() and settings.GITHUB_OAUTH_CLIENT_ID):
        raise AccountError("GitHub sign-in is not configured on this server")
    state = _sign({"n": secrets.token_urlsafe(16)}, 600, _STATE_AUD)    # stateless CSRF guard
    q = {"client_id": settings.GITHUB_OAUTH_CLIENT_ID, "scope": "read:user", "state": state,
         "allow_signup": "true"}
    if settings.GITHUB_OAUTH_REDIRECT_URL:
        q["redirect_uri"] = settings.GITHUB_OAUTH_REDIRECT_URL
    return {"url": "https://github.com/login/oauth/authorize?" + urlencode(q), "state": state}


def github_profile(code: str, state: str, transport: httpx.BaseTransport | None = None) -> dict:
    """Exchange the OAuth code, read the profile, discard the GitHub token."""
    if _read(state, _STATE_AUD) is None:
        raise AccountError("sign-in expired or was not started here; try again")
    with httpx.Client(timeout=15, transport=transport) as h:
        r = h.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
                   data={"client_id": settings.GITHUB_OAUTH_CLIENT_ID,
                         "client_secret": settings.GITHUB_OAUTH_CLIENT_SECRET, "code": code})
        token = (r.json() if r.status_code == 200 else {}).get("access_token")
        if not token:
            raise AccountError("GitHub did not accept the sign-in code")
        u = h.get("https://api.github.com/user", headers={"Authorization": f"Bearer {token}",
                                                          "Accept": "application/vnd.github+json"})
    if u.status_code != 200:
        raise AccountError("could not read the GitHub profile")
    p = u.json()
    return {"id": int(p["id"]), "login": str(p["login"]), "name": p.get("name"), "avatar_url": p.get("avatar_url")}


# ── accounts and workspaces ───────────────────────────────────────────────────
def _ws_json(ws: m.Workspace, role: str) -> dict:
    return {"id": ws.id, "name": ws.name, "role": role, "tenant": f"ws:{ws.id}"}


async def sign_in(svc, profile: dict) -> dict:
    """Upsert the account; the first sign-in also creates a personal workspace it owns."""
    aid = f"gh:{profile['id']}"
    async with svc.sessions() as db, db.begin():
        acc = await db.get(m.Account, aid)
        if acc is None:
            ws = m.Workspace(id=str(uuid.uuid4()), name=f"{profile['login']}'s workspace", created_by=aid,
                             created_at=time.time())
            acc = m.Account(id=aid, login=profile["login"], name=profile.get("name"),
                            avatar_url=profile.get("avatar_url"), personal_workspace=ws.id, created_at=time.time())
            db.add_all([acc, ws])
            await db.flush()
            db.add(m.Membership(workspace_id=ws.id, account_id=aid, role="owner"))
        else:
            acc.login, acc.name, acc.avatar_url = profile["login"], profile.get("name"), profile.get("avatar_url")
    return {"token": issue_session(aid, profile["login"]), **(await me(svc, aid))}


async def me(svc, account_id: str) -> dict:
    async with svc.sessions() as db:
        acc = await db.get(m.Account, account_id)
        if acc is None:
            raise AccountError("account not found")
        rows = (await db.execute(select(m.Workspace, m.Membership.role).join(
            m.Membership, m.Membership.workspace_id == m.Workspace.id).where(
            m.Membership.account_id == account_id).order_by(m.Workspace.created_at))).all()
    return {"account": {"id": acc.id, "login": acc.login, "name": acc.name, "avatar_url": acc.avatar_url},
            "personal_workspace": acc.personal_workspace,
            "workspaces": [_ws_json(ws, role) for ws, role in rows]}


async def role_of(svc, workspace_id: str, account_id: str) -> str | None:
    async with svc.sessions() as db:
        return (await db.execute(select(m.Membership.role).where(
            m.Membership.workspace_id == workspace_id, m.Membership.account_id == account_id))).scalar_one_or_none()


async def create_workspace(svc, account_id: str, name: str) -> dict:
    ws = m.Workspace(id=str(uuid.uuid4()), name=name.strip()[:100], created_by=account_id, created_at=time.time())
    async with svc.sessions() as db, db.begin():
        db.add(ws)
        await db.flush()
        db.add(m.Membership(workspace_id=ws.id, account_id=account_id, role="owner"))
    return _ws_json(ws, "owner")


async def members(svc, workspace_id: str) -> list[dict]:
    async with svc.sessions() as db:
        rows = (await db.execute(select(m.Account, m.Membership.role).join(
            m.Membership, m.Membership.account_id == m.Account.id).where(
            m.Membership.workspace_id == workspace_id).order_by(m.Account.login))).all()
    return [{"account_id": a.id, "login": a.login, "name": a.name, "avatar_url": a.avatar_url, "role": r}
            for a, r in rows]


async def set_member(svc, workspace_id: str, actor_role: str, login: str, role: str | None) -> dict:
    """Add, change or (role=None) remove a member. Admins manage members and admins; only an
    owner grants or removes ownership; the last owner can never be removed or demoted."""
    if role is not None and role not in ROLES:
        raise AccountError(f"role must be one of {', '.join(ROLES)}")
    if ROLES[actor_role] < ROLES["admin"]:
        raise Forbidden("only admins and owners manage members")
    async with svc.sessions() as db, db.begin():
        acc = (await db.execute(select(m.Account).where(m.Account.login == login))).scalar_one_or_none()
        if acc is None:
            raise AccountError(f"{login} has not signed in to AutoML Autopilot yet")
        mem = (await db.execute(select(m.Membership).where(
            m.Membership.workspace_id == workspace_id, m.Membership.account_id == acc.id))).scalar_one_or_none()
        if "owner" in (role, mem.role if mem else None) and actor_role != "owner":
            raise Forbidden("only an owner can grant or remove ownership")
        if mem is not None and mem.role == "owner" and role != "owner":
            owners = (await db.execute(select(func.count()).select_from(m.Membership).where(
                m.Membership.workspace_id == workspace_id, m.Membership.role == "owner"))).scalar_one()
            if owners <= 1:
                raise AccountError("a workspace must keep at least one owner")
        if role is None:
            if mem is not None:
                await db.delete(mem)
            return {"login": login, "role": None}
        if mem is None:
            db.add(m.Membership(workspace_id=workspace_id, account_id=acc.id, role=role))
        else:
            mem.role = role
    return {"login": login, "role": role}


# ── request dependencies ──────────────────────────────────────────────────────
def get_service():
    """The repair service (routes re-export this, so tests override one dependency)."""
    from app.repair.service import default_service
    return default_service


@dataclass(frozen=True)
class Actor:
    tenant: str
    account_id: str | None
    role: str
    workspace_id: str | None = None

    def need(self, role: str) -> None:
        if ROLES[self.role] < ROLES[role]:
            raise HTTPException(403, f"this needs the {role} role in the workspace")


async def actor(anon: str = Depends(autopilot_tenant),
                x_autopilot_token: str | None = Header(default=None, max_length=2000),
                x_workspace: str | None = Header(default=None, max_length=36), svc=Depends(get_service)) -> Actor:
    if not x_autopilot_token:
        return Actor(tenant=anon, account_id=None, role="owner")      # anonymous: owns its own browser scope
    claims = read_session(x_autopilot_token)
    if claims is None:
        raise HTTPException(401, "session expired; sign in again")
    sub = claims["sub"]
    ws = x_workspace
    if not ws:
        try:
            ws = (await me(svc, sub))["personal_workspace"]
        except AccountError as e:
            raise HTTPException(401, "account not found; sign in again") from e
    role = await role_of(svc, ws, sub) if ws else None
    if role is None:
        raise HTTPException(404, "workspace not found")
    return Actor(tenant=f"ws:{ws}", account_id=sub, role=role, workspace_id=ws)


async def reader(a: Actor = Depends(actor)) -> str:
    return a.tenant


async def writer(a: Actor = Depends(actor)) -> str:
    a.need("member")
    return a.tenant


async def signed_in(a: Actor = Depends(actor)) -> Actor:
    if a.account_id is None:
        raise HTTPException(401, "sign in with GitHub first")
    return a
