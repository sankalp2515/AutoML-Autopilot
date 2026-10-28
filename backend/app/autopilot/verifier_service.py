"""Verifier service: the verification plane as its own container.

    uvicorn app.autopilot.verifier_service:app --port 8100

Separate write authority (v4.1): only this service reads final-test rows and produces
verification records. It runs with the artifact volume mounted read-only, on an
internal network with no internet route, holds no model or search provider keys, and
contains no language model. Callers send a run id (never a file path) and a token.
"""
from __future__ import annotations

import hmac
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.autopilot.runner import _verify_job
from app.autopilot.verifier import VERIFIER_VERSION
from app.config import settings

_RUN_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
app = FastAPI(title="AutoML Autopilot verifier", version=VERIFIER_VERSION, docs_url=None, redoc_url=None)


class VerifyRequest(BaseModel):
    run_id: str
    candidate: dict[str, Any]
    self_report: dict[str, Any] = Field(default_factory=dict)
    mode: str = Field(pattern="^(improve|non_inferior)$")
    expected_protocol_hash: str
    declared_split: str = "same"
    may_open: bool


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "verifier", "version": VERIFIER_VERSION}


@app.post("/verify")
def verify(req: VerifyRequest, x_verifier_token: str | None = Header(default=None)) -> dict:
    expected = settings.AUTOPILOT_VERIFIER_TOKEN
    if not expected or not x_verifier_token or not hmac.compare_digest(expected, x_verifier_token):
        raise HTTPException(401, "invalid verifier token")
    if not _RUN_ID.match(req.run_id):
        raise HTTPException(422, "run_id must be a UUID")
    ws_root = Path(settings.DATA_DIR) / "autopilot" / req.run_id
    if not (ws_root / "inputs" / "protocol.json").exists():
        raise HTTPException(404, "unknown investigation workspace")
    model_path = str(req.candidate.get("model_path", ""))
    # Candidate artifacts must live inside this run's workspace.
    if not Path(model_path).resolve().is_relative_to((ws_root / "models").resolve()):
        raise HTTPException(422, "candidate artifact outside the investigation workspace")
    kwargs = dict(candidate=req.candidate, self_report=req.self_report, mode=req.mode,
                  expected_protocol_hash=req.expected_protocol_hash, declared_split=req.declared_split)
    return _verify_job(str(ws_root), kwargs, req.may_open)
