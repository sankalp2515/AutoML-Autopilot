"""Typed records for an Autopilot investigation.

Every record that crosses a trust boundary (planner output, external evidence,
experiment proposals) is a Pydantic model so malformed input is rejected before it
can influence execution. Records that only the system writes (findings,
observations, receipts) are typed too, so the API and the export share one shape.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Status(str, Enum):
    CREATED = "CREATED"
    VALIDATING = "VALIDATING"
    BASELINING = "BASELINING"
    INVESTIGATING = "INVESTIGATING"
    RESEARCHING = "RESEARCHING"
    PROPOSING = "PROPOSING"
    POLICY_CHECK = "POLICY_CHECK"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    TESTING_CORRECTION = "TESTING_CORRECTION"
    VERIFYING = "VERIFYING"
    REVIEW_READY = "REVIEW_READY"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL = {Status.REVIEW_READY, Status.APPROVED, Status.REJECTED, Status.INCONCLUSIVE,
            Status.BLOCKED, Status.FAILED, Status.CANCELLED}

# Allowed transitions (System Design §4.1). Anything else is a programming error
# and raises — the state machine can never skip the verifier.
TRANSITIONS: dict[Status, set[Status]] = {
    Status.CREATED: {Status.VALIDATING, Status.FAILED, Status.CANCELLED},
    Status.VALIDATING: {Status.BASELINING, Status.BLOCKED, Status.FAILED, Status.CANCELLED},
    Status.BASELINING: {Status.INVESTIGATING, Status.TESTING_CORRECTION, Status.INCONCLUSIVE,
                        Status.FAILED, Status.CANCELLED},
    Status.INVESTIGATING: {Status.RESEARCHING, Status.PROPOSING, Status.TESTING_CORRECTION,
                           Status.INCONCLUSIVE, Status.BLOCKED, Status.FAILED, Status.CANCELLED},
    Status.RESEARCHING: {Status.PROPOSING, Status.FAILED, Status.CANCELLED},
    Status.PROPOSING: {Status.POLICY_CHECK, Status.TESTING_CORRECTION, Status.INCONCLUSIVE,
                       Status.BLOCKED, Status.FAILED, Status.CANCELLED},
    Status.POLICY_CHECK: {Status.EXECUTING, Status.PROPOSING, Status.BLOCKED, Status.INCONCLUSIVE,
                          Status.CANCELLED},
    Status.EXECUTING: {Status.OBSERVING, Status.FAILED, Status.CANCELLED},
    Status.OBSERVING: {Status.INVESTIGATING, Status.PROPOSING, Status.TESTING_CORRECTION,
                       Status.INCONCLUSIVE, Status.BLOCKED, Status.CANCELLED},
    Status.TESTING_CORRECTION: {Status.VERIFYING, Status.INCONCLUSIVE, Status.FAILED, Status.CANCELLED},
    Status.VERIFYING: {Status.REVIEW_READY, Status.REJECTED, Status.INCONCLUSIVE, Status.FAILED},
    Status.REVIEW_READY: {Status.APPROVED, Status.REJECTED},
}


class Decision(str, Enum):
    DIAGNOSIS_SUPPORTED = "DIAGNOSIS_SUPPORTED"
    CORRECTION_VERIFIED = "CORRECTION_VERIFIED"
    REJECT = "REJECT"
    INCONCLUSIVE = "INCONCLUSIVE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Cause(str, Enum):
    """Failure-cause families a hypothesis can name. A hypothesis is always a
    *possible* cause; only an executed experiment can support it."""
    FEATURE_SHIFT = "FEATURE_SHIFT"
    PREPROCESSING_MISMATCH = "PREPROCESSING_MISMATCH"
    THRESHOLD_MISCALIBRATION = "THRESHOLD_MISCALIBRATION"
    LABEL_PRIOR_SHIFT = "LABEL_PRIOR_SHIFT"
    DATA_CONTRACT_BREAK = "DATA_CONTRACT_BREAK"
    LIBRARY_API_CHANGE = "LIBRARY_API_CHANGE"
    SEGMENT_FAILURE = "SEGMENT_FAILURE"


class Finding(BaseModel):
    """A machine-computed observation. Never written by a model."""
    finding_id: str
    kind: str
    title: str
    value: Any
    method: str
    sample_count: int
    severity: Literal["info", "warn", "critical"] = "info"
    detail: dict[str, Any] = Field(default_factory=dict)
    created_at: str = Field(default_factory=utcnow)


class Hypothesis(BaseModel):
    hypothesis_id: str
    cause: Cause
    statement: str
    supporting_findings: list[str] = Field(default_factory=list)
    counter_findings: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    prediction_by_template: dict[str, str] = Field(default_factory=dict)
    belief_weight: float = Field(ge=0.0, le=1.0)
    calibration: Literal["uncalibrated_heuristic"] = "uncalibrated_heuristic"
    status: Literal["PROPOSED", "SUPPORTED", "REFUTED", "UNRESOLVED"] = "PROPOSED"
    revision: int = 0
    proposed_by: str = "deterministic"
    consistent_observations: int = 0


class CandidateScore(BaseModel):
    template_id: str
    kind: Literal["diagnostic", "corrective"]
    eligible: bool
    reason: str
    expected_information_bits: float
    estimated_cost_units: float
    duplicate_penalty: float
    score: float
    predicted_buckets: dict[str, str] = Field(default_factory=dict)


class ExperimentProposal(BaseModel):
    """What the planner/selector asks the policy gate to run."""
    model_config = ConfigDict(extra="forbid")
    template_id: str
    hypothesis_ids: list[str] = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    source_refs: list[str] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=600)


class Experiment(BaseModel):
    experiment_id: str
    template_id: str
    kind: Literal["diagnostic", "corrective"]
    hypothesis_ids: list[str]
    parameters: dict[str, Any]
    source_refs: list[str] = Field(default_factory=list)
    expected_outcomes: dict[str, str] = Field(default_factory=dict)
    signature: str
    protocol_ref: str
    estimated_cost_units: float
    limits: dict[str, Any]
    policy: dict[str, Any] = Field(default_factory=dict)
    status: Literal["PROPOSED", "DENIED", "RUNNING", "DONE", "FAILED", "TIMEOUT", "CANCELLED"] = "PROPOSED"
    observation: dict[str, Any] | None = None
    actual_seconds: float | None = None
    error: str | None = None
    created_at: str = Field(default_factory=utcnow)


class ExternalEvidence(BaseModel):
    source_ref: str
    url: str
    title: str
    publisher: str
    source_type: Literal["official_docs", "release_notes", "maintainer_issue", "third_party"]
    fetched_at: str
    version_match: Literal["exact", "compatible", "mismatch", "unknown"]
    relevance: float
    status: Literal["ACCEPTED", "CONTEXT_ONLY", "REJECTED_UNTRUSTED", "REJECTED_IRRELEVANT", "REJECTED_OUTDATED"]
    caveats: list[str] = Field(default_factory=list)
    excerpt: str = ""
    excerpt_sha256: str = ""
    extracted: bool = False


class ProviderCall(BaseModel):
    """Receipt for one external call (Tavily or Token Factory)."""
    provider: Literal["tavily", "nebius_token_factory", "nvidia_api"]
    operation: str
    correlation_id: str
    request_id: str | None = None      # only when the provider returned one
    model: str | None = None
    ok: bool
    status_code: int | None = None
    error: str | None = None
    latency_ms: int
    result_count: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    est_cost_usd: float | None = None   # list-price estimate (autopilot/pricing.py), never a bill
    at: str = Field(default_factory=utcnow)


class CreateInvestigation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_id: str = Field(min_length=2, max_length=16)
    seed: int = Field(default=7, ge=0, le=10_000)
    planner: Literal["auto", "deterministic", "nemotron"] = "auto"
    research: Literal["auto", "off"] = "auto"
    max_experiments: int = Field(default=4, ge=1, le=8)
    max_cost_units: float = Field(default=24.0, gt=0, le=100)
    auto_run: bool = True


class ReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["approve", "reject"]
    reviewer: str = Field(min_length=2, max_length=80)
    note: str = Field(default="", max_length=500)
