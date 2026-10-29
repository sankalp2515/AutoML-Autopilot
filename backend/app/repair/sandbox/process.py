"""Unisolated subprocess runner for tests and local development only.

It copies the repository into a temp folder and runs commands with the current Python
interpreter. There is no network or filesystem isolation, so it refuses to start unless
`allow_unisolated=True`, and every receipt names the runner it came from.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from app.repair.contracts import safe_relpath
from app.repair.sandbox.base import SCRATCH, RunResult, SandboxError, Upload

HEAD, TAIL = 4_000, 12_000
_ENV_KEEP = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT", "LANG", "LC_ALL")


def cap(text: str) -> tuple[str, bool]:
    if len(text) <= HEAD + TAIL:
        return text, False
    return text[:HEAD] + f"\n… [{len(text) - HEAD - TAIL} characters omitted] …\n" + text[-TAIL:], True


def _kill_tree(p: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, timeout=15)
    else:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    p.kill()


class ProcessRunner:
    name = "process-unisolated"
    isolated = False

    def __init__(self, root: str | None = None, *, allow_unisolated: bool = False) -> None:
        if not allow_unisolated:
            raise SandboxError("process runner is not isolated; use it only in tests or local development")
        self.root = Path(root or tempfile.mkdtemp(prefix="ap-envs-"))
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir(self, env_id: str) -> Path:
        d = self.root / env_id
        if not d.is_dir():
            raise SandboxError(f"unknown environment {env_id}")
        return d

    def _path(self, env_id: str, rel: str) -> Path:
        base = self._dir(env_id).resolve()
        p = (base / safe_relpath(rel)).resolve()
        if base != p and base not in p.parents:
            raise SandboxError(f"path escapes the environment: {rel}")
        return p

    def prepare(self, repo_dir: str, *, python: str, install: str, timeout_s: int) -> str:
        env_id = uuid.uuid4().hex[:12]
        dest = self.root / env_id
        shutil.copytree(repo_dir, dest, ignore=shutil.ignore_patterns(
            ".git", "__pycache__", ".pytest_cache", SCRATCH))
        (dest / SCRATCH / "artifacts").mkdir(parents=True)
        return env_id      # dependencies come from the current interpreter; `install` is not run

    def fork(self, env_id: str) -> str:
        new = uuid.uuid4().hex[:12]
        shutil.copytree(self._dir(env_id), self.root / new)
        return new

    def write_files(self, env_id: str, files: dict[str, bytes | None]) -> None:
        for rel, content in files.items():
            p = self._path(env_id, rel)
            if content is None:
                p.unlink(missing_ok=True)
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(content)

    def read_file(self, env_id: str, path: str, max_bytes: int = 2_000_000) -> bytes:
        p = self._path(env_id, path)
        if not p.is_file():
            raise FileNotFoundError(path)
        with p.open("rb") as f:
            return f.read(max_bytes)

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        cwd = self._dir(env_id)
        for u in uploads or []:
            self.write_files(env_id, {u.dest: u.content})
        argv = [sys.executable if i == 0 and a == "python" else a for i, a in enumerate(argv)]
        penv = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
        penv.update({"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(cwd),
                     "PYTHONIOENCODING": "utf-8"})
        penv.update(env or {})
        kw = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
        t0 = time.monotonic()
        try:
            p = subprocess.Popen(argv, cwd=cwd, env=penv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace", **kw)
        except OSError as e:
            raise SandboxError(f"cannot start {argv[0]}: {e}") from e
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_tree(p)
            out, err = p.communicate()
        ms = int((time.monotonic() - t0) * 1000)
        so, t1 = cap(out or "")
        se, t2 = cap(err or "")
        arts = {}
        for rel in collect or []:
            f = self._path(env_id, rel)
            if f.is_file():
                arts[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
        return RunResult(exit_code=-1 if timed_out else p.returncode, timed_out=timed_out, duration_ms=ms,
                         stdout=so, stderr=se, truncated=t1 or t2, artifacts=arts, runner=self.name)

    def discard(self, env_id: str) -> None:
        shutil.rmtree(self.root / env_id, ignore_errors=True)
