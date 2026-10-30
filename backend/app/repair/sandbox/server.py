"""sandbox-runner service (BE-07): the only process allowed to start candidate containers.

Run: uvicorn app.repair.sandbox.server:app --port 8200
It needs the Docker CLI and socket and the REPAIR_ENV_ROOT path mounted at the same path
on the host (Docker-out-of-Docker bind mounts resolve on the host). It holds no provider
keys and no labels. Every request needs the shared bearer token.
"""
from __future__ import annotations

import base64
import hmac
import io
import tarfile
import tempfile
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.repair.sandbox.base import SandboxError, Upload
from app.repair.sandbox.factory import local_runner

app = FastAPI(title="AutoML Autopilot sandbox-runner", docs_url=None, redoc_url=None)
_runner = None


def runner():
    global _runner
    if _runner is None:
        _runner = local_runner()
    return _runner


def auth(authorization: str = Header("")) -> None:
    token = settings.REPAIR_SANDBOX_TOKEN
    if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "unauthorized")


class Prepare(BaseModel):
    archive: str
    python: str = Field(pattern=r"^3\.\d{1,2}$")
    install: str
    timeout_s: int = Field(ge=10, le=1800)


class EnvRef(BaseModel):
    env_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")


class Write(EnvRef):
    files: dict[str, str | None]


class Read(EnvRef):
    path: str
    max_bytes: int = Field(default=2_000_000, le=20_000_000)


class Run(EnvRef):
    argv: list[str] = Field(min_length=1, max_length=64)
    timeout_s: int = Field(ge=1, le=1800)
    collect: list[str] = Field(default_factory=list, max_length=20)
    env: dict[str, str] = Field(default_factory=dict)
    uploads: list[dict] = Field(default_factory=list, max_length=200)


def _err(fn):
    try:
        return fn()
    except FileNotFoundError as e:
        raise HTTPException(404, str(e)) from e
    except SandboxError as e:
        raise HTTPException(502, str(e)) from e
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.get("/health")
def health() -> dict:
    r = runner()
    return {"ok": True, "runner": r.name, "isolated": r.isolated}


@app.post("/prepare", dependencies=[Depends(auth)])
def prepare(body: Prepare) -> dict:
    raw = base64.b64decode(body.archive)
    if len(raw) > settings.REPAIR_MAX_REPO_MB * 1024 * 1024:
        raise HTTPException(413, "repository archive too large")
    with tempfile.TemporaryDirectory(prefix="ap-in-") as tmp:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
            for m in tar.getmembers():
                if not m.isfile() or m.name.startswith(("/", "..")) or ".." in Path(m.name).parts:
                    raise HTTPException(422, f"unsafe archive member {m.name!r}")
            tar.extractall(tmp, filter="data")
        env_id = _err(lambda: runner().prepare(tmp, python=body.python, install=body.install, timeout_s=body.timeout_s))
    return {"env_id": env_id, "runner": runner().name, "image": getattr(runner(), "image", None)}


@app.post("/fork", dependencies=[Depends(auth)])
def fork(body: EnvRef) -> dict:
    return {"env_id": _err(lambda: runner().fork(body.env_id))}


@app.post("/write", dependencies=[Depends(auth)])
def write(body: Write) -> dict:
    files = {k: None if v is None else base64.b64decode(v) for k, v in body.files.items()}
    _err(lambda: runner().write_files(body.env_id, files))
    return {"ok": True}


@app.post("/inventory", dependencies=[Depends(auth)])
def inventory(body: EnvRef) -> dict:
    method=getattr(runner(),"inventory",None)
    if not callable(method):raise HTTPException(404,"inventory capability unavailable")
    return {"paths":_err(lambda:method(body.env_id))}


@app.post("/read", dependencies=[Depends(auth)])
def read(body: Read) -> dict:
    data = _err(lambda: runner().read_file(body.env_id, body.path, body.max_bytes))
    return {"content": base64.b64encode(data).decode()}


@app.post("/run", dependencies=[Depends(auth)])
def run(body: Run) -> dict:
    ups = [Upload(u["dest"], base64.b64decode(u["content"])) for u in body.uploads]
    res = _err(lambda: runner().run(body.env_id, body.argv, timeout_s=body.timeout_s, uploads=ups,
                                    collect=body.collect, env=body.env))
    return asdict(res)


@app.post("/discard", dependencies=[Depends(auth)])
def discard(body: EnvRef) -> dict:
    runner().discard(body.env_id)
    return {"ok": True}
