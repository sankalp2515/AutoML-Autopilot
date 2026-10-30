"""Shared oracle plumbing: environments, command expansion, diff application, pytest results."""
from __future__ import annotations

import fnmatch
import hashlib
import os
import sys
import threading
import importlib.metadata
import re
import shlex
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from app.repair.contracts import Check, CheckStatus, RepoContract, safe_relpath
from app.repair.sandbox.base import SCRATCH, RunResult, SandboxRunner

ARTIFACTS = f"{SCRATCH}/artifacts"
PREDICTIONS = f"{SCRATCH}/predictions.csv"
_TRAIN_CONTEXT: dict[tuple[int, str], str] = {}
_TRAIN_CACHE: dict[str, bytes] = {}
_TRAIN_LOCK = threading.RLock()


def _training_key(base_dir, contract, files):
    """Every admitted byte, patch byte and installed runtime version contributes.

    Test-only and opt-in: deterministic fixture training can reuse opaque artifact
    bytes. We never unpickle them in the controller or enable this for live work.
    """
    digest = hashlib.sha256(contract.model_dump_json().encode())
    for path in sorted(Path(base_dir).rglob("*")):
        rel = path.relative_to(base_dir).as_posix()
        if path.is_file() and not path.is_symlink() and not ({".git", SCRATCH, "__pycache__", ".pytest_cache"} & set(Path(rel).parts)):
            digest.update(rel.encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    for path, content in sorted((files or {}).items()):
        digest.update(path.encode() + b"\0" + (b"deleted" if content is None else hashlib.sha256(content).digest()))
    digest.update(sys.version.encode())
    for name in ("numpy", "pandas", "scikit-learn", "joblib"):
        digest.update(importlib.metadata.version(name).encode())
    return digest.hexdigest()


def train(runner, env_id, contract):
    argv = expand(contract.commands.train, placeholders(contract))
    key = _TRAIN_CONTEXT.get((id(runner), env_id))
    if key is None:
        return runner.run(env_id, argv, timeout_s=contract.limits.train_seconds)
    with _TRAIN_LOCK:
        if key in _TRAIN_CACHE:
            raw = _TRAIN_CACHE[key]
            runner.write_files(env_id, {model_path(contract): raw})
            return RunResult(0, False, 0, "[test-only deterministic fixture artifact cache hit]", "", False,
                             {model_path(contract): hashlib.sha256(raw).hexdigest()}, runner.name)
        result = runner.run(env_id, argv, timeout_s=contract.limits.train_seconds)
        if result.exit_code == 0 and not result.timed_out:
            try:
                raw = runner.read_file(env_id, model_path(contract), max_bytes=8_000_001)
                if len(raw) <= 8_000_000:
                    if len(_TRAIN_CACHE) >= 32:
                        _TRAIN_CACHE.pop(next(iter(_TRAIN_CACHE)))
                    _TRAIN_CACHE[key] = raw
            except FileNotFoundError:
                pass
        return result


def check(name: str, status: CheckStatus, detail: str, **evidence) -> Check:
    return Check(name=name, status=status, detail=detail, evidence=evidence)


def placeholders(contract: RepoContract, **override: str) -> dict[str, str]:
    p = {"artifacts": ARTIFACTS, "predictions": PREDICTIONS,
         "holdout_features": contract.data.holdout_features if contract.data else ""}
    p.update(override)
    return p


def expand(command: str, values: dict[str, str]) -> list[str]:
    """Contract command string -> argv. Placeholders were validated by the contract model."""
    argv = shlex.split(re.sub(r"\{([a-z_]+)\}", lambda m: values[m.group(1)], command), posix=True)
    if argv and argv[0] == "pytest":
        argv = ["python", "-m", "pytest", *argv[1:]]
    return argv


def model_path(contract: RepoContract) -> str:
    return f"{ARTIFACTS}/{contract.artifacts.model}"


def matches(path: str, patterns: list[str]) -> bool:
    """Directory patterns end with '/' (prefix); others are fnmatch globs; '**/' also matches the root."""
    for pat in patterns:
        if pat.endswith("/") and (path == pat[:-1] or path.startswith(pat)):
            return True
        if fnmatch.fnmatchcase(path, pat) or (pat.startswith("**/") and fnmatch.fnmatchcase(path, pat[3:])):
            return True
    return False


# ── diffs ─────────────────────────────────────────────────────────────────────
@dataclass
class FileChange:
    path: str
    status: str                 # "added" | "modified" | "deleted"
    binary: bool = False
    added_lines: list[str] = field(default_factory=list)
    removed: int = 0


def parse_diff(diff: str) -> list[FileChange]:
    """Minimal unified-diff reader for policy checks (git or plain `diff -u` format).
    A file header is a `--- ` line immediately followed by a `+++ ` line."""
    out: list[FileChange] = []
    cur: FileChange | None = None
    lines = diff.splitlines()
    git_header = False                     # inside a `diff --git` block, before its ---/+++ pair
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = FileChange(path=m.group(2) if m else line[11:], status="modified")
            out.append(cur)
            git_header = True
        elif line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            minus = line[4:].split("\t")[0]
            plus = lines[i + 1][4:].split("\t")[0]
            path = minus if plus == "/dev/null" else plus
            path = path[2:] if path.startswith(("a/", "b/")) else path
            if not (git_header and cur is not None and cur.path == path):
                cur = FileChange(path=path, status="modified")
                out.append(cur)
            if minus == "/dev/null":
                cur.status = "added"
            elif plus == "/dev/null":
                cur.status = "deleted"
            git_header = False
            i += 2
            continue
        elif cur is None:
            pass
        elif line.startswith("@@"):
            # Consume exactly the hunk's declared line counts, so content lines that look
            # like headers ("--- x" removed, "+++ y" added) are never misread.
            git_header = False
            m = re.match(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@", line)
            n_old = int(m.group(1)) if m and m.group(1) is not None else 1
            n_new = int(m.group(2)) if m and m.group(2) is not None else 1
            i += 1
            while i < len(lines) and (n_old > 0 or n_new > 0):
                body = lines[i]
                if body.startswith("+"):
                    cur.added_lines.append(body[1:])
                    n_new -= 1
                elif body.startswith("-"):
                    cur.removed += 1
                    n_old -= 1
                elif body.startswith("\\"):
                    pass                                   # "\ No newline at end of file"
                else:
                    n_old -= 1
                    n_new -= 1
                i += 1
            continue
        elif line.startswith("new file mode"):
            cur.status = "added"
        elif line.startswith("deleted file mode"):
            cur.status = "deleted"
        elif line.startswith(("Binary files", "GIT binary patch")):
            cur.binary = True
        elif line.startswith(("rename from", "rename to", "similarity index", "copy from", "copy to")):
            cur.binary = True          # renames/copies are refused as "not a plain edit"
        elif line.startswith("+"):
            cur.added_lines.append(line[1:])
        elif line.startswith("-"):
            cur.removed += 1
        i += 1
    return out


def diff_digest(diff: str) -> str:
    return "sha256:" + hashlib.sha256(diff.encode()).hexdigest()


def apply_diff(base_dir: str, diff: str) -> dict[str, bytes | None]:
    """Apply a git diff to a scratch copy of the pristine snapshot on the oracle side and
    return the resulting file contents (None = deleted). `git apply` refuses paths outside
    the tree; we re-check every path anyway."""
    changes = parse_diff(diff)
    for c in changes:
        safe_relpath(c.path)
    with tempfile.TemporaryDirectory(prefix="ap-apply-") as tmp:
        work = Path(tmp) / "w"
        shutil.copytree(base_dir, work, ignore=shutil.ignore_patterns(".git", "__pycache__", ".pytest_cache"))
        patch = Path(tmp) / "c.patch"
        patch.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8", newline="\n")
        r = subprocess.run(["git", "apply", "--whitespace=nowarn", str(patch)], cwd=work,
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            raise ValueError(f"diff does not apply: {r.stderr.strip()[:500]}")
        result: dict[str, bytes | None] = {}
        for c in changes:
            p = work / c.path
            result[c.path] = None if c.status == "deleted" or not p.exists() else p.read_bytes()
        return result


# ── environments ──────────────────────────────────────────────────────────────
@contextmanager
def environment(runner: SandboxRunner, base_dir: str, contract: RepoContract,
                files: dict[str, bytes | None] | None = None) -> Iterator[str]:
    env_id = runner.prepare(base_dir, python=contract.python, install=contract.install, timeout_s=900)
    try:
        if (os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("REPAIR_TEST_TRAIN_CACHE") == "1"
                and runner.name == "process-unisolated"):
            _TRAIN_CONTEXT[id(runner), env_id] = _training_key(base_dir, contract, files)
        if files:
            runner.write_files(env_id, files)
        yield env_id
    finally:
        _TRAIN_CONTEXT.pop((id(runner), env_id), None)
        runner.discard(env_id)


# ── pytest results ────────────────────────────────────────────────────────────
@dataclass
class TestRun:
    result: RunResult
    outcomes: dict[str, str]           # node id -> passed | failed | error | skipped
    parsed: bool

    @property
    def passed(self) -> set[str]:
        return {k for k, v in self.outcomes.items() if v == "passed"}


def _node_id(classname: str, name: str) -> str:
    parts = classname.split(".")
    # "tests.test_serve" -> tests/test_serve.py ; "tests.test_x.TestCls" -> tests/test_x.py::TestCls
    for i in range(len(parts), 0, -1):
        if parts[i - 1].startswith("test") or i == 1:
            mod = "/".join(parts[:i]) + ".py"
            rest = parts[i:]
            return "::".join([mod, *rest, name])
    return f"{classname}::{name}"


def run_pytest(runner: SandboxRunner, env_id: str, argv: list[str], *, timeout_s: int, report: str,
               uploads=None, env: dict[str, str] | None = None) -> TestRun:
    # Tests are arbitrary candidate execution and may mutate source; don't reuse
    # an input key computed before they ran.
    _TRAIN_CONTEXT.pop((id(runner), env_id), None)
    full = [*argv, "-p", "no:cacheprovider", "-o", "junit_family=xunit1", f"--junitxml={report}"]
    res = runner.run(env_id, full, timeout_s=timeout_s, uploads=uploads, env=env, collect=[report])
    outcomes: dict[str, str] = {}
    try:
        root = ET.fromstring(runner.read_file(env_id, report))
    except (FileNotFoundError, ET.ParseError):
        return TestRun(res, outcomes, parsed=False)
    for case in root.iter("testcase"):
        nid = _node_id(case.get("classname", ""), case.get("name", ""))
        if file := case.get("file"):
            # xunit1 records the collected path, including custom *_spec.py files.
            classes = case.get("classname", "").split(".")
            suffix = classes[classes.index(Path(file).stem) + 1:] if Path(file).stem in classes else []
            nid = "::".join([file.replace("\\", "/"), *suffix, case.get("name", "")])
        if case.find("failure") is not None:
            outcomes[nid] = "failed"
        elif case.find("error") is not None:
            outcomes[nid] = "error"
        elif case.find("skipped") is not None:
            outcomes[nid] = "skipped"
        else:
            outcomes[nid] = "passed"
    return TestRun(res, outcomes, parsed=True)


def tail(res: RunResult, n: int = 1500) -> str:
    return (res.stderr or res.stdout or "")[-n:]
