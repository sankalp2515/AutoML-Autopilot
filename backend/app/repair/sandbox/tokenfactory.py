"""Nebius Sandboxes REST adapter; credentials never enter a guest operation.

Each environment points to an immutable provider image. Mutations create a new
checkpoint; forks share the old checkpoint until either branch changes. A
dependency-only checkpoint is separately bound into admission and reused by the
trusted evaluator with an empty workspace. Provider retention is not deletion.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
import shlex
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

from app.repair.sandbox.base import RunResult, SandboxError
from app.repair.sandbox.install_policy import PIN, install_argv
from app.repair.sandbox.local import _path, _relative

GUEST = Path(__file__).with_name("nebius_guest.py").read_text(encoding="utf-8")
BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes/v1"


class TokenFactoryRunner:
    name = "tokenfactory-sandbox"
    isolated = True

    def __init__(self, root, *, api_key, project_id, base_url=BASE_URL,
                 layer_bytes=2 * 1024 ** 3, transport=None, poll_s=0.5):
        if not api_key or not project_id:
            raise SandboxError("Nebius Sandboxes requires an API key and enabled project ID")
        if not transport and base_url != BASE_URL:
            raise SandboxError("unsupported Nebius Sandboxes endpoint")
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._headers = {"Authorization": "Bearer " + api_key, "Project": project_id}
        self.base_url, self.transport = base_url.rstrip("/"), transport
        self.layer_bytes, self.poll_s = layer_bytes, poll_s
        self._guard = threading.RLock()
        self._locks = {}
        self._health = None
        from app.config import settings
        self._slots = threading.BoundedSemaphore(max(1, settings.REPAIR_SANDBOX_MAX_CONCURRENT))
        for directory in ("environments", "images", "cache", "operations"):
            (self.root / directory).mkdir(exist_ok=True)

    def _request(self, method, path, **kwargs):
        try:
            with httpx.Client(timeout=45, transport=self.transport, follow_redirects=False) as client:
                response = client.request(method, self.base_url + path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise SandboxError("Nebius sandbox transport: " + type(exc).__name__) from exc
        if response.status_code == 404:
            raise FileNotFoundError("sandbox resource unavailable")
        if response.status_code not in (200, 201, 202, 204):
            raise SandboxError(f"Nebius sandbox control plane HTTP {response.status_code}")
        return response.json() if response.content else {}

    def _save(self, path, data):
        temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(data), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)

    def _state(self, ident):
        if not re.fullmatch(r"[0-9a-f]{32}", ident):
            raise SandboxError("invalid sandbox environment")
        path = self.root / "environments" / (ident + ".json")
        try:
            data = json.loads(path.read_text())
        except FileNotFoundError:
            raise SandboxError("sandbox environment unavailable") from None
        return path, data

    def _lock(self, ident):
        with self._guard:
            return self._locks.setdefault(ident, threading.RLock())

    def _new(self, dependency, image, files=None):
        with self._guard:
            from app.config import settings
            if len(list((self.root / "environments").glob("*.json"))) >= settings.REPAIR_SANDBOX_MAX_ENVIRONMENTS:
                raise SandboxError("sandbox environment capacity reached; retry after cleanup")
            ident = uuid.uuid4().hex
            self._save(self.root / "environments" / (ident + ".json"),
                       {"dependency": dependency, "image": image, "files": files or {}})
        return ident

    @staticmethod
    def _stream(data):
        if not data:
            return ""
        raw = base64.b64decode(data["value"]) if data.get("encoding") == "base64" else data.get("value", "").encode()
        return raw.decode("utf-8", errors="replace")

    def _operation(self, image, request, *, timeout_s, network=False):
        if not self._slots.acquire(timeout=30):
            raise SandboxError("sandbox execution slots busy; retry after the current operation")
        try:
            return self._execute(image, request, timeout_s=timeout_s, network=network)
        finally:
            self._slots.release()

    def _execute(self, image, request, *, timeout_s, network=False):
        # The command is platform-owned code. Untrusted argv is passed in stdin,
        # never interpolated into Python source or a shell expression.
        payload = {"image": image, "command": "/usr/local/bin/python3", "shell": False,
                   "args": ["-I", "-S", "-c", GUEST], "cwd": "/work", "uid": 0, "gid": 0,
                   "env": {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
                   "preserve_env": False, "networking": {"enabled": network},
                   "resources_limits": {"max_layer_bytes": self.layer_bytes},
                   "stdin": {"value": json.dumps(request), "encoding": "ascii", "close": True},
                   "timeout": min(timeout_s + 20, 3600), "truncate_output_at": 10485760,
                   "disposable": False}
        response = self._request("POST", "/instances", json=payload)
        operation = str(uuid.UUID(response["uuid"]))
        marker = self.root / "operations" / (operation + ".json")
        self._save(marker, {"operation": operation})
        deadline = time.monotonic() + timeout_s + 120
        try:
            while time.monotonic() < deadline:
                status = self._request("GET", "/operations/" + operation)
                if status["status"] in ("SUCCESS", "FAILED", "CANCELLED"):
                    break
                time.sleep(self.poll_s)
            else:
                self._request("POST", "/operations/" + operation + "/cancel")
                raise SandboxError("Nebius operation exceeded its bounded client wait")
            if status["status"] != "SUCCESS":
                raise SandboxError("Nebius operation did not complete: " + status["status"])
            result = status.get("metadata", {}).get("result") or {}
            state = result.get("state") or {}
            if state.get("timed_out") or state.get("exit_code", 0) != 0:
                raise SandboxError("Nebius guest controller failed or timed out")
            try:
                output = json.loads(self._stream(result.get("stdout")))
            except (ValueError, KeyError):
                raise SandboxError("Nebius guest returned invalid controller output") from None
            if output.get("error"):
                raise SandboxError("Nebius guest: " + output["error"])
            saved = status.get("result_image_uuid") or result.get("image") or status.get("result", {}).get("image")
            if not saved:
                raise SandboxError("Nebius did not retain an execution checkpoint")
            return str(uuid.UUID(saved)), output
        except BaseException:
            # Only cancel this client's own accepted operation, never a project batch.
            try:
                self._request("POST", "/operations/" + operation + "/cancel")
            except Exception:
                pass
            raise
        finally:
            marker.unlink(missing_ok=True)

    def recover_orphan_operations(self):
        for marker in (self.root / "operations").glob("*.json"):
            operation = str(uuid.UUID(json.loads(marker.read_text())["operation"]))
            try:
                self._request("POST", "/operations/" + operation + "/cancel")
            except (SandboxError, FileNotFoundError):
                pass
            marker.unlink(missing_ok=True)

    def health_check(self):
        if self._health and time.monotonic() - self._health[0] < 60:
            return self._health[1]
        try:
            data = self._request("GET", "/whoami", timeout=3)
            ok = data.get("permissions", {}).get("spawn") is True
        except (SandboxError, FileNotFoundError):
            ok = False
        self._health = (time.monotonic(), ok)
        return ok

    def prepare(self, repo_dir, *, python, install, timeout_s):
        root = Path(repo_dir)
        argv, index = install_argv(root, install)
        original = shlex.split(install)[2:]
        if len(original) == 2 and original[0] in ("-r", "--requirement"):
            lines = _path(root, original[1]).read_text().splitlines()
            pins = [line.split("#", 1)[0].strip() for line in lines]
            pins = [p for p in pins if p]
        else:
            pins = original
        if any(not PIN.fullmatch(p) for p in pins):
            raise SandboxError("exact dependency pins required")
        source_digest = hashlib.sha256()
        for source in sorted(root.rglob("*")):
            rel = source.relative_to(root)
            if {".git", "__pycache__", ".pytest_cache", "_ap"} & set(rel.parts):
                continue
            checked = _path(root, rel.as_posix())
            if checked.is_file():
                source_digest.update(json.dumps([rel.as_posix(), checked.stat().st_mode & 0o111,
                                                 hashlib.sha256(checked.read_bytes()).hexdigest()]).encode())
        key = hashlib.sha256(json.dumps([source_digest.hexdigest(), python, install, index,
                                         hashlib.sha256(GUEST.encode()).hexdigest()]).encode()).hexdigest()
        cache = self.root / "cache" / (key + ".json")
        with self._guard:
            if cache.exists():
                dependency = json.loads(cache.read_text())["image"]
            else:
                if not re.fullmatch(r"3\.\d{1,2}", python):
                    raise SandboxError("invalid Python version")
                tag = "python:" + python + "-slim"
                images = self._request("GET", "/images?tag=" + quote(tag) + "&limit=100")["images"]
                base = next((entry["uuid"] for entry in images if entry.get("tag") == tag), None)
                if not base:
                    raise SandboxError("requested Python base image is unavailable")
                dependency, _ = self._operation(base, {"action": "prepare", "pins": pins, "index": index,
                                                       "timeout_s": timeout_s}, timeout_s=timeout_s, network=True)
                record = {"image": dependency, "python": python, "install_digest": hashlib.sha256(install.encode()).hexdigest()}
                self._save(self.root / "images" / (dependency + ".json"), record)
                self._save(cache, record)
        return self.prepare_pinned(repo_dir, image="nebius:" + dependency, timeout_s=timeout_s)

    def prepare_pinned(self, repo_dir, *, image, timeout_s):
        if not image.startswith("nebius:"):
            raise SandboxError("expected a pinned Nebius image UUID")
        dependency = str(uuid.UUID(image.removeprefix("nebius:")))
        self.image = "nebius:" + dependency
        if not (self.root / "images" / (dependency + ".json")).is_file():
            raise SandboxError("pinned Nebius dependency image not registered")
        ident = self._new(dependency, dependency)
        root, files, modes, total = Path(repo_dir), {}, {}, 0
        try:
            for source in sorted(root.rglob("*")):
                rel = source.relative_to(root)
                if {".git", "__pycache__", ".pytest_cache", "_ap"} & set(rel.parts):
                    continue
                if source.is_symlink():
                    raise SandboxError("repository symlinks refused")
                if source.is_file():
                    raw = source.read_bytes()
                    total += len(raw)
                    if total > 50 * 1024 ** 2 or len(files) >= 5000:
                        raise SandboxError("repository exceeds sandbox input limits")
                    files[rel.as_posix()] = raw
                    modes[rel.as_posix()] = 0o700 if source.stat().st_mode & 0o111 else 0o600
            self._write_files(ident, files, modes=modes)
            return ident
        except BaseException:
            self.discard(ident)
            raise

    def environment_image(self, env_id):
        return "nebius:" + self._state(env_id)[1]["dependency"]

    def fork(self, env_id):
        with self._lock(env_id):
            _, state = self._state(env_id)
            return self._new(state["dependency"], state["image"], dict(state["files"]))

    @staticmethod
    def _files(files):
        from app.repair.intake import validate_path
        result, total = {}, 0
        for name, value in files.items():
            _relative(name)
            total += len(value or b"")
            if total > 50 * 1024 ** 2:
                raise SandboxError("sandbox write exceeds input limit")
            result[name] = None if value is None else base64.b64encode(value).decode()
        return result

    def write_files(self, env_id, files):
        self._write_files(env_id, files)

    def _write_files(self, env_id, files, *, modes=None):
        encoded = self._files(files)
        with self._lock(env_id):
            path, state = self._state(env_id)
            state["image"], result = self._operation(state["image"], {"action": "write", "files": encoded,
                                                                   "modes": modes or {}}, timeout_s=60)
            state["files"] = result["files"]
            self._save(path, state)

    def inventory(self, env_id):
        from app.repair.intake import sensitive_path
        state = self._state(env_id)[1]
        return sorted(p for p in state["files"] if not sensitive_path(p) and
                      not ({"_ap", ".git", "__pycache__", ".pytest_cache"} & set(Path(p).parts)))

    def read_file(self, env_id, path, max_bytes=2_000_000):
        path = _relative(path).as_posix()
        with self._lock(env_id):
            state = self._state(env_id)[1]
            if path not in state["files"]:
                raise FileNotFoundError(path)
            if state["files"][path] > max_bytes:
                raise SandboxError("sandbox file exceeds read limit")
            url = self.base_url + "/inspect/" + state["image"] + "/download"
            try:
                with httpx.Client(timeout=45, transport=self.transport, follow_redirects=False) as client:
                    with client.stream("GET", url, headers=self._headers, params={"path": "/work/" + path}) as response:
                        if response.status_code == 404:
                            raise FileNotFoundError(path)
                        if response.status_code != 200:
                            raise SandboxError("sandbox artifact download failed")
                        chunks, total = [], 0
                        for chunk in response.iter_bytes():
                            total += len(chunk)
                            if total > max_bytes:
                                raise SandboxError("sandbox file exceeds read limit")
                            chunks.append(chunk)
                        return b"".join(chunks)
            except httpx.HTTPError as exc:
                raise SandboxError("sandbox artifact transport failure") from exc

    def run(self, env_id, argv, *, timeout_s, uploads=None, collect=None, env=None):
        if not argv or not 0 < timeout_s <= 1800 or any(not isinstance(a, str) or "\x00" in a for a in argv):
            raise SandboxError("invalid command or timeout")
        if env and any(not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", k) or "\x00" in v for k, v in env.items()):
            raise SandboxError("invalid execution environment")
        for name in collect or []:
            _relative(name)
        files = self._files({u.dest: u.content for u in uploads or []})
        with self._lock(env_id):
            path, state = self._state(env_id)
            state["image"], result = self._operation(state["image"], {"action": "run", "argv": argv,
                "timeout_s": timeout_s, "files": files, "collect": collect or [], "env": env or {}}, timeout_s=timeout_s)
            state["files"] = result.pop("files")
            self._save(path, state)
            return RunResult(**result, runner=self.name)

    def trusted_run(self, env_id, argv, *, uploads, timeout_s, collect=None, env=None):
        dependency = self._state(env_id)[1]["dependency"]
        clean = self._new(dependency, dependency)
        try:
            result = self.run(clean, argv, uploads=uploads, timeout_s=timeout_s, collect=collect,
                              env={"PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", **(env or {})})
            files = {name: self.read_file(clean, name, 2_000_000) for name in collect or [] if name in result.artifacts}
            return result, files
        finally:
            self.discard(clean)

    def discard(self, env_id):
        if re.fullmatch(r"[0-9a-f]{32}", env_id):
            with self._lock(env_id):
                (self.root / "environments" / (env_id + ".json")).unlink(missing_ok=True)

    def cleanup(self, ttl_s, *, now=None):
        now = time.time() if now is None else now
        for path in (self.root / "environments").glob("*.json"):
            if now - path.stat().st_mtime > ttl_s:
                self.discard(path.stem)
