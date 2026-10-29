"""Deterministic model tools. Policy is evaluated outside model-authored text."""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
import re
import shlex
import tempfile

from pydantic import ValidationError

from app.repair.contracts import ALWAYS_IMMUTABLE, AgentStep, ECC, RepoContract, ToolResult
from app.repair.intake import IntakeError, git, jailed_path, sensitive_path, test_path, validate_path
from app.repair.sandbox.base import SandboxRunner

MAX_FILE = 2_000_000
MAX_OUTPUT = 32_000
_BINARY = {".pyc", ".png", ".jpg", ".jpeg", ".gif", ".joblib", ".pkl", ".pickle",
           ".zip", ".gz", ".exe", ".dll", ".so", ".pdf", ".bin"}


def _matches(path: str, rule: str) -> bool:
    path, rule = path.casefold(), rule.casefold()
    return (path == rule.rstrip("/") or path.startswith(rule.rstrip("/") + "/")
            or fnmatch.fnmatchcase(path, rule)
            or (rule.startswith("**/") and fnmatch.fnmatchcase(path, rule[3:])))


class ToolDenied(ValueError):
    pass


class ToolExecutor:
    """Operate on the actual environment work directory, never the source snapshot.

    The caller serializes tools with runs and passes the candidate environment's
    work_dir. Execution itself always goes through SandboxRunner; there is no
    host-subprocess fallback. `_ap`, Git metadata and secrets are not agent tools.
    """
    def __init__(self, work_dir: str | Path, *, contract: RepoContract, ecc: ECC,
                 runner: SandboxRunner | None = None, env_id: str | None = None,
                 placeholders: dict[str, str] | None = None):
        self.root = Path(work_dir).resolve()
        self.contract, self.ecc = contract.model_copy(deep=True), ecc.model_copy(deep=True)
        if not self.root.is_dir() or not ecc.intact() or ecc.repo_contract_digest != contract.digest():
            raise ValueError("a real work directory and intact matching contract are required")
        self.runner, self.env_id = runner, env_id
        self.existing_tests = {path.casefold() for path in self._files() if test_path(path)}
        self.placeholders = {"artifacts": "_ap/artifacts", "predictions": "_ap/predictions.csv",
                             "holdout_features": contract.data.holdout_features if contract.data else "_ap/rows.csv",
                             "rows": "_ap/rows.csv", "out": "_ap/out.json"}
        if placeholders:
            if set(placeholders) - set(self.placeholders):
                raise ValueError("unknown platform placeholder")
            self.placeholders.update(placeholders)

    def _files(self, relative: str = ".") -> list[str]:
        start = jailed_path(self.root, relative, root_ok=True)
        if not start.exists():
            raise ToolDenied("E_NOT_FOUND: list an existing repository directory first")
        def walk():
            for directory, children, filenames in os.walk(start, followlinks=False):
                parent = Path(directory)
                children[:] = [name for name in children if not (
                    name in ("__pycache__", ".pytest_cache")
                    or sensitive_path((parent / name).relative_to(self.root).as_posix())
                    or (parent / name).is_symlink()
                    or (hasattr(parent / name, "is_junction") and (parent / name).is_junction()))]
                yield from (parent / name for name in filenames)
        paths = [start] if start.is_file() else walk()
        result = []
        for path in paths:
            relative = path.relative_to(self.root).as_posix()
            if sensitive_path(relative) or any(part in ("__pycache__", ".pytest_cache") for part in path.parts):
                continue
            if path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction()):
                continue
            if path.is_file():
                jailed_path(self.root, relative)
                result.append(relative)
                if len(result) > 10_000:
                    raise ToolDenied("E_FILE_LIMIT: narrow the directory before listing")
        return sorted(result)

    def _text(self, path: str) -> str:
        file = jailed_path(self.root, path)
        if file.exists() and file.stat().st_nlink > 1:
            raise ToolDenied("E_HARDLINK: edit an independent source file, not a linked alias")
        if file.suffix.casefold() in _BINARY or not file.is_file():
            raise ToolDenied("E_TEXT_ONLY: choose an existing UTF-8 source file")
        if file.stat().st_size > MAX_FILE:
            raise ToolDenied("E_FILE_LIMIT: choose a source file smaller than 2 MB")
        raw = file.read_bytes()
        if b"\x00" in raw:
            raise ToolDenied("E_BINARY: choose a UTF-8 source file")
        try:
            return raw.decode("utf-8")
        except UnicodeError as exc:
            raise ToolDenied("E_BINARY: choose a UTF-8 source file") from exc

    def _edit(self, act):
        path = validate_path(act.path)
        if not any(path == rule.rstrip("/") or path.startswith(rule.rstrip("/") + "/")
                   for rule in self.ecc.allowed_edit_roots):
            raise ToolDenied("E_EDIT_SCOPE: edit a file under an authorized edit root")
        if any(_matches(path, rule) for rule in (*ALWAYS_IMMUTABLE, *self.ecc.immutable)):
            raise ToolDenied("E_IMMUTABLE: propose a contract amendment to a trusted reviewer")
        # Configuration basenames are reserved even below a writable source root.
        if Path(path).name.casefold() in {"conftest.py", "pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"}:
            raise ToolDenied("E_TEST_CONFIGURATION: add a normal test file, not test configuration")
        if self.ecc.immutable_existing_tests and path.casefold() in self.existing_tests:
            raise ToolDenied("E_EXISTING_TEST: add a new regression test instead")
        file = jailed_path(self.root, path)
        if file.suffix.casefold() in _BINARY or "\x00" in act.new:
            raise ToolDenied("E_BINARY: edit UTF-8 source text only")
        if act.old == "":
            if file.exists():
                raise ToolDenied("E_CREATE_EXISTS: read the file and provide one exact old substring")
            output = act.new
        else:
            text = self._text(path)
            if text.count(act.old) != 1:
                raise ToolDenied("E_MATCH_COUNT: read the current file and choose a substring occurring exactly once")
            output = text.replace(act.old, act.new, 1)
        file.parent.mkdir(parents=True, exist_ok=True)
        # Recheck after creating parents; the caller must not run candidate code
        # concurrently with file operations.
        file = jailed_path(self.root, path)
        file.write_bytes(output.encode("utf-8"))
        return "Edited " + path

    def _run(self, act):
        if not self.runner or not self.env_id:
            raise ToolDenied("E_NO_SANDBOX: prepare an isolated execution environment first")
        if act.kind not in ("test", "train", "predict"):
            raise ToolDenied("E_COMMAND_KIND: choose test, train or predict")
        command = getattr(self.contract.commands, act.kind)
        if not command:
            raise ToolDenied("E_COMMAND_ABSENT: choose a command declared in autopilot.yaml")
        if act.selector and act.kind != "test":
            raise ToolDenied("E_SELECTOR: selectors are only allowed for test runs")
        values = {}
        for name, value in self.placeholders.items():
            # Scratch is trusted platform input, but must still stay inside work.
            if value.startswith("_ap/"):
                validate_path(value[len("_ap/"):])
                resolved = (self.root / value).resolve()
                if not resolved.is_relative_to(self.root):
                    raise ToolDenied("E_PLACEHOLDER: use an environment-relative platform path")
            else:
                jailed_path(self.root, value)
            values[name] = value
        try:
            if any(field not in values for field in re.findall(r"\{([^{}]*)\}", command)):
                raise ToolDenied("E_COMMAND_FORMAT: use only declared platform placeholders")
            argv = [token.format_map(values) for token in shlex.split(command, posix=True)]
        except (ValueError, KeyError, IndexError, AttributeError) as exc:
            raise ToolDenied("E_COMMAND_FORMAT: correct the repository command contract") from exc
        if not argv or not argv[0] or any("\x00" in token for token in argv):
            raise ToolDenied("E_COMMAND_FORMAT: declare a nonempty argv command")
        if act.selector:
            for selector in act.selector.split():
                path = selector.split("::", 1)[0]
                jailed_path(self.root, path)
                if not re.fullmatch(r"[\w./-]+\.py(?:::[\w\[\]-]+)*", selector):
                    raise ToolDenied("E_SELECTOR: use repository-relative pytest node IDs")
                argv.append(selector)
        timeout = self.contract.limits.train_seconds if act.kind == "train" else self.contract.limits.test_seconds
        result = self.runner.run(self.env_id, argv, timeout_s=timeout)
        output = result.stdout + ("\n" + result.stderr if result.stderr else "")
        return ToolResult(ok=result.exit_code == 0 and not result.timed_out, action="run",
                          output=output[:MAX_OUTPUT], truncated=result.truncated or len(output) > MAX_OUTPUT,
                          exit_code=result.exit_code, duration_ms=result.duration_ms)

    def execute(self, step: AgentStep | dict) -> ToolResult:
        action = "invalid"
        try:
            step = AgentStep.model_validate(step)
            act, action = step.act, step.act.action
            if action not in ("list_files", "read_file", "search", "apply_edit", "run", "finish"):
                raise ToolDenied("E_ACTION: choose an action from the frozen tool protocol")
            if not self.ecc.intact() or self.ecc.repo_contract_digest != self.contract.digest():
                raise ToolDenied("E_CONTRACT: request a fresh trusted admission contract")
            if action == "run":
                return self._run(act)
            if action == "apply_edit":
                output, truncated = self._edit(act), False
            elif action == "list_files":
                files = self._files(act.path)
                output, truncated = "\n".join(files), False
            elif action == "read_file":
                lines = self._text(act.path).splitlines()
                last = min(act.end_line or act.start_line + 199, act.start_line + 199)
                if last < act.start_line:
                    raise ToolDenied("E_LINE_RANGE: end_line must be at least start_line")
                output = "\n".join(f"{i}: {lines[i-1]}" for i in range(act.start_line, min(last, len(lines)) + 1))
                truncated = last < len(lines)
            elif action == "search":
                hits = []
                for path in self._files(act.path):
                    try:
                        lines = self._text(path).splitlines()
                    except ToolDenied:
                        continue
                    for i, line in enumerate(lines, 1):
                        if act.pattern in line:
                            hits.append(f"{path}:{i}: {line}")
                            if len(hits) == 51:
                                break
                    if len(hits) == 51:
                        break
                output, truncated = "\n".join(hits[:50]), len(hits) > 50
            else:
                output, truncated = "Finish recorded; independent verification is still required.", False
            return ToolResult(ok=True, action=action, output=output[:MAX_OUTPUT],
                              truncated=truncated or len(output) > MAX_OUTPUT)
        except (ToolDenied, IntakeError) as exc:
            return ToolResult(ok=False, action=action, denied=str(exc) + "; choose an authorized repository path")
        except ValidationError:
            return ToolResult(ok=False, action=action, denied="E_SCHEMA: send one valid AgentStep action with safe paths")
        except OSError:
            return ToolResult(ok=False, action=action, denied="E_FILESYSTEM: refresh the environment and retry an existing source path")
        except UnicodeError:
            return ToolResult(ok=False, action=action, denied="E_TEXT_ENCODING: provide valid UTF-8 source text")


