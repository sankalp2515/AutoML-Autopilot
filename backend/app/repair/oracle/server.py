"""Verifier service v5 endpoints (BE-08), mounted into the existing verifier app.

The verifier container holds sealed bundles, evaluation packs, admission records and the
holdout ledger, and asks the sandbox-runner to execute candidates. It has no provider keys.
"""
from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.repair.oracle.grading import Grader, GradeRef, GradingError
from app.repair.sandbox.base import SandboxError
from app.repair.sandbox.factory import make_runner

router = APIRouter(prefix="/v5/oracle")
_grader: Grader | None = None


def grader() -> Grader:
    global _grader
    if _grader is None:
        _grader = Grader(make_runner())
    return _grader


def auth(authorization: str = Header("")) -> None:
    token = settings.REPAIR_VERIFIER_TOKEN
    if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "unauthorized")


class Ref(BaseModel):
    task_id: str
    ecc: dict
    snapshot_dir: str
    sealed_ref: str | None = None


class Grade(Ref):
    candidate_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,36}$")
    diff: str = Field(max_length=500_000)
    final: bool = False


def _ref(b: Ref) -> GradeRef:
    return GradeRef(b.task_id, b.ecc, b.snapshot_dir, b.sealed_ref)


def _guard(fn):
    try:
        return fn()
    except GradingError as e:
        raise HTTPException(409, str(e)) from e
    except SandboxError as e:
        raise HTTPException(502, str(e)) from e


@router.post("/baseline", dependencies=[Depends(auth)])
def baseline(body: Ref) -> dict:
    b = _guard(lambda: grader().baseline(_ref(body)))
    return {"outcomes": b.outcomes, "parsed": b.parsed}


@router.post("/code", dependencies=[Depends(auth)])
def code(body: Grade) -> dict:
    return _guard(lambda: grader().code(_ref(body), body.candidate_id, body.diff)).model_dump(mode="json")


@router.post("/ml", dependencies=[Depends(auth)])
def ml(body: Grade) -> dict:
    return _guard(lambda: grader().ml(_ref(body), body.candidate_id, body.diff, final=body.final)).model_dump(mode="json")
