"""Local mirror of a sandbox work directory (fixes BUG-03).

The agent's file tools need a real directory. Instead of reaching into the sandbox host,
each candidate gets a local mirror of the repository snapshot. Before every command the
mirror's changes are pushed into the sandbox with `write_files`, so the same code works
with the local Docker runner, the remote sandbox-runner service and Token Factory
Sandboxes. File tools refresh the bounded local cache through runner calls before
each action; candidate commands may have changed its source.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from pathlib import Path

from app.repair.sandbox.base import SCRATCH, RunResult, SandboxRunner, Upload
from app.repair.sandbox.base import SandboxError
from app.repair.intake import validate_path, jailed_path, sensitive_path

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

    def pull(self) -> int:
        """Refresh the controller cache through runner RPCs, without a host work path.

        Candidate commands may have changed source since the previous tool call.
        A hermetic stdlib adapter enumerates regular files only; the controller
        revalidates every returned path before accepting it into its own cache.
        """
        out = f"{SCRATCH}/file-map.json"
        adapter = b'''import json, os, stat
files = []
for directory, dirs, names in os.walk(".", followlinks=False):
    dirs[:] = [d for d in dirs if d not in {".git", "_ap", "sealed", "__pycache__", ".pytest_cache"} and not d.startswith(".env") and not os.path.islink(os.path.join(directory,d))]
    for name in names:
        path = os.path.join(directory, name)
        if name.startswith(".env") or os.path.islink(path): continue
        info = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1: continue
        files.append(os.path.relpath(path, ".").replace(os.sep, "/"))
        if len(files) > 10000: raise ValueError("file limit")
with open("_ap/file-map.json", "w", encoding="utf-8") as f: json.dump(sorted(files), f)
'''
        result = self.runner.run(self.env_id, ["python", "-I", "-S", f"{SCRATCH}/file-map.py"],
                                 timeout_s=30, uploads=[Upload(f"{SCRATCH}/file-map.py", adapter)], collect=[out])
        if result.exit_code or result.timed_out:
            raise SandboxError("sandbox file inventory failed")
        try:
            paths = json.loads(self.runner.read_file(self.env_id, out))
            if not isinstance(paths, list) or len(paths) > 10000:
                raise ValueError()
            now = set()
            total = 0
            for path in paths:
                validate_path(path)
                if sensitive_path(path) or set(Path(path).parts) & _SKIP:
                    raise ValueError("reserved sandbox file")
                raw = self.runner.read_file(self.env_id, path, max_bytes=2_000_001)
                total += len(raw)
                if len(raw) > 2_000_000 or total > 100_000_000:
                    raise ValueError("source cache limit")
                target = jailed_path(self.dir, path)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
                now.add(path)
            for path in set(_hashes(self.dir)) - now:
                jailed_path(self.dir, path).unlink()
            self._synced = _hashes(self.dir)
            return len(now)
        except (ValueError, TypeError, FileNotFoundError) as exc:
            raise SandboxError("sandbox returned an invalid source inventory") from exc

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
