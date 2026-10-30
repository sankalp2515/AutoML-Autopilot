"""Pinned Git admission. Repository contents are data and are never imported."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import tempfile
import unicodedata
from urllib.parse import urlsplit

import yaml

from app.repair.contracts import RepoContract, canonical_digest


class IntakeError(ValueError):
    pass


def sensitive_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return any(part.casefold() in {".git", "sealed", "_ap"}
               or part.casefold().startswith(".env") for part in parts)


def validate_path(path: str, *, root_ok: bool = False) -> str:
    if root_ok and path in (".", "./", ""):
        return "."
    if not path or any(c in path for c in ('\\', '\x00', ':', '~', '<', '>', '"', '|', '?', '*')):
        raise IntakeError("path must be repository-relative POSIX text")
    if path.startswith("/") or unicodedata.normalize("NFC", path) != path:
        raise IntakeError("absolute or non-canonical path")
    parts = path.rstrip("/").split("/")
    reserved = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)),
                *(f"lpt{i}" for i in range(1, 10))}
    for part in parts:
        if part in ("", ".", "..") or part.endswith((".", " ")):
            raise IntakeError("path traversal or Windows alias")
        if part.split(".")[0].casefold() in reserved or any(ord(c) < 32 for c in part):
            raise IntakeError("reserved path component")
    if sensitive_path(path):
        raise IntakeError("secret or platform-only path; use repository source files")
    return "/".join(parts)


def jailed_path(root: Path, path: str, *, root_ok: bool = False) -> Path:
    relative = validate_path(path, root_ok=root_ok)
    current = root
    if relative != ".":
        for part in relative.split("/"):
            current = current / part
            if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
                raise IntakeError("symlinks and junctions are not permitted")
    if not current.resolve().is_relative_to(root.resolve()):
        raise IntakeError("path escapes the environment")
    return current


def git(argv: list[str], *, cwd: Path | None = None, timeout_s: int = 120,
        index_file: Path | None = None) -> bytes:
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_LFS_SKIP_SMUDGE": "1"})
    if index_file is not None:
        env["GIT_INDEX_FILE"] = str(index_file)
    try:
        result = subprocess.run(
            ["git", "-c", "core.hooksPath=" + os.devnull, "-c", "core.autocrlf=false",
             "-c", "core.attributesFile=" + os.devnull, "-c", "core.fsmonitor=false",
               "-c", "http.followRedirects=false", "-c", "protocol.ext.allow=never",
             "-c", "uploadpack.packObjectsHook=", *argv],
            cwd=cwd, env=env, capture_output=True, timeout=timeout_s, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise IntakeError("Git unavailable or operation timed out") from exc
    if result.returncode:
        # Git errors may contain credentials or host paths; do not expose them.
        raise IntakeError("Git operation failed; check source access and the exact commit SHA")
    return result.stdout


@dataclass(frozen=True)
class FileInfo:
    path: str
    size: int
    is_test: bool


def pytest_file(path: str) -> bool:
    """pytest's default `python_files` patterns: test_*.py and *_test.py."""
    name = PurePosixPath(path.casefold()).name
    return name.endswith(".py") and (name.startswith("test_") or name.endswith("_test.py"))


def test_path(path: str) -> bool:
    pp = PurePosixPath(path.casefold())
    return bool({"test", "tests"}.intersection(pp.parts)) or (
        pp.name.startswith("test_") or pp.name.endswith("_test.py"))


@dataclass(frozen=True)
class RepoSnapshot:
    directory: Path
    commit_sha: str
    digest: str
    contract: RepoContract
    files: tuple[FileInfo, ...]
    entrypoints: dict[str, str]


def _tracked(directory: Path, sha: str, timeout_s: int) -> list[tuple[str, str]]:
    records = git(["ls-tree", "-rz", "--full-tree", sha], cwd=directory, timeout_s=timeout_s)
    paths, seen = [], set()
    for record in records.split(b"\x00"):
        if not record:
            continue
        metadata, name = record.split(b"\t", 1)
        mode, kind, _ = metadata.decode("ascii").split()
        path = validate_path(name.decode("utf-8"))
        if kind != "blob" or mode not in ("100644", "100755"):
            raise IntakeError("symlinks, submodules and special files are unsupported")
        if path.casefold() in seen:
            raise IntakeError("case-colliding tracked paths are unsupported")
        seen.add(path.casefold())
        paths.append((path, mode))
    if len(paths) > 10_000:
        raise IntakeError("repository exceeds the 10000-file admission limit")
    return sorted(paths)


