"""Policy gate: the only path from a proposal to the runner.

Deterministic and model-free. A proposal is ALLOWED only if the template is in
the approved registry, its parameters pass the template's schema, every cited
hypothesis/source exists, the remaining budget covers it and an identical
experiment has not already run. Denials carry a reason code and never reach a
worker (FR-009, B13).
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import ValidationError

from app.autopilot import mlcore
from app.autopilot.schemas import Experiment, ExperimentProposal
from app.autopilot.templates import TEMPLATES, validate_params

CODE_VERSION = "autopilot-templates/1"


def signature(inputs_digest: str, protocol_hash: str, template_id: str, params: dict[str, Any]) -> str:
    return mlcore.canonical_hash({"inputs": inputs_digest, "protocol": protocol_hash, "template": template_id,
                                  "params": params, "code": CODE_VERSION})[:24]


def check(raw: dict[str, Any] | ExperimentProposal, state: dict[str, Any]) -> dict[str, Any]:
    """state keys: hypotheses, evidence, budget, experiments, protocol_hash, inputs_digest,
    numeric_features, labels_available, limits."""
    def deny(code: str, reason: str) -> dict[str, Any]:
        return {"allow": False, "code": code, "reason": reason, "experiment": None}

    try:
        p = raw if isinstance(raw, ExperimentProposal) else ExperimentProposal.model_validate(raw)
    except ValidationError as e:
        return deny("E_PROPOSAL_INVALID", f"schema: {e.errors()[0]['msg']}")
    spec = TEMPLATES.get(p.template_id)
    if spec is None:
        return deny("E_PROPOSAL_INVALID", f"unknown template {p.template_id!r}")
    hyp_ids = {h["hypothesis_id"] for h in state["hypotheses"]}
    if not set(p.hypothesis_ids) <= hyp_ids:
        return deny("E_PROPOSAL_INVALID", "references an unknown hypothesis")
    ok, why, params = validate_params(p.template_id, p.parameters, {"numeric": state["numeric_features"]})
    if not ok:
        return deny("E_PROPOSAL_INVALID", why)
    accepted = {e["source_ref"] for e in state.get("evidence", []) if e["status"] == "ACCEPTED"}
    if not set(p.source_refs) <= accepted:
        return deny("E_PROPOSAL_INVALID", "cites a source that was not accepted by the source gate")
    if spec.needs_source and not p.source_refs:
        return deny("E_SOURCE_REQUIRED", "this correction needs a cited, accepted source")
    if spec.requires_labels and not state["labels_available"]:
        return deny("E_EVIDENCE_MISSING", "template needs labelled current rows")
    b = state["budget"]
    if b["experiments_used"] >= b["max_experiments"]:
        return deny("E_BUDGET_EXCEEDED", "experiment count budget exhausted")
    if b["cost_units_used"] + spec.cost_units > b["max_cost_units"]:
        return deny("E_BUDGET_EXCEEDED", "cost budget would be exceeded")
    sig = signature(state["inputs_digest"], state["protocol_hash"], p.template_id, params)
    if any(e["signature"] == sig and e["status"] in ("DONE", "FAILED", "TIMEOUT") for e in state["experiments"]):
        return deny("E_DUPLICATE", "an identical experiment already ran in this investigation")
    exp = Experiment(experiment_id=f"E{len(state['experiments']) + 1}-{uuid.uuid4().hex[:6]}",
                     template_id=p.template_id, kind=spec.kind, hypothesis_ids=p.hypothesis_ids,
                     parameters=params, source_refs=p.source_refs, signature=sig,
                     protocol_ref=state["protocol_hash"], estimated_cost_units=spec.cost_units,
                     limits=state["limits"],
                     expected_outcomes={h["hypothesis_id"]: h["prediction_by_template"].get(p.template_id)
                                        for h in state["hypotheses"] if h["hypothesis_id"] in p.hypothesis_ids
                                        and h["prediction_by_template"].get(p.template_id)},
                     policy={"decision": "ALLOW", "checks": ["template", "params", "refs", "labels",
                                                             "budget", "duplicate"]})
    return {"allow": True, "code": "ALLOW", "reason": "all policy checks passed", "experiment": exp}


ENVELOPE_SCHEMA = "autopilot-envelope/1"


def envelope(state: dict[str, Any], exp: dict[str, Any], eval_protocol_digest: str) -> dict[str, Any]:
    """Everything that defines an experiment, digested before it runs. The digest covers
    every field except itself, so any later change to the record is detectable when
    compared with the digest emitted at admission. Integrity, not a signature."""
    env = {
        "schema_version": ENVELOPE_SCHEMA,
        "run_id": state["run_id"],
        "experiment_id": exp["experiment_id"],
        "experiment_kind": exp["kind"],
        "hypothesis_ids": exp["hypothesis_ids"],
        "template_id": f"{exp['template_id']}@{CODE_VERSION}",
        "typed_parameters": exp["parameters"],
        "dataset_manifest_digest": state["inputs_digest"],
        "split_protocol_digest": state["protocol"]["protocol_hash"],
        "eval_protocol_digest": eval_protocol_digest,
        "expected_observation_buckets": exp.get("expected_outcomes") or {},
        "external_evidence_refs": exp.get("source_refs") or [],
        "resource_ceiling": {"wall_seconds": state["limits"]["max_seconds"],
                             "cost_units": exp["estimated_cost_units"], "attempts": 1},
        "authorized_by": "policy:allow-list/" + CODE_VERSION,
    }
    env["envelope_digest"] = "sha256:" + mlcore.canonical_hash(env)
    return env


def envelope_intact(env: dict[str, Any]) -> bool:
    body = {k: v for k, v in env.items() if k != "envelope_digest"}
    return env.get("envelope_digest") == "sha256:" + mlcore.canonical_hash(body)
