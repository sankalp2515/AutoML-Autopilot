"""Settings control panel — read + edit a curated set of runtime config.

GET  /api/v1/config        → current editable fields (secrets masked)
PATCH /api/v1/config       → apply changes (live), persist overlay

Writes are guarded by the admin ``API_KEY`` (X-API-Key header) when one is set.
When no API_KEY is configured (pure local dev) writes are allowed but the
response carries a warning so the UI can surface that the panel is unprotected.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel

from app.config import settings
from app.core import runtime_config

router = APIRouter(prefix="/api/v1/config", tags=["config"])


def _require_admin(x_api_key: str | None = Header(default=None)) -> dict[str, Any]:
    if settings.API_KEY:
        if x_api_key != settings.API_KEY:
            raise HTTPException(status_code=401, detail="Admin API key required (X-API-Key)")
        return {"warning": None}
    return {"warning": "Config writes are UNPROTECTED — set an Admin API key before exposing this server."}


class ConfigPatch(BaseModel):
    changes: dict[str, Any]


@router.get("")
async def get_config() -> dict[str, Any]:
    return {
        "fields": runtime_config.describe(),
        "groups": runtime_config.GROUPS,
        "api_key_protected": bool(settings.API_KEY),
    }


@router.patch("")
async def patch_config(body: ConfigPatch, ctx: dict[str, Any] = Depends(_require_admin)) -> dict[str, Any]:
    result = runtime_config.update(body.changes)
    result["warning"] = ctx.get("warning")
    return result
