"""Local mirror of a sandbox work directory (fixes BUG-03).

The agent's file tools need a real directory. Instead of reaching into the sandbox host,
each candidate gets a local mirror of the repository snapshot. Before every command the
mirror's changes are pushed into the sandbox with `write_files`, so the same code works
with the local Docker runner, the remote sandbox-runner service and Token Factory
Sandboxes. Nothing is ever pulled back from the sandbox into the mirror.
"""
from __future__ import annotations

import hashlib
import shutil
import tempfile
from pathlib import Path

from app.repair.sandbox.base import SCRATCH, RunResult, SandboxRunner, Upload

_SKIP = {"__pycache__", ".pytest_cache", ".git", SCRATCH}


def _hashes(root: Path) -> dict[str, str]:
    out = {}
    for p in root.rglob("*"):
        if p.is_file() and not p.is_symlink() and not (set(p.relative_to(root).parts) & _SKIP):
            out[p.relative_to(root).as_posix()] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


class MirroredEnv:
    """One candidate: a local mirror plus the sandbox environment it is synced into."""

    def __init__(self, runner: SandboxRunner, env_id: str, base_dir: str, root: str | None = None):
        self.runner, self.env_id = runner, env_id
        self.dir = Path(tempfile.mkdtemp(prefix="ap-mirror-", dir=root))
        shutil.rmtree(self.dir)
        shutil.copytree(base_dir, self.dir, ignore=shutil.ignore_patterns(*_SKIP))
        self._synced = _hashes(self.dir)

    def sync(self) -> int:
        now = _hashes(self.dir)
        changes: dict[str, bytes | None] = {p: (self.dir / p).read_bytes() for p, h in now.items()
                                            if self._synced.get(p) != h}
        changes.update({p: None for p in self._synced if p not in now})
        if changes:
            self.runner.write_files(self.env_id, changes)
        self._synced = now
        return len(changes)

    def close(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class SyncingRunner:
    """Runner proxy used by the agent's tools: every run first syncs the mirror."""

    def __init__(self, inner: SandboxRunner, mirror: MirroredEnv):
        self.inner, self.mirror = inner, mirror
        self.name, self.isolated = inner.name, inner.isolated

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        self.mirror.sync()
        return self.inner.run(env_id, argv, timeout_s=timeout_s, uploads=uploads, collect=collect, env=env)

    def __getattr__(self, item):
        return getattr(self.inner, item)
