"""SandboxRunner: the only way candidate code is executed (frozen interface).

Implementations:
    local.py         hardened local container: no network, non-root, read-only base,
                     tmpfs work dir, CPU/RAM/time/output limits          (Codex card C2)
    tokenfactory.py  Nebius Token Factory Sandboxes (microVM, branchable images),
                     activated with the Nebius key and beta access       (Codex card C13)

Guarantees every implementation must provide (tested in tests/test_repair/test_sandbox_*):
    * No outbound network during `run` (dependency install happens in `prepare` only).
    * Nothing outside the environment's own copy of the repository is visible, in
      particular no sealed tests, holdout labels, provider keys or host paths.
    * `fork` copies filesystem state; the parent is never mutated by a child's runs.
    * `run` never raises for a non-zero exit: it returns the exit code. It raises
      SandboxError only for infrastructure failures (cannot start, lost environment).
    * Output is capped and returned as head+tail; digests are computed over full files.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


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
    artifacts: dict[str, str] = field(default_factory=dict)   # path (relative to /out) -> sha256
    runner: str = ""


@dataclass(frozen=True)
class Upload:
    """A file placed into the environment for one run (for example the holdout features or
    the grader's parity harness). `dest` is relative to the environment work directory."""
    dest: str
    content: bytes


@runtime_checkable
class SandboxRunner(Protocol):
    name: str                          # "local-container" | "tokenfactory-sandbox"

    def prepare(self, repo_dir: str, *, python: str, install: str, timeout_s: int) -> str:
        """Copy the repository snapshot into a fresh environment, run the contract's install
        command (network allowed only here), and return a reusable environment id."""

    def fork(self, env_id: str) -> str:
        """Independent copy of an environment's filesystem. Used once per candidate."""

    def write_files(self, env_id: str, files: dict[str, bytes]) -> None:
        """Replace files inside the environment's repository copy (applies a candidate's edits)."""

    def read_file(self, env_id: str, path: str, max_bytes: int = 200_000) -> bytes:
        """Read one file from the environment's repository copy or its /out directory."""

    def run(self, env_id: str, argv: list[str], *, timeout_s: int, uploads: list[Upload] | None = None,
            collect: list[str] | None = None) -> RunResult:
        """Run argv (no shell) inside the environment with the network disabled. `uploads` are
        written first; files named in `collect` (relative to /out) are hashed into `artifacts`."""

    def discard(self, env_id: str) -> None:
        """Delete the environment. Idempotent."""
