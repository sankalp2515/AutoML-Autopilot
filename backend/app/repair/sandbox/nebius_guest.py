"""Platform-owned guest controller, sent via python -I -S -c; never repository code.

The controller runs as root only to manage the workspace and drop child credentials.
Candidate commands run as uid/gid 10001, without a shell, inherited credentials or
network interface. Its JSON response is separate from captured candidate streams.
"""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

ROOT = Path("/work")


def path(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or ".." in p.parts or "\\" in name or "\x00" in name or ":" in name:
        raise ValueError("unsafe workspace path")
    target = ROOT.joinpath(*p.parts)
    for part in [ROOT, *target.parents, target]:
        if part == Path("/"):
            continue
        if part.is_symlink():
            raise ValueError("workspace symlink refused")
    if target.exists():
        info = target.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("workspace path is not a single regular file")
    return target


def write(files, modes=None):
    for name, content in files.items():
        target = path(name)
        mode = 0o700 if target.exists() and target.stat().st_mode & 0o111 else 0o600
        mode = (modes or {}).get(name, mode)
        if mode not in (0o600, 0o700):
            raise ValueError("invalid workspace file mode")
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        # Parent ownership is needed by the non-root candidate, but /usr and the
        # dependency image stay root-owned and outside its editable workspace.
        current = target.parent
        while current != ROOT.parent:
            os.chown(current, 10001, 10001)
            current.chmod(0o700)
            current = current.parent
        with target.open("wb") as stream:
            stream.write(base64.b64decode(content, validate=True))
        os.chown(target, 10001, 10001)
        target.chmod(mode)


def inventory():
    files = {}
    for directory, dirs, names in os.walk(ROOT, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in names:
            p = Path(directory) / name
            info = p.lstat()
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                files[p.relative_to(ROOT).as_posix()] = info.st_size
                if len(files) > 10000:
                    raise ValueError("workspace file count exceeded")
    return files


class Capture:
    def __init__(self):
        self.head, self.tail, self.count = b"", b"", 0

    def drain(self, pipe):
        while True:
            raw = pipe.read(8192)
            if not raw:
                break
            self.count += len(raw)
            take = min(4096 - len(self.head), len(raw))
            if take:
                self.head += raw[:take]
                raw = raw[take:]
            if raw:
                self.tail = (self.tail + raw)[-12288:]

    def text(self):
        raw = self.head + (b"\n[output truncated]\n" if self.count > 16384 else b"") + self.tail
        return raw.decode("utf-8", errors="replace")


def run(request):
    argv = request["argv"]
    if not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
        raise ValueError("invalid argv")
    env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp", "PYTHONDONTWRITEBYTECODE": "1"}
    env.update(request.get("env", {}))
    # This flag is inherited across exec and cannot be cleared by the child.
    if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("cannot set no-new-privileges")
    stdout, stderr = Capture(), Capture()
    start = time.monotonic()
    proc = subprocess.Popen(argv, cwd=ROOT, env=env, shell=False, user=10001,
                            group=10001, extra_groups=(), umask=0o077,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    readers = [threading.Thread(target=cap.drain, args=(pipe,), daemon=True)
               for cap, pipe in ((stdout, proc.stdout), (stderr, proc.stderr))]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        proc.wait(timeout=request["timeout_s"])
    except subprocess.TimeoutExpired:
        timed_out = True
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        # Kill detached descendants too, before inspecting any candidate files.
        for process in Path("/proc").iterdir():
            try:
                if process.name.isdecimal() and process.stat().st_uid == 10001:
                    os.kill(int(process.name), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
        proc.wait()
        for reader in readers:
            reader.join(timeout=5)
        if any(reader.is_alive() for reader in readers):
            raise RuntimeError("candidate output stream did not close")
    artifacts = {}
    for name in request.get("collect", []):
        target = path(name)
        if target.exists():
            with target.open("rb") as stream:
                artifacts[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"exit_code": -1 if timed_out else proc.returncode, "timed_out": timed_out,
            "duration_ms": int((time.monotonic() - start) * 1000),
            "stdout": stdout.text(), "stderr": stderr.text(),
            "truncated": stdout.count > 16384 or stderr.count > 16384, "artifacts": artifacts}


def main():
    request = json.load(sys.stdin)
    ROOT.mkdir(mode=0o700, exist_ok=True)
    os.chown(ROOT, 10001, 10001)
    ROOT.chmod(0o700)
    (ROOT / "_ap").mkdir(exist_ok=True)
    os.chown(ROOT / "_ap", 10001, 10001)
    if request["action"] == "prepare":
        if not request["pins"]:
            return {"ok": True, "files": {}}
        # Only operator-validated exact dependency pins; repository source is
        # deliberately absent during this network-enabled operation.
        command = [sys.executable, "-m", "pip", "install", *request["pins"],
                   "--isolated", "--only-binary=:all:", "--disable-pip-version-check",
                   "--index-url", request["index"]]
        result = subprocess.run(command, cwd="/tmp", env={"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/tmp"},
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=request["timeout_s"])
        if result.returncode:
            raise RuntimeError("dependency installation failed: " + result.stderr[-2000:].decode(errors="replace"))
        return {"ok": True, "files": {}}
    write(request.get("files", {}), request.get("modes"))
    result = run(request) if request["action"] == "run" else {"ok": True}
    result["files"] = inventory()
    return result


if __name__ == "__main__":
    try:
        response = main()
    except Exception as exc:
        response = {"error": type(exc).__name__ + ": " + str(exc)[:2000]}
    print(json.dumps(response))
