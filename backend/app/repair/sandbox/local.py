"""Docker CLI runner. The Docker daemon is trusted; candidate code is not.

Only dependency preparation has networking. Each execution sees its own bind
mounts, runs unprivileged, and has bounded resources and captured output. This is
container isolation, not a claim of a microVM boundary against kernel exploits.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import uuid

from .base import RunResult, SandboxError, Upload

HEAD_BYTES, TAIL_BYTES = 4096, 12288


class _Capture:
    """Drain a pipe without retaining the potentially unbounded middle."""
    def __init__(self):
        self.head = bytearray()
        self.tail = bytearray()
        self.total = 0

    def drain(self, pipe):
        try:
            while chunk := pipe.read(8192):
                self.total += len(chunk)
                take = min(HEAD_BYTES - len(self.head), len(chunk))
                self.head.extend(chunk[:take])
                self.tail.extend(chunk[take:])
                if len(self.tail) > TAIL_BYTES:
                    del self.tail[:-TAIL_BYTES]
        finally:
            pipe.close()

    @property
    def truncated(self):
        return self.total > HEAD_BYTES + TAIL_BYTES

    def text(self):
        return bytes(self.head + self.tail).decode("utf-8", errors="replace")


@dataclass
class _Environment:
    root: Path
    image: str
    lock: threading.RLock


def _relative(path: str) -> PurePosixPath:
    parts = PurePosixPath(path)
    if (not path or path.startswith("/") or "\\" in path or "\x00" in path
            or ":" in path or ".." in parts.parts or str(parts) == "."):
        raise SandboxError("path must be a nonempty repository-relative POSIX path")
    if any(part.rstrip(". ") != part or part.casefold() == ".git" or
           part.casefold() == ".env" or part.casefold().startswith(".env.") for part in parts.parts):
        raise SandboxError("metadata, secret and ambiguous filesystem paths are not allowed")
    return parts


def _path(root: Path, relative: str) -> Path:
    parts = _relative(relative)
    current = root
    for part in parts.parts:
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise SandboxError("unreadable path or unsupported symlink") from exc
        if (stat.S_ISLNK(info.st_mode)
                or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
            raise SandboxError("symlinks and junctions are not allowed")
    if not current.resolve().is_relative_to(root.resolve()):
        raise SandboxError("path escapes its environment")
    if current.exists():
        info = current.stat()
        if not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode):
            raise SandboxError("special files are not allowed")
        if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
            raise SandboxError("hard-linked files are not allowed")
    return current


def _copy_tree(source: Path, target: Path):
    """Validate before reading; never follow a repository symlink or read secrets."""
    if (source.is_symlink() or (hasattr(source, "is_junction") and source.is_junction())
            or not source.is_dir()):
        raise SandboxError("repository must be an ordinary directory")
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(0o777)

    def copy_directory(directory):
        for source_path in sorted(directory.iterdir()):
            if source_path.name in (".git", "__pycache__", ".pytest_cache"):
                continue
            relative = source_path.relative_to(source).as_posix()
            checked = _path(source, relative)  # inspect before descending into any directory
            destination = target / relative
            if checked.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
                destination.chmod(0o777)
                copy_directory(checked)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(checked, destination)
                destination.chmod(0o666 | (stat.S_IMODE(checked.stat().st_mode) & 0o111))

    copy_directory(source)


def _snapshot_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        checked = _path(root, relative)
        if not checked.is_file():
            continue
        name = relative.encode("utf-8")
        digest.update(len(name).to_bytes(8, "big"))
        digest.update(name)
        digest.update((stat.S_IMODE(checked.stat().st_mode) & 0o111).to_bytes(2, "big"))
        digest.update(checked.stat().st_size.to_bytes(8, "big"))
        with checked.open("rb") as stream:
            while block := stream.read(1024 * 1024):
                digest.update(block)
    return digest.hexdigest()


class LocalContainerRunner:
    name = "local-container"
    isolated = True

    def __init__(self, root: str | Path | None = None, *, docker: str = "docker",
                 base_image: str | None = None):
        # base_image is an explicit operator override, useful for offline tests.
        # Never take it from repository/model input. The production default is
        # always the contract's python:<version>-slim image.
        self.root = Path(root).resolve() if root else Path(tempfile.mkdtemp(prefix="ap-containers-"))
        if any(char in str(self.root) for char in (",", "\r", "\n")):
            raise SandboxError("attempt root cannot contain Docker mount separators")
        self.root.mkdir(parents=True, exist_ok=True)
        self.root.chmod(0o700)
        self.docker = docker
        self.base_image = base_image
        self._environments: dict[str, _Environment] = {}
        self._guard = threading.RLock()

    def _environment(self, env_id: str) -> _Environment:
        with self._guard:
            environment = self._environments.get(env_id)
        if environment is None or not environment.root.is_dir():
            raise SandboxError("unknown or discarded environment")
        return environment

    def work_dir(self, env_id: str) -> Path:
        """Trusted platform tools only; never serialize this host path to an agent."""
        return self._environment(env_id).root / "work"

    def _artifact_path(self, environment: _Environment, path: str) -> Path:
        work = _path(environment.root / "work", path)
        out = _path(environment.root / "out", path)
        if work.exists() and out.exists():
            raise SandboxError("ambiguous artifact path exists in both work and output directories")
        return work if work.exists() else out

    def _command(self, argv, *, timeout_s, kill_container=None, cwd=None):
        started = time.monotonic()
        try:
            process = subprocess.Popen([self.docker, *argv], cwd=cwd,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as exc:
            raise SandboxError("Docker CLI could not start") from exc
        stdout, stderr = _Capture(), _Capture()
        threads = [threading.Thread(target=capture.drain, args=(pipe,), daemon=True)
                   for capture, pipe in ((stdout, process.stdout), (stderr, process.stderr))]
        for thread in threads:
            thread.start()
        timed_out = False
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                if kill_container:
                    # Killing the client alone leaves its container running.
                    removed = subprocess.run([self.docker, "rm", "--force", kill_container],
                                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
                    if removed.returncode != 0:
                        remaining = subprocess.run(
                            [self.docker, "container", "ls", "--all", "--filter",
                             f"name=^/{kill_container}$", "--format", "{{.ID}}"],
                            capture_output=True, timeout=15)
                        if remaining.returncode != 0 or remaining.stdout.strip():
                            raise SandboxError("Docker could not confirm timeout cleanup")
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise SandboxError("Docker could not confirm timeout cleanup") from exc
            finally:
                process.kill()
                process.wait(timeout=15)
        for thread in threads:
            thread.join(timeout=15)
        if any(thread.is_alive() for thread in threads):
            raise SandboxError("Docker output stream did not close")
        return RunResult(exit_code=-1 if timed_out else process.returncode, timed_out=timed_out,
                         duration_ms=int((time.monotonic() - started) * 1000), stdout=stdout.text(),
                         stderr=stderr.text(), truncated=stdout.truncated or stderr.truncated,
                         runner=self.name)

    def prepare(self, repo_dir: str, *, python: str, install: str, timeout_s: int) -> str:
        if not re.fullmatch(r"3\.\d{1,2}", python):
            raise SandboxError("unsupported Python version")
        try:
            install_argv = shlex.split(install, posix=True)
        except ValueError as exc:
            raise SandboxError("invalid install command") from exc
        if (install_argv[:2] != ["pip", "install"] or len(install_argv) < 3
                or re.search(r"[;&|`$<>\r\n\\]", install)):
            raise SandboxError("install must be a single pip install argv command")
        if timeout_s <= 0:
            raise SandboxError("timeout must be positive")
        env_id = uuid.uuid4().hex
        environment = _Environment(self.root / env_id, "", threading.RLock())
        environment.root.mkdir(mode=0o700)
        try:
            work = environment.root / "work"
            _copy_tree(Path(repo_dir).absolute(), work)
            out = environment.root / "out"
            out.mkdir(mode=0o777)
            out.chmod(0o777)
            base = self.base_image or f"python:{python}-slim"
            key = hashlib.sha256(json.dumps([_snapshot_digest(work), python, install, base,
                                             "local-runner-v2"], separators=(",", ":")).encode()).hexdigest()
            image = f"automl-repair:{key}"
            inspected = self._command(["image", "inspect", image, "--format", "{{.Id}}"], timeout_s=15)
            if inspected.exit_code != 0:
                # Build context is platform-created; a repository Dockerfile or
                # .dockerignore can neither inject instructions nor hide inputs.
                with tempfile.TemporaryDirectory(prefix="build-", dir=self.root) as build_root:
                    build = Path(build_root)
                    _copy_tree(work, build / "repo")
                    recipe = (
                        f"FROM {base}\nUSER 0\nWORKDIR /build\nCOPY repo/ /build/\n"
                        f"RUN {json.dumps(install_argv)}\n"
                        'RUN ["python", "-I", "-S", "-c", "import shutil; shutil.rmtree(\'/build\')"]\n'
                        "WORKDIR /work\nUSER 10001\n"
                    )
                    (build / "Dockerfile").write_text(recipe, encoding="utf-8")
                    built = self._command(["build", "--tag", image, "--file", str(build / "Dockerfile"),
                                           str(build)], timeout_s=timeout_s)
                    if built.exit_code != 0:
                        raise SandboxError(f"dependency image preparation failed: {built.stderr[-2000:]}")
            environment.image = image
            with self._guard:
                self._environments[env_id] = environment
            return env_id
        except Exception:
            self._remove(environment.root)
            raise

    def fork(self, env_id: str) -> str:
        parent = self._environment(env_id)
        with parent.lock:
            child_id = uuid.uuid4().hex
            child = _Environment(self.root / child_id, parent.image, threading.RLock())
            try:
                child.root.mkdir(mode=0o700)
                _copy_tree(parent.root / "work", child.root / "work")
                _copy_tree(parent.root / "out", child.root / "out")
                with self._guard:
                    self._environments[child_id] = child
                return child_id
            except Exception:
                self._remove(child.root)
                raise

    def write_files(self, env_id: str, files: dict[str, bytes | None]) -> None:
        environment = self._environment(env_id)
        with environment.lock:
            for name, content in files.items():
                target = _path(environment.root / "work", name)
                if content is None:
                    target.unlink(missing_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    for parent in target.parents:
                        if parent == environment.root / "work":
                            break
                        parent.chmod(0o777)
                    target.write_bytes(content)
                    target.chmod(0o666)

    def read_file(self, env_id: str, path: str, max_bytes: int = 200_000) -> bytes:
        if max_bytes < 0:
            raise SandboxError("max_bytes must be nonnegative")
        environment = self._environment(env_id)
        with environment.lock:
            target = self._artifact_path(environment, path)
            with target.open("rb") as stream:
                return stream.read(max_bytes)

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        environment = self._environment(env_id)
        if not argv or not all(isinstance(arg, str) and "\x00" not in arg for arg in argv) or timeout_s <= 0:
            raise SandboxError("run requires a nonempty argv and positive timeout")
        if env and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value
                       for key, value in env.items()):
            raise SandboxError("invalid execution environment")
        with environment.lock:
            self.write_files(env_id, {upload.dest: upload.content for upload in uploads or []})
            name = f"ap-repair-{uuid.uuid4().hex}"
            cidfile = environment.root / f"{name}.cid"
            command = ["run", "--rm", "--pull", "never", "--name", name, "--cidfile", str(cidfile),
                       "--network", "none", "--user", "10001",
                       "--read-only", "--tmpfs", "/tmp:rw,nosuid,nodev,size=256m", "--cap-drop", "ALL",
                       "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "2g",
                       "--cpus", "2", "--workdir", "/work", "--mount",
                       f"type=bind,source={environment.root / 'work'},target=/work", "--mount",
                       f"type=bind,source={environment.root / 'out'},target=/out",
                       "--env", "HOME=/tmp", "--env", "PYTHONDONTWRITEBYTECODE=1"]
            for key, value in sorted((env or {}).items()):
                command.extend(["--env", f"{key}={value}"])
            command.extend(["--entrypoint", argv[0], environment.image, *argv[1:]])
            try:
                result = self._command(command, timeout_s=timeout_s, kill_container=name)
                if result.exit_code == 125 and not cidfile.exists():
                    raise SandboxError(f"Docker could not create candidate container: {result.stderr[-2000:]}")
            finally:
                cidfile.unlink(missing_ok=True)
            artifacts = {}
            for name in collect or []:
                target = self._artifact_path(environment, name)
                if target.exists():
                    if not target.is_file():
                        raise SandboxError("collected artifact must be a regular file")
                    with target.open("rb") as stream:
                        artifacts[name] = hashlib.file_digest(stream, "sha256").hexdigest()
            return RunResult(**{**result.__dict__, "artifacts": artifacts})

    def _remove(self, path):
        resolved = path.resolve()
        if resolved == self.root or not resolved.is_relative_to(self.root):
            raise SandboxError("refusing cleanup outside environment root")
        if path.is_symlink():
            path.unlink()
        else:
            shutil.rmtree(path, ignore_errors=False)

    def discard(self, env_id: str) -> None:
        with self._guard:
            environment = self._environments.get(env_id)
        if environment is None:
            return
        with environment.lock:
            if environment.root.exists():
                try:
                    self._remove(environment.root)
                except PermissionError:
                    # On native Linux, container-created directories belong to
                    # UID 10001 rather than the host developer. Clear their
                    # contents as that UID, still inside the same runtime jail.
                    for folder in ("work", "out"):
                        (environment.root / folder).mkdir(exist_ok=True)
                    cleanup = """
import os, stat
def clear(root):
    try:
        os.chmod(root, 0o777)
    except PermissionError:
        pass
    for item in os.scandir(root):
        if stat.S_ISDIR(item.stat(follow_symlinks=False).st_mode):
            clear(item.path)
            os.rmdir(item.path)
        else:
            os.unlink(item.path)
clear('/work')
clear('/out')
"""
                    result = self.run(env_id, ["python", "-I", "-S", "-c", cleanup], timeout_s=30)
                    if result.exit_code != 0:
                        raise SandboxError("could not clean UID-owned attempt files")
                    self._remove(environment.root)
            with self._guard:
                self._environments.pop(env_id, None)
