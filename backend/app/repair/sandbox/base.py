"""SandboxRunner: the only way candidate code is executed (frozen interface).

Implementations:
    local.py         hardened local container: no network, non-root, read-only base,
                     tmpfs scratch, CPU/RAM/time/output limits             (Codex card C2)
    tokenfactory.py  Nebius Token Factory Sandboxes (microVM, branchable images),
                     activated with the Nebius key and beta access         (Codex card C13)
    process.py       plain subprocess in a temp copy. NOT isolated: tests and local
                     development only; refuses to start unless explicitly allowed.

Every environment has one work directory: a copy of the repository plus a platform
scratch folder `_ap/` (artifacts, predictions, uploaded grader files). All paths in this
interface (`write_files`, `read_file`, `Upload.dest`, `collect`) are relative to the work
directory, and argv runs with the work directory as its current directory.

Guarantees every isolated implementation must provide (tested in tests/test_repair):
    * No outbound network during `run` (dependency install happens in `prepare` only).
    * Nothing outside the environment's own work directory is visible, in particular no
      sealed tests or holdout labels (unless uploaded for one run), provider keys or host paths.
    * The process environment contains only PATH-like basics plus `env` passed to `run`.
    * `fork` copies filesystem state; the parent is never mutated by a child's runs.
    * `run` never raises for a non-zero exit: it returns the exit code. It raises
      SandboxError only for infrastructure failures (cannot start, lost environment).
    * Output is capped and returned as head+tail; digests are computed over full files.
    * argv[0] == "python" means the environment's interpreter.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

SCRATCH = "_ap"          # platform scratch folder inside every work directory


class SandboxError(RuntimeError):
    """Infrastructure failure. The caller records SYSTEM_FAILURE; never a code verdict."""


@dataclass(frozen=True)
class RunResult:
    exit_code: int                     # -1 when killed by timeout
    timed_out: bool
    duration_ms: int
    stdout: str                        # capped: head and tail of the stream
    stderr: str
    truncated: bool
    artifacts: dict[str, str] = field(default_factory=dict)   # collected path -> sha256
    runner: str = ""


@dataclass(frozen=True)
class Upload:
    """A file placed into the work directory for one run (holdout features with columns
    blanked, a grader harness, sealed tests). Never holdout labels."""
    dest: str
    content: bytes


@runtime_checkable
class SandboxRunner(Protocol):
    name: str                          # "local-container" | "tokenfactory-sandbox" | "process-unisolated"
    isolated: bool

    def prepare(self, repo_dir: str, *, python: str, install: str, timeout_s: int) -> str:
        """Copy the repository snapshot into a fresh environment, run the contract's install
        command (network allowed only here), and return a reusable environment id."""

    def fork(self, env_id: str) -> str:
        """Independent copy of an environment's filesystem. Used once per candidate."""

    def write_files(self, env_id: str, files: dict[str, bytes | None]) -> None:
        """Create or replace files in the work directory; None deletes a file."""

    def read_file(self, env_id: str, path: str, max_bytes: int = 2_000_000) -> bytes:
        """Read one file from the work directory. Raises FileNotFoundError if absent."""

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None, env: dict[str, str] | None = None) -> RunResult:
        """Run argv (no shell) in the work directory with the network disabled. `uploads` are
        written first; files named in `collect` that exist afterwards are hashed into
        `artifacts`."""

    def discard(self, env_id: str) -> None:
        """Delete the environment. Idempotent."""
