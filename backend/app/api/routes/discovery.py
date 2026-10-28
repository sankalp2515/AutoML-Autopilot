"""Discovery Agent API (ask-mode permission gate).

GET  /api/v1/discovery                → all proposals (pending/approved/rejected/blocked)
POST /api/v1/discovery/{pkg}/approve  → stage the pinned requirement for the host rebuild
POST /api/v1/discovery/{pkg}/reject   → dismiss (the scout won't re-nag)

Approval is admin-guarded like the Settings panel: nothing about the execution
environment changes without an explicit human decision, and the actual install
happens on the HOST (scripts/apply_discovery.ps1 rebuild) — never at runtime.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from app.config import settings
from app.core import discovery

router = APIRouter(prefix="/api/v1/discovery", tags=["discovery"])


def _require_admin(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    if settings.API_KEY:
        if x_api_key != settings.API_KEY:
            raise HTTPException(status_code=401, detail="Admin API key required (X-API-Key)")
        return {"warning": None}
    return {"warning": "Discovery approvals are UNPROTECTED — set an Admin API key before exposing this server."}


@router.get("")
async def list_proposals() -> dict[str, Any]:
    return {
        "proposals": discovery.list_proposals(),
        "installed": sorted(discovery.installed_packages()),
        "apply_script": "scripts/apply_discovery.ps1",
    }


@router.post("/{pkg}/approve")
async def approve(pkg: str, admin: dict = Depends(_require_admin)) -> dict[str, Any]:
    try:
        proposal = discovery.decide(pkg.lower(), approve=True)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return {"proposal": proposal, **admin}


@router.post("/{pkg}/reject")
async def reject(pkg: str, admin: dict = Depends(_require_admin)) -> dict[str, Any]:
    try:
        proposal = discovery.decide(pkg.lower(), approve=False)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"proposal": proposal, **admin}