def platform_diff(work_dir: str | Path, base_sha: str, *, snapshot_dir: str | Path | None = None) -> str:
    """Generate a diff using platform-only Git metadata and a private scratch index.

    `snapshot_dir` is the trusted intake snapshot; the candidate work directory
    need not contain Git metadata. Neither metadata nor this index is mounted
    inside a candidate container. The snapshot's index is never mutated.
    """
    root = Path(work_dir).resolve()
    snapshot = Path(snapshot_dir).resolve() if snapshot_dir else root
    if not re.fullmatch(r"[a-f0-9]{40}", base_sha):
        raise IntakeError("exact base commit required")
    prefix = ["--git-dir", str(snapshot / ".git"), "--work-tree", str(root)]
    with tempfile.TemporaryDirectory(prefix="repair-diff-") as directory:
        index = Path(directory) / "index"
        git([*prefix, "read-tree", base_sha], cwd=root, index_file=index)
        new = git([*prefix, "ls-files", "--others", "-z"], cwd=root,
                  index_file=index).decode("utf-8").split("\x00")
        for relative in filter(None, new):
            if sensitive_path(relative) or any(part in ("__pycache__", ".pytest_cache") for part in Path(relative).parts):
                continue
            jailed_path(root, relative)
            git([*prefix, "add", "-N", "--", relative], cwd=root, index_file=index)
        return git([*prefix, "diff", "--no-ext-diff", "--no-textconv", "--binary", base_sha, "--", ".",
                    ":(exclude)_ap", ":(exclude).env*"], cwd=root, index_file=index).decode("utf-8")
