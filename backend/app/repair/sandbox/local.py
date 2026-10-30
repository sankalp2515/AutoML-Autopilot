"""Docker CLI runner. The Docker daemon is trusted; candidate code is not.

Only dependency preparation has networking. Each execution sees its own bind
mounts, runs unprivileged, and has bounded resources and captured output. This is
container isolation, not a claim of a microVM boundary against kernel exploits.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
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
from weakref import WeakValueDictionary

from .base import RunResult, SandboxError, Upload

HEAD_BYTES, TAIL_BYTES = 4096, 12288
_IMAGE_LOCKS=WeakValueDictionary()
_IMAGE_LOCK_GUARD=threading.Lock()


def _image_lock(key):
    with _IMAGE_LOCK_GUARD:
        lock=_IMAGE_LOCKS.get(key)
        if lock is None:lock=threading.RLock();_IMAGE_LOCKS[key]=lock
        return lock


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
        self.namespace=hashlib.sha256(str(self.root).encode()).hexdigest()[:12]
        self._environments: dict[str, _Environment] = {}
        self._guard = threading.RLock()
        from app.config import settings
        self._execution_slots=threading.BoundedSemaphore(max(1,settings.REPAIR_SANDBOX_MAX_CONCURRENT))

    def recover_orphan_containers(self):
        result=self._command(["ps","-aq","--filter",f"label=autopilot.runner.namespace={self.namespace}"],timeout_s=15)
        if result.exit_code:raise SandboxError("cannot inspect previous sandbox containers")
        identifiers=result.stdout.split()
        if any(not re.fullmatch(r"[0-9a-f]{12,64}",ident) for ident in identifiers):raise SandboxError("invalid daemon container inventory")
        if identifiers:
            removed=self._command(["rm","--force",*identifiers],timeout_s=30)
            if removed.exit_code:raise SandboxError("cannot stop orphan sandbox containers")
        return len(identifiers)

    def _capacity(self):
        from app.config import settings
        if sum(1 for p in self.root.iterdir() if p.is_dir() and re.fullmatch(r"[0-9a-f]{32}",p.name))>=settings.REPAIR_SANDBOX_MAX_ENVIRONMENTS:
            raise SandboxError("sandbox environment capacity reached; retry after cleanup")

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
        self._capacity()
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
        with self._guard:
            self._capacity()
            environment.root.mkdir(mode=0o700)
        try:
            work = environment.root / "work"
            _copy_tree(Path(repo_dir).absolute(), work)
            from .install_policy import install_argv as checked_install
            install_argv,index=checked_install(work,install)
            out = environment.root / "out"
            out.mkdir(mode=0o777)
            out.chmod(0o777)
            base = self.base_image or f"python:{python}-slim"
            resolved=self._command(["image","inspect",base,"--format","{{.Id}}"],timeout_s=15)
            if resolved.exit_code:
                pulled=self._command(["pull",base],timeout_s=timeout_s)
                if pulled.exit_code:raise SandboxError("cannot obtain the configured Python base image")
                resolved=self._command(["image","inspect",base,"--format","{{.Id}}"],timeout_s=15)
            base_digest=resolved.stdout.strip()
            if not base_digest.startswith("sha256:"):raise SandboxError("base image content digest unavailable")
            # BuildKit treats a bare config id in FROM as a registry image name.
            base_ref="automl-python-base:"+base_digest.split(":",1)[1]
            tagged=self._command(["image","tag",base_digest,base_ref],timeout_s=15)
            if tagged.exit_code:raise SandboxError("cannot pin the prepared Python base")
            key = hashlib.sha256(json.dumps([_snapshot_digest(work), python, install, base,
                                             base_digest,index,"local-runner-v5"], separators=(",", ":")).encode()).hexdigest()
            image = f"automl-repair-{self.namespace}:{key}"
            with _image_lock(key):
                inspected = self._command(["image", "inspect", image, "--format", "{{.Id}}"], timeout_s=15)
                if inspected.exit_code != 0:
                    # Build context is platform-created; a repository Dockerfile or
                    # .dockerignore can neither inject instructions nor hide inputs.
                    with tempfile.TemporaryDirectory(prefix="build-", dir=self.root) as build_root:
                        build = Path(build_root)
                        # Installer policy forbids local source installs. Copy only the
                        # pinned requirements, so private source/data never enters an image layer.
                        (build/"repo").mkdir()
                        tokens=shlex.split(install)
                        flag=next((f for f in ("-r","--requirement") if f in tokens),None)
                        if flag:
                            name=tokens[tokens.index(flag)+1]
                            destination=build/"repo"/name;destination.parent.mkdir(parents=True,exist_ok=True)
                            shutil.copyfile(_path(work,name),destination)
                        else:(build/"repo/.empty").write_bytes(b"")
                        recipe = (
                            f"FROM {base_ref}\nUSER 0\nWORKDIR /build\nCOPY repo/ /build/\n"
                            "ENV PIP_CONFIG_FILE=/dev/null\n"
                            f"RUN {json.dumps(install_argv)}\n"
                            'RUN ["python", "-I", "-S", "-c", "import shutil; shutil.rmtree(\'/build\')"]\n'
                            f'LABEL autopilot.repair.key="{key}"\n'
                            "WORKDIR /work\nUSER 10001\n"
                        )
                        (build / "Dockerfile").write_text(recipe, encoding="utf-8")
                        built = self._command(["build", "--pull=false", "--tag", image, "--file", str(build / "Dockerfile"),
                                               str(build)], timeout_s=timeout_s)
                        if built.exit_code != 0:
                            raise SandboxError(f"dependency image preparation failed: {built.stderr[-2000:]}")
                ident = self._command(["image", "inspect", image, "--format", "{{.Id}}"], timeout_s=15)
                resolved_image = (ident.stdout or "").strip()
                if not resolved_image.startswith("sha256:"):raise SandboxError("dependency image digest unavailable")
                self.image = resolved_image
                environment.image = resolved_image  # Run by immutable content id, never by a mutable tag.
                (environment.root/".environment.json").write_text(json.dumps({"image":resolved_image,"tag":image}))
                usage=self.root/".image-usage";usage.mkdir(exist_ok=True)
                (usage/key).write_text(image)
            with self._guard:
                self._environments[env_id] = environment
            return env_id
        except Exception:
            self._remove(environment.root)
            raise

    def environment_image(self,env_id):
        return self._environment(env_id).image

    def prepare_pinned(self,repo_dir,*,image,timeout_s):
        """Prepare from an already admitted image; never resolve tags or install again."""
        self._capacity()
        if not re.fullmatch(r"sha256:[0-9a-f]{64}",image):raise SandboxError("invalid admitted image id")
        found=self._command(["image","inspect",image,"--format","{{.Id}}"],timeout_s=min(15,timeout_s))
        if found.exit_code or found.stdout.strip()!=image:raise SandboxError("admitted dependency image unavailable; restore it or create a new task")
        ident=uuid.uuid4().hex
        environment=_Environment(self.root/ident,image,threading.RLock())
        with self._guard:
            self._capacity()
            environment.root.mkdir(mode=0o700)
        try:
            _copy_tree(Path(repo_dir).absolute(),environment.root/"work")
            (environment.root/"out").mkdir(mode=0o777);(environment.root/"out").chmod(0o777)
            (environment.root/".environment.json").write_text(json.dumps({"image":image}))
            with self._guard:self._environments[ident]=environment
            self.image=image
            return ident
        except Exception:
            self._remove(environment.root);raise

    def cleanup(self,ttl_s,*,now=None):
        """Only this runner namespace; no global prune or arbitrary directory deletion."""
        cutoff=(time.time() if now is None else now)-ttl_s
        removed={"environments":0,"image_tags":0}
        with self._guard:
            active_roots={e.root for e in self._environments.values()}
            active_images={e.image for e in self._environments.values()}
        for folder in self.root.iterdir():
            marker=folder/".environment.json"
            if folder.is_symlink() or not re.fullmatch(r"[0-9a-f]{32}",folder.name):continue
            if marker.is_file() and marker.stat().st_mtime<cutoff:
                if folder in active_roots:
                    environment=self._environments.get(folder.name)
                    if environment is None or not environment.lock.acquire(blocking=False):continue
                    try:self.discard(folder.name)
                    finally:environment.lock.release()
                else:self._remove(folder)
                removed["environments"]+=1
        with self._guard:active_images={e.image for e in self._environments.values()}
        for marker in (self.root/".image-usage").glob("*"):
            if marker.is_symlink() or not re.fullmatch(r"[0-9a-f]{64}",marker.name):continue
            with _image_lock(marker.name):
                if marker.stat().st_mtime>=cutoff:continue
                tag=marker.read_text().strip()
                if tag!=f"automl-repair-{self.namespace}:{marker.name}":continue
                found=self._command(["image","inspect",tag,"--format","{{.Id}}"],timeout_s=15)
                if found.stdout.strip() in active_images:continue
                result=self._command(["image","rm",tag],timeout_s=15)
                if result.exit_code==0 or found.exit_code:
                    marker.unlink(missing_ok=True);removed["image_tags"]+=1
        return removed

    def fork(self, env_id: str) -> str:
        self._capacity()
        parent = self._environment(env_id)
        with parent.lock:
            child_id = uuid.uuid4().hex
            child = _Environment(self.root / child_id, parent.image, threading.RLock())
            try:
                with self._guard:
                    self._capacity()
                    child.root.mkdir(mode=0o700)
                _copy_tree(parent.root / "work", child.root / "work")
                _copy_tree(parent.root / "out", child.root / "out")
                marker=parent.root/".environment.json"
                if marker.is_file():(child.root/".environment.json").write_bytes(marker.read_bytes())
                with self._guard:
                    self._environments[child_id] = child
                return child_id
            except Exception:
                self._remove(child.root)
                raise

    def trusted_run(self,env_id,argv,*,uploads,timeout_s,collect=None,env=None):
        """Verifier capability: same immutable image, EMPTY workspace, no candidate files.

        Only explicit trusted scripts and data cross this boundary. In particular,
        never copy candidate source, pickles, pytest config or batch work state.
        """
        self._capacity()
        parent=self._environment(env_id)
        ident=uuid.uuid4().hex
        child=_Environment(self.root/ident,parent.image,threading.RLock())
        with self._guard:
            self._capacity()
            child.root.mkdir(mode=0o700)
        (child.root/".environment.json").write_text(json.dumps({"image":parent.image,"trusted":True}))
        for name in ("work","out"):
            (child.root/name).mkdir(mode=0o777);(child.root/name).chmod(0o777)
        with self._guard:self._environments[ident]=child
        try:
            result=self.run(ident,argv,uploads=uploads,timeout_s=timeout_s,collect=collect,
                            env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD":"1",**(env or {})})
            files={name:self.read_file(ident,name,max_bytes=2_000_001) for name in collect or []
                   if name in result.artifacts}
            if any(len(raw)>2_000_000 for raw in files.values()):raise SandboxError("trusted output too large")
            return result,files
        finally:self.discard(ident)

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
                        try:
                            parent.chmod(0o777)
                        except PermissionError:
                            pass        # already writable or owned by the candidate user
                    target.write_bytes(content)
                    target.chmod(0o666)

    def inventory(self, env_id: str) -> list[str]:
        """Optional bounded file RPC; avoids starting a container merely to list files.

        No host path leaves the runner. Consumers revalidate and read through the
        existing jailed read_file API. The frozen SandboxRunner protocol is unchanged.
        """
        environment=self._environment(env_id)
        with environment.lock:
            root=environment.root/"work"
            files=[]
            for directory, dirs, names in os.walk(root,followlinks=False):
                dirs[:]=[d for d in dirs if d not in {".git", "_ap", "sealed", "__pycache__", ".pytest_cache"} and not d.startswith(".env") and not (Path(directory)/d).is_symlink()]
                for name in names:
                    path=Path(directory)/name
                    if name.startswith(".env") or path.is_symlink():continue
                    info=path.stat(follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink!=1:continue
                    relative=path.relative_to(root).as_posix()
                    _path(root,relative)
                    files.append(relative)
                    if len(files)>10000:raise SandboxError("source file inventory limit exceeded")
            return sorted(files)

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
        if not self._execution_slots.acquire(timeout=min(max(timeout_s,0),30)):
            raise SandboxError("sandbox execution capacity busy; retry later")
        try:return self._run(env_id,argv,timeout_s=timeout_s,uploads=uploads,collect=collect,env=env)
        finally:self._execution_slots.release()

    def _run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        environment = self._environment(env_id)
        if not argv or not all(isinstance(arg, str) and "\x00" not in arg for arg in argv) or timeout_s <= 0:
            raise SandboxError("run requires a nonempty argv and positive timeout")
        if env and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) or "\x00" in value
                       for key, value in env.items()):
            raise SandboxError("invalid execution environment")
        with environment.lock:
            marker=environment.root/".environment.json"
            if marker.is_file():marker.touch()
            self.write_files(env_id, {upload.dest: upload.content for upload in uploads or []})
            name = f"ap-repair-{uuid.uuid4().hex}"
            cidfile = environment.root / f"{name}.cid"
            command = ["run", "--rm", "--pull", "never", "--name", name, "--cidfile", str(cidfile),
                       "--label",f"autopilot.runner.namespace={self.namespace}","--label",f"autopilot.environment={env_id}",
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
