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
import shutil
import asyncio
import threading
import os
from contextlib import asynccontextmanager,contextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from app.config import settings
from app.repair.sandbox.base import SandboxError, Upload
from app.repair.sandbox.factory import local_runner

@asynccontextmanager
async def lifespan(app):
    async def maintenance():
        while True:
            await asyncio.sleep(600)
            method=getattr(runner(),"cleanup",None)
            if callable(method):
                try:await asyncio.to_thread(method,max(settings.REPAIR_RETENTION_HOURS*3600,settings.REPAIR_QUEUE_TTL_S+2400))
                except Exception:pass
    task=asyncio.create_task(maintenance())
    try:yield
    finally:
        task.cancel()
        try:await task
        except asyncio.CancelledError:pass
        global _runner,_ownership_file
        if _ownership_file is not None:_ownership_file.close();_ownership_file=None
        _runner=None


app = FastAPI(title="AutoML Autopilot sandbox-runner", docs_url=None, redoc_url=None,lifespan=lifespan)
_runner = None
_ownership_file=None
_runner_lock=threading.Lock()


def claim_root(candidate):
    stream=(candidate.root/".owner.lock").open("a+b")
    try:
        stream.write(b"0");stream.flush();stream.seek(0)
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:
        stream.close()
        raise SandboxError("another sandbox process owns this environment root; use one owner per node") from exc
    return stream


def runner():
    global _runner,_ownership_file
    if _runner is None:
        with _runner_lock:
            if _runner is None:
                candidate=local_runner()
                if callable(getattr(candidate,"recover_orphan_containers",None)):
                    handle=claim_root(candidate)
                    try:candidate.recover_orphan_containers()
                    except Exception:handle.close();raise
                    _ownership_file=handle
                _runner=candidate
    return _runner


def auth(authorization: str = Header("")) -> None:
    token = settings.REPAIR_SANDBOX_TOKEN
    if not token or not hmac.compare_digest(authorization, f"Bearer {token}"):
        raise HTTPException(401, "unauthorized")


def trusted_auth(authorization: str = Header("")) -> None:
    token=settings.REPAIR_TRUSTED_SANDBOX_TOKEN
    if not token or not hmac.compare_digest(authorization,f"Bearer {token}"):
        raise HTTPException(401,"unauthorized")


class Prepare(BaseModel):
    archive: str
    python: str = Field(pattern=r"^3\.\d{1,2}$")
    install: str
    timeout_s: int = Field(ge=10, le=1800)


class PreparePinned(BaseModel):
    archive:str
    image:str=Field(pattern=r"^sha256:[0-9a-f]{64}$")
    timeout_s:int=Field(ge=10,le=1800)


@contextmanager
def unpacked(archive):
    from app.repair.intake import validate_path,IntakeError
    raw=base64.b64decode(archive)
    limit=settings.REPAIR_MAX_REPO_MB*1024*1024
    if len(raw)>limit:raise HTTPException(413,"repository archive too large")
    with tempfile.TemporaryDirectory(prefix="ap-in-") as temporary:
        total=0;seen=set()
        with tarfile.open(fileobj=io.BytesIO(raw),mode="r|gz") as tar:
            for member in tar:
                try:name=validate_path(member.name)
                except IntakeError:raise HTTPException(422,"unsafe repository archive path") from None
                total+=member.size
                if total>limit or len(seen)>=settings.REPAIR_MAX_FILES:raise HTTPException(413,"expanded repository archive exceeds limits")
                if not member.isfile() or member.size<0 or name.casefold() in seen:raise HTTPException(422,"unsafe or duplicate repository archive member")
                seen.add(name.casefold())
                target=Path(temporary)/name;target.parent.mkdir(parents=True,exist_ok=True)
                with tar.extractfile(member) as source,target.open("wb") as destination:shutil.copyfileobj(source,destination)
        yield temporary


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
    if not settings.DEBUG:
        from app.repair.ops import validate_secrets
        try:validate_secrets("REPAIR_SANDBOX_TOKEN","REPAIR_TRUSTED_SANDBOX_TOKEN")
        except ValueError:raise HTTPException(503,"sandbox credentials require operator configuration") from None
    r = runner()
    return {"ok": True, "runner": r.name, "isolated": r.isolated}


@app.post("/prepare", dependencies=[Depends(auth)])
def prepare(body: Prepare) -> dict:
    with unpacked(body.archive) as tmp:
        env_id = _err(lambda: runner().prepare(tmp, python=body.python, install=body.install, timeout_s=body.timeout_s))
    method=getattr(runner(),"environment_image",None)
    image=method(env_id) if callable(method) else getattr(runner(),"image",None)
    return {"env_id": env_id, "runner": runner().name, "image": image}


@app.post("/prepare-pinned",dependencies=[Depends(auth)])
def prepare_pinned(body:PreparePinned):
    with unpacked(body.archive) as temporary:
        ident=_err(lambda:runner().prepare_pinned(temporary,image=body.image,timeout_s=body.timeout_s))
    return {"env_id":ident,"image":runner().environment_image(ident)}


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


@app.post("/trusted-run",dependencies=[Depends(trusted_auth)])
def trusted_run(body:Run):
    method=getattr(runner(),"trusted_run",None)
    if not callable(method):raise HTTPException(503,"external evaluator unavailable")
    uploads=[Upload(u["dest"],base64.b64decode(u["content"])) for u in body.uploads]
    result,files=_err(lambda:method(body.env_id,body.argv,uploads=uploads,timeout_s=body.timeout_s,
                                  collect=body.collect,env=body.env))
    return {"result":asdict(result),"files":{k:base64.b64encode(v).decode() for k,v in files.items()}}


@app.post("/discard", dependencies=[Depends(auth)])
def discard(body: EnvRef) -> dict:
    runner().discard(body.env_id)
    return {"ok": True}
