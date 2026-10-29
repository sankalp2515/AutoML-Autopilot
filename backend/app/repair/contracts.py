"""Frozen interfaces for v5 repository repair (contract version 1 / ECC 5.0).

Every record that crosses a trust boundary is a Pydantic model, so malformed input
from a repository, the model or a sandbox is rejected before it can influence
execution. Change these shapes only with a versioned PR reviewed by both lanes.

    RepoContract   autopilot.yaml, written by the repository owner (untrusted input)
    ECC            Engineering Change Contract, frozen by the platform per task
    AgentAction    one tool call the model may request (validated, then policy-checked)
    ToolResult     what the platform returns to the model
    CodeReceipt / MLReceipt / VerifiedChangeReceipt   oracle outputs; only oracles write them
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.autopilot.schemas import utcnow

CONTRACT_VERSION = 1
ECC_SCHEMA_VERSION = "5.0"

# Always immutable, whatever the repository declares: the contract itself and anything that
# changes which tests run or how (a conftest.py that skips tests is a fake pass).
ALWAYS_IMMUTABLE = ("autopilot.yaml", "**/conftest.py", "conftest.py", "pytest.ini", "setup.cfg",
                    "tox.ini", "pyproject.toml", ".github/")

# Placeholders the platform substitutes into contract commands. Nothing else is expanded.
PLACEHOLDERS = {"artifacts", "holdout_features", "predictions", "rows", "out"}
_PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
_SHELL_META = re.compile(r"[;&|`$<>\\\n]")
_PKG = re.compile(r"^[A-Za-z0-9_.-]+$")
_ENTRYPOINT = re.compile(r"^[A-Za-z_][\w.]*:[A-Za-z_]\w*$")
_TEST_ID = re.compile(r"^[\w./-]+\.py(::[\w\[\]-]+)*$")


def canonical_digest(obj: Any) -> str:
    """sha256 over canonical JSON (sorted keys, no whitespace)."""
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def safe_relpath(p: str) -> str:
    """A repository-relative POSIX path that cannot escape the repository root."""
    if not p or "\x00" in p or "\\" in p:
        raise ValueError(f"invalid path: {p!r}")
    pp = PurePosixPath(p)
    if pp.is_absolute() or ".." in pp.parts or (pp.parts and pp.parts[0].endswith(":")):
        raise ValueError(f"path escapes the repository: {p!r}")
    return str(pp) + ("/" if p.endswith("/") else "")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ── autopilot.yaml ────────────────────────────────────────────────────────────
class Commands(_Strict):
    test: str
    train: str | None = None
    predict: str | None = None

    @field_validator("test", "train", "predict")
    @classmethod
    def _command(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if len(v) > 300 or _SHELL_META.search(v):
            raise ValueError("commands are argv strings: no shell operators, redirects or substitutions")
        unknown = set(_PLACEHOLDER.findall(v)) - PLACEHOLDERS
        if unknown:
            raise ValueError(f"unknown placeholders: {sorted(unknown)}")
        return v


class DataSpec(_Strict):
    train: str
    holdout_features: str
    id_column: str
    target: str
    positive_label: str | int = 1
    # Columns that are only known after the outcome (refunds, cancellation calls...). The ML
    # oracle's leakage check rejects a candidate whose model depends on any of them.
    unavailable_at_prediction: list[str] = Field(default_factory=list, max_length=100)
    # Protected group for the segment guardrail. Holdout labels and this column stay sealed.
    segment_column: str | None = None
    segment_max_drop: float = Field(default=0.05, ge=0, le=0.5)

    @field_validator("train", "holdout_features")
    @classmethod
    def _path(cls, v: str) -> str:
        return safe_relpath(v)


class TaskSpec(_Strict):
    type: Literal["restore", "preserve", "improve"]
    metric: Literal["roc_auc", "f1", "recall", "precision", "balanced_accuracy"] = "roc_auc"
    fail_to_pass: list[str] = Field(default_factory=list, max_length=50)
    ml_invariants: list[Literal["train_serve_parity", "holdout_non_inferior", "holdout_improves",
                                "segment_guardrail", "no_target_leakage"]] = Field(default_factory=list)

    @field_validator("fail_to_pass")
    @classmethod
    def _ids(cls, v: list[str]) -> list[str]:
        for t in v:
            if not _TEST_ID.match(t):
                raise ValueError(f"not a pytest node id: {t!r}")
        return v


class PolicySpec(_Strict):
    allowed_edit_roots: list[str] = Field(min_length=1, max_length=20)
    immutable: list[str] = Field(default_factory=list, max_length=50)
    immutable_existing_tests: bool = True

    @field_validator("allowed_edit_roots", "immutable")
    @classmethod
    def _paths(cls, v: list[str]) -> list[str]:
        return [safe_relpath(p) for p in v]


class LimitsSpec(_Strict):
    test_seconds: int = Field(default=120, ge=5, le=900)
    train_seconds: int = Field(default=120, ge=5, le=1800)


class ArtifactsSpec(_Strict):
    """Where the train command leaves the model, relative to {artifacts}. The serve entrypoint
    is called as `fn(payload: dict, model)` with `model = joblib.load(<this file>)`."""
    model: str = "model.joblib"

    @field_validator("model")
    @classmethod
    def _p(cls, v: str) -> str:
        return safe_relpath(v)


class RepoContract(_Strict):
    """`autopilot.yaml` at the repository root. Written by the repo owner; validated, never executed."""
    version: Literal[1]
    name: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    python: str = Field(default="3.12", pattern=r"^3\.\d{1,2}$")
    install: str = "pip install -r requirements.txt"
    commands: Commands
    entrypoints: dict[Literal["serve"], str] = Field(default_factory=dict)
    artifacts: ArtifactsSpec = Field(default_factory=ArtifactsSpec)
    data: DataSpec | None = None
    task: TaskSpec
    policy: PolicySpec
    limits: LimitsSpec = Field(default_factory=LimitsSpec)

    @field_validator("install")
    @classmethod
    def _install(cls, v: str) -> str:
        if _SHELL_META.search(v) or not v.startswith("pip install"):
            raise ValueError("install must be a single `pip install ...` command")
        return v

    @field_validator("entrypoints")
    @classmethod
    def _entry(cls, v: dict[str, str]) -> dict[str, str]:
        for name, ep in v.items():
            if not _ENTRYPOINT.match(ep):
                raise ValueError(f"entrypoint {name} must look like 'package.module:function'")
        return v

    @model_validator(mode="after")
    def _ml_needs_data(self) -> "RepoContract":
        ml = set(self.task.ml_invariants)
        if ml and (self.data is None or not self.commands.train or not self.commands.predict):
            raise ValueError("ML invariants need data, a train command and a predict command")
        if "train_serve_parity" in ml and "serve" not in self.entrypoints:
            raise ValueError("train_serve_parity needs entrypoints.serve")
        return self

    @classmethod
    def from_yaml(cls, text: str) -> "RepoContract":
        if len(text) > 20_000:
            raise ValueError("autopilot.yaml is too large")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError("autopilot.yaml must be a mapping")
        return cls.model_validate(data)

    def digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json"))


# ── Sealed grading bundle ─────────────────────────────────────────────────────
class SealedManifest(_Strict):
    """`sealed/manifest.yaml`: trusted grader material, never shown to the agent.

    Predictions written by the repository's predict command must have columns
    `<id_column>,score` (probability of the positive label) and may add `decision` (0/1)
    for threshold metrics. Holdout labels stay on the oracle side and are never copied
    into a sandbox."""
    version: Literal[1]
    labels: str                                   # CSV: <id_column>,<target>[,<segment_column>]
    tests: str | None = None                      # directory of sealed pytest files (copied at grading)
    # Environment for sealed tests. Values may use {artifacts}, {holdout_features}, {predictions}.
    env: dict[str, str] = Field(default_factory=dict)
    parity_rows: int = Field(default=200, ge=10, le=5000)
    # Last known good metric, used when the unpatched repository cannot produce predictions.
    baseline_metric: float | None = Field(default=None, ge=0, le=1)

    @field_validator("labels", "tests")
    @classmethod
    def _p(cls, v: str | None) -> str | None:
        return None if v is None else safe_relpath(v)

    @field_validator("env")
    @classmethod
    def _env(cls, v: dict[str, str]) -> dict[str, str]:
        for k, val in v.items():
            if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,40}", k):
                raise ValueError(f"bad env name {k!r}")
            if set(_PLACEHOLDER.findall(val)) - PLACEHOLDERS:
                raise ValueError(f"unknown placeholder in {k}")
        return v

    @classmethod
    def from_yaml(cls, text: str) -> "SealedManifest":
        return cls.model_validate(yaml.safe_load(text))


# ── Engineering Change Contract ───────────────────────────────────────────────
class TierTokens(_Strict):
    """Per-tier ceilings on total tokens (prompt + completion) for one task."""
    ultra: int = Field(default=12_000, ge=0)
    super: int = Field(default=160_000, ge=0)
    nano: int = Field(default=20_000, ge=0)


class Limits(_Strict):
    wall_seconds: int = Field(default=1800, ge=60, le=7200)
    sandbox_seconds: int = Field(default=900, ge=30, le=7200)
    max_candidates: int = Field(default=2, ge=1, le=4)
    max_iterations: int = Field(default=8, ge=1, le=30)      # edit/run rounds per candidate
    max_tool_calls: int = Field(default=60, ge=5, le=300)
    max_diff_lines: int = Field(default=200, ge=5, le=2000)
    model_tokens: TierTokens = Field(default_factory=TierTokens)
    tavily_calls: int = Field(default=3, ge=0, le=10)


class SuccessContract(_Strict):
    task_type: Literal["restore", "preserve", "improve"]
    metric: str
    fail_to_pass: list[str]
    pass_to_pass: Literal["all_existing"] = "all_existing"
    ml_invariants: list[str]
    non_inferiority_margin: float = Field(default=0.02, ge=0, le=0.2)
    parity_tolerance: float = Field(default=1e-6, gt=0, le=1e-2)


class ECC(_Strict):
    """Frozen per task before the agent starts. The agent may request an amendment; only a
    trusted reviewer can approve one, which creates a new ECC with a new digest."""
    schema_version: Literal["5.0"] = ECC_SCHEMA_VERSION
    task_id: str
    created_at: str = Field(default_factory=utcnow)
    base_commit_sha: str = Field(pattern=r"^[0-9a-f]{40}$")
    repo_snapshot_digest: str
    repo_contract_digest: str
    target: str = Field(min_length=3, max_length=500)
    allowed_edit_roots: list[str]
    immutable: list[str]
    immutable_existing_tests: bool
    success: SuccessContract
    dataset_digests: dict[str, str] = Field(default_factory=dict)   # role -> sha256 of file bytes
    sealed_digest: str | None = None        # digest of the sealed grading bundle (never shown to the agent)
    environment: dict[str, str] = Field(default_factory=dict)       # image, python, lockfile digest
    limits: Limits = Field(default_factory=Limits)
    approval_policy: Literal["draft_pr_only", "patch_only"] = "draft_pr_only"
    contract_digest: str = ""

    def compute_digest(self) -> str:
        return canonical_digest(self.model_dump(mode="json", exclude={"contract_digest"}))

    def sealed(self) -> "ECC":
        """Return a copy with its digest filled in."""
        return self.model_copy(update={"contract_digest": self.compute_digest()})

    def intact(self) -> bool:
        return bool(self.contract_digest) and self.contract_digest == self.compute_digest()

    @classmethod
    def build(cls, *, task_id: str, base_commit_sha: str, repo_snapshot_digest: str, contract: RepoContract,
              target: str, dataset_digests: dict[str, str] | None = None, sealed_digest: str | None = None,
              environment: dict[str, str] | None = None, limits: Limits | None = None) -> "ECC":
        t = contract.task
        return cls(
            task_id=task_id, base_commit_sha=base_commit_sha, repo_snapshot_digest=repo_snapshot_digest,
            repo_contract_digest=contract.digest(), target=target,
            allowed_edit_roots=contract.policy.allowed_edit_roots,
            immutable=sorted(set(contract.policy.immutable) | set(ALWAYS_IMMUTABLE)),
            immutable_existing_tests=contract.policy.immutable_existing_tests,
            success=SuccessContract(task_type=t.type, metric=t.metric, fail_to_pass=t.fail_to_pass,
                                    ml_invariants=list(t.ml_invariants)),
            dataset_digests=dataset_digests or {}, sealed_digest=sealed_digest,
            environment=environment or {}, limits=limits or Limits(),
        ).sealed()


# ── Model tool protocol ───────────────────────────────────────────────────────
# Plain JSON actions, schema-validated here and policy-checked by tools.py. We do not
# rely on provider-native tool calling, so the NVIDIA and Nebius endpoints behave the same.
class ListFiles(_Strict):
    action: Literal["list_files"]
    path: str = "."
    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        return "." if v in ("", ".", "./") else safe_relpath(v)


class ReadFile(_Strict):
    action: Literal["read_file"]
    path: str
    start_line: int = Field(default=1, ge=1)
    end_line: int | None = Field(default=None, ge=1)      # inclusive; at most 200 lines returned
    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        return safe_relpath(v)


class Search(_Strict):
    action: Literal["search"]
    pattern: str = Field(min_length=1, max_length=200)    # literal text, not a regex
    path: str = "."
    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        return "." if v in ("", ".", "./") else safe_relpath(v)


class ApplyEdit(_Strict):
    """Exact search-and-replace. `old` must occur exactly once (or be empty to create a new file).
    The platform, not the model, produces the diff."""
    action: Literal["apply_edit"]
    path: str
    old: str = Field(max_length=20_000)
    new: str = Field(max_length=20_000)
    rationale: str = Field(default="", max_length=500)
    @field_validator("path")
    @classmethod
    def _p(cls, v: str) -> str:
        return safe_relpath(v)


class Run(_Strict):
    """Run a contract command. The model chooses which, never the command line."""
    action: Literal["run"]
    kind: Literal["test", "train", "predict"]
    selector: str | None = Field(default=None, max_length=200)   # pytest node id(s) for kind=test
    @field_validator("selector")
    @classmethod
    def _sel(cls, v: str | None) -> str | None:
        if v is not None and not all(_TEST_ID.match(s) for s in v.split()):
            raise ValueError("selector must be pytest node ids")
        return v


class Finish(_Strict):
    action: Literal["finish"]
    hypothesis_id: str | None = None
    summary: str = Field(max_length=1500)
    expected_behaviour_change: str = Field(default="", max_length=500)


AgentAction = Annotated[Union[ListFiles, ReadFile, Search, ApplyEdit, Run, Finish], Field(discriminator="action")]


class AgentStep(_Strict):
    """One model reply: brief reasoning plus exactly one action."""
    thought: str = Field(default="", max_length=1200)
    act: AgentAction


class ToolResult(BaseModel):
    ok: bool
    action: str
    output: str = ""                    # already compacted (tails, ranges) before it reaches the model
    truncated: bool = False
    denied: str | None = None           # policy rule that blocked the action, with a recovery hint
    exit_code: int | None = None
    duration_ms: int | None = None


# ── Candidates and receipts ───────────────────────────────────────────────────
class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    INCONCLUSIVE = "INCONCLUSIVE"


class Decision(str, Enum):
    VERIFIED = "VERIFIED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    SYSTEM_FAILURE = "SYSTEM_FAILURE"


class Check(BaseModel):
    name: str
    status: CheckStatus
    detail: str
    evidence: dict[str, Any] = Field(default_factory=dict)


class Candidate(BaseModel):
    candidate_id: str
    task_id: str
    parent: str | None = None           # candidate_id it branched from, or None for the base
    hypothesis_id: str | None = None
    diff: str = ""
    diff_digest: str = ""
    files_changed: list[str] = Field(default_factory=list)
    iterations: int = 0
    status: Literal["EXPLORING", "FROZEN", "REJECTED", "VERIFIED", "ABANDONED"] = "EXPLORING"
    summary: str = ""
    created_at: str = Field(default_factory=utcnow)


class _Receipt(BaseModel):
    task_id: str
    candidate_id: str
    contract_digest: str
    diff_digest: str
    checks: list[Check]
    decision: Decision
    reasons: list[str] = Field(default_factory=list)
    runner: str                          # "local-container" | "tokenfactory-sandbox"
    created_at: str = Field(default_factory=utcnow)
    receipt_digest: str = ""

    def sealed(self):
        return self.model_copy(update={"receipt_digest": canonical_digest(
            self.model_dump(mode="json", exclude={"receipt_digest"}))})


class CodeReceipt(_Receipt):
    kind: Literal["code"] = "code"


class MLReceipt(_Receipt):
    kind: Literal["ml"] = "ml"
    metrics: dict[str, Any] = Field(default_factory=dict)
    holdout_opened: bool = False


class VerifiedChangeReceipt(BaseModel):
    task_id: str
    candidate_id: str
    contract_digest: str
    diff_digest: str
    code_receipt_digest: str
    ml_receipt_digest: str | None       # None only when the contract has no ML invariants
    decision: Decision
    reasons: list[str]
    created_at: str = Field(default_factory=utcnow)

    @classmethod
    def combine(cls, code: CodeReceipt, ml: MLReceipt | None, ml_required: bool) -> "VerifiedChangeReceipt":
        """A candidate with one PASS and one FAIL is rejected; anything missing is not a PASS."""
        reasons = list(code.reasons) + (list(ml.reasons) if ml else [])
        decisions = [code.decision] + ([ml.decision] if ml else [])
        if ml_required and ml is None:
            decision, reasons = Decision.INCONCLUSIVE, reasons + ["E_ML_RECEIPT_MISSING"]
        elif ml and (ml.contract_digest != code.contract_digest or ml.diff_digest != code.diff_digest):
            decision, reasons = Decision.INCONCLUSIVE, reasons + ["E_RECEIPT_MISMATCH"]
        elif Decision.SYSTEM_FAILURE in decisions:
            decision = Decision.SYSTEM_FAILURE
        elif Decision.REJECTED in decisions:
            decision = Decision.REJECTED
        elif Decision.INCONCLUSIVE in decisions:
            decision = Decision.INCONCLUSIVE
        else:
            decision = Decision.VERIFIED
        return cls(task_id=code.task_id, candidate_id=code.candidate_id, contract_digest=code.contract_digest,
                   diff_digest=code.diff_digest, code_receipt_digest=code.receipt_digest,
                   ml_receipt_digest=ml.receipt_digest if ml else None, decision=decision, reasons=reasons)


# ── Task lifecycle and events ─────────────────────────────────────────────────
class TaskStatus(str, Enum):
    CREATED = "CREATED"
    SNAPSHOTTING = "SNAPSHOTTING"
    MAPPING = "MAPPING"
    INVESTIGATING = "INVESTIGATING"
    DIAGNOSING = "DIAGNOSING"
    PATCHING = "PATCHING"
    CODE_VERIFYING = "CODE_VERIFYING"
    ML_VERIFYING = "ML_VERIFYING"
    SELECTING = "SELECTING"
    REVIEW_READY = "REVIEW_READY"
    PR_DRAFTED = "PR_DRAFTED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_TASK = {TaskStatus.REVIEW_READY, TaskStatus.PR_DRAFTED, TaskStatus.REJECTED, TaskStatus.INCONCLUSIVE,
                 TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.CANCELLED}

# Allowed forward moves. Loops back to INVESTIGATING/PATCHING are allowed within budget.
TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.SNAPSHOTTING},
    TaskStatus.SNAPSHOTTING: {TaskStatus.MAPPING},
    TaskStatus.MAPPING: {TaskStatus.INVESTIGATING},
    TaskStatus.INVESTIGATING: {TaskStatus.DIAGNOSING, TaskStatus.PATCHING},
    TaskStatus.DIAGNOSING: {TaskStatus.INVESTIGATING, TaskStatus.PATCHING},
    TaskStatus.PATCHING: {TaskStatus.CODE_VERIFYING, TaskStatus.INVESTIGATING},
    TaskStatus.CODE_VERIFYING: {TaskStatus.ML_VERIFYING, TaskStatus.SELECTING, TaskStatus.PATCHING},
    TaskStatus.ML_VERIFYING: {TaskStatus.SELECTING, TaskStatus.PATCHING},
    TaskStatus.SELECTING: {TaskStatus.REVIEW_READY, TaskStatus.REJECTED, TaskStatus.INCONCLUSIVE},
    TaskStatus.REVIEW_READY: {TaskStatus.PR_DRAFTED},
}
ANY_TO = {TaskStatus.BLOCKED, TaskStatus.FAILED, TaskStatus.CANCELLED}


def can_move(src: TaskStatus, dst: TaskStatus) -> bool:
    if src in TERMINAL_TASK and not (src is TaskStatus.REVIEW_READY and dst is TaskStatus.PR_DRAFTED):
        return False
    return dst in ANY_TO or dst in TRANSITIONS.get(src, set())


class Provenance(str, Enum):
    OBSERVED = "OBSERVED"      # measured by platform code or a sandbox run
    RETRIEVED = "RETRIEVED"    # fetched by Tavily; context, not proof
    INFERRED = "INFERRED"      # proposed by the model; must be tested
    VERIFIED = "VERIFIED"      # decided by an oracle


EVENT_TYPES = (
    "task.created", "repo.snapshotted", "repo.mapped", "baseline.reproduced", "hypothesis.proposed",
    "hypothesis.revised", "tavily.source.retrieved", "diagnostic.executed", "candidate.created",
    "patch.applied", "tool.denied", "tests.failed", "tests.passed", "candidate.frozen",
    "code_oracle.passed", "code_oracle.rejected", "ml_oracle.passed", "ml_oracle.rejected",
    "candidate.verified", "candidate.rejected", "budget.exhausted", "pr.drafted", "task.cancelled",
    "task.failed",
)


class Event(BaseModel):
    task_id: str
    seq: int
    type: Literal[EVENT_TYPES]  # type: ignore[valid-type]
    actor: Literal["platform", "agent", "code_oracle", "ml_oracle", "reviewer", "sandbox"]
    provenance: Provenance = Provenance.OBSERVED
    candidate_id: str | None = None
    artifact_digest: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    at: str = Field(default_factory=utcnow)
