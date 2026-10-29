"""SandboxRunner over HTTP: the worker and verifier talk to the sandbox-runner service.

Only the sandbox-runner container can start candidate containers; it holds no provider
keys and no labels. Repository snapshots are uploaded as a tar archive at `prepare`.
"""
from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

import httpx

from app.repair.sandbox.base import SCRATCH, RunResult, SandboxError, Upload


def tar_dir(path: str) -> bytes:
    buf = io.BytesIO()
    root = Path(path)
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for p in sorted(root.rglob("*")):
            rel = p.relative_to(root)
            if p.is_file() and not p.is_symlink() and not ({"__pycache__", ".git", ".pytest_cache", SCRATCH} & set(rel.parts)):
                tar.add(p, arcname=rel.as_posix(), recursive=False)
    return buf.getvalue()


class RemoteRunner:
    isolated = True

    def __init__(self, base_url: str, token: str, *, timeout_s: float = 1900.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url, self.token, self.timeout_s = base_url.rstrip("/"), token, timeout_s
        self._transport = transport
        self.name = "sandbox-service"

    def _post(self, endpoint: str, **body):
        try:
            with httpx.Client(timeout=self.timeout_s, transport=self._transport) as c:
                r = c.post(f"{self.base_url}{endpoint}", json=body, headers={"Authorization": f"Bearer {self.token}"})
        except httpx.HTTPError as e:
            raise SandboxError(f"sandbox service unreachable: {type(e).__name__}") from e
        if r.status_code == 404 and endpoint == "/read":
            raise FileNotFoundError(body.get("path"))
        if r.status_code != 200:
            raise SandboxError(f"sandbox service {endpoint}: HTTP {r.status_code} {r.text[:200]}")
        return r.json()

    def prepare(self, repo_dir: str, *, python: str, install: str, timeout_s: int) -> str:
        res = self._post("/prepare", archive=base64.b64encode(tar_dir(repo_dir)).decode(), python=python,
                         install=install, timeout_s=timeout_s)
        self.name = res.get("runner", self.name)
        self.image = res.get("image")
        return res["env_id"]

    def fork(self, env_id: str) -> str:
        return self._post("/fork", env_id=env_id)["env_id"]

    def write_files(self, env_id: str, files: dict[str, bytes | None]) -> None:
        self._post("/write", env_id=env_id, files={k: None if v is None else base64.b64encode(v).decode()
                                                   for k, v in files.items()})

    def read_file(self, env_id: str, path: str, max_bytes: int = 2_000_000) -> bytes:
        return base64.b64decode(self._post("/read", env_id=env_id, path=path, max_bytes=max_bytes)["content"])

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        res = self._post("/run", env_id=env_id, argv=argv, timeout_s=timeout_s, collect=collect or [], env=env or {},
                         uploads=[{"dest": u.dest, "content": base64.b64encode(u.content).decode()} for u in uploads or []])
        return RunResult(**res)

    def discard(self, env_id: str) -> None:
        try:
            self._post("/discard", env_id=env_id)
        except SandboxError:
            pass