def verify_snapshot(root: Path, sha: str, expected_digest: str | None = None) -> str:
    """Recheck admitted bytes, including rejection of untracked executable additions."""
    root=Path(root)
    if any(p.is_symlink() for p in root.rglob("*") if ".git" not in p.relative_to(root).parts):
        raise IntakeError("snapshot contains a symlink")
    tracked=_tracked(root,sha,30)
    wanted={path for path,_ in tracked}
    actual={p.relative_to(root).as_posix() for p in root.rglob("*")
            if p.is_file() and ".git" not in p.relative_to(root).parts}
    if actual != wanted:
        raise IntakeError("snapshot has missing or untracked files")
    manifest=[{"path":path,"mode":mode,"sha256":hashlib.sha256(jailed_path(root,path).read_bytes()).hexdigest()}
              for path,mode in tracked]
    digest=canonical_digest(manifest)
    if expected_digest is not None and digest!=expected_digest:
        raise IntakeError("snapshot digest mismatch")
    return digest


def intake_repository(source: str, sha: str, snapshot_dir: str | Path, *, timeout_s: int = 120) -> RepoSnapshot:
    """Clone without checkout, validate tracked names, then materialize one exact SHA.

    Untracked local files never enter the snapshot. Existing destinations are never
    overwritten. Neither repository code, hooks nor submodules are executed.
    """
    if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
        raise IntakeError("an exact 40-character commit SHA is required")
    sha = sha.lower()
    parsed = urlsplit(source)
    local = Path(source)
    if local.is_dir():
        # Reject sensitive tracked names before cloning local objects.
        _tracked(local.resolve(), sha, timeout_s)
        source = str(local.resolve())
    elif not (parsed.scheme == "https" and parsed.hostname and not parsed.username
              and not parsed.password and not parsed.query and not parsed.fragment):
        raise IntakeError("source must be a local Git directory or credential-free HTTPS URL")
    destination = Path(snapshot_dir).absolute()
    if destination.exists() or destination.is_symlink():
        raise IntakeError("snapshot destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix="repair-intake-", dir=destination.parent))
    clone = temporary / "repo"
    try:
        git(["clone", "--no-checkout", "--no-local", "--", source, str(clone)], timeout_s=timeout_s)
        actual = git(["rev-parse", "--verify", sha + "^{commit}"], cwd=clone,
                     timeout_s=timeout_s).decode().strip()
        if actual != sha:
            raise IntakeError("requested object is not the exact commit")
        tracked = _tracked(clone, sha, timeout_s)
        git(["checkout", "--detach", "--force", sha], cwd=clone, timeout_s=timeout_s)
        files, manifest, total = [], [], 0
        for relative, mode in tracked:
            path = jailed_path(clone, relative)
            size = path.stat().st_size
            total += size
            if size > 20_000_000 or total > 200_000_000:
                raise IntakeError("repository exceeds admission byte limits")
            data = path.read_bytes()
            manifest.append({"path": relative, "mode": mode, "sha256": hashlib.sha256(data).hexdigest()})
            files.append(FileInfo(relative, size, test_path(relative)))
        try:
            contract = RepoContract.from_yaml(jailed_path(clone, "autopilot.yaml").read_text(encoding="utf-8"))
        except (ValueError, yaml.YAMLError) as exc:
            raise IntakeError("autopilot.yaml is not a valid repository contract") from exc
        for entrypoint in contract.entrypoints.values():
            module = entrypoint.split(":")[0].replace(".", "/")
            if not ((clone / (module + ".py")).is_file() or (clone / module / "__init__.py").is_file()):
                raise IntakeError("declared entrypoint module is absent")
        clone.rename(destination)
        return RepoSnapshot(destination, sha, canonical_digest(manifest), contract,
                            tuple(files), dict(contract.entrypoints))
    finally:
        # Only remove this function's verified temporary sibling; no computed
        # cleanup target may escape the requested destination's parent.
        if temporary.resolve().parent == destination.parent.resolve() and temporary.name.startswith("repair-intake-"):
            def writable(function, path, _error):
                os.chmod(path, stat.S_IWRITE)
                function(path)
            shutil.rmtree(temporary, onerror=writable)
