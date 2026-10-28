"""Investigation engine — the state machine that drives one investigation.

`Investigator.step()` advances exactly one stage and is idempotent with respect
to persisted state, so the API can run an investigation to completion
(auto_run) or one confirmed step at a time (POST /next). All state lives in a
JSON-serialisable dict; `emit()` appends an event and the store persists both.

Decision authority: hypotheses come from a planner, experiments from the
information-gain selector, admission from the policy gate, numbers from the
runner and verifier. The engine only routes between them.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any, Callable

from app.autopilot import beliefs, mlcore, policy, profiler, research, runner, scenarios
from app.autopilot.planner import DeterministicPlanner, NemotronPlanner, context, has_symptom, prompt_hash
from app.autopilot.schemas import (TERMINAL, TRANSITIONS, Cause, Decision, ExternalEvidence, Finding,
                                   Hypothesis, Status, utcnow)
from app.autopilot.verifier import VERIFIER_VERSION
from app.autopilot.workspace import Workspace

CORRECTIVE_FOR = {
    Cause.PREPROCESSING_MISMATCH: "PREPROCESSING_ABLATION",
    Cause.THRESHOLD_MISCALIBRATION: "THRESHOLD_CANDIDATE",
    Cause.LABEL_PRIOR_SHIFT: "THRESHOLD_CANDIDATE",
    Cause.LIBRARY_API_CHANGE: "DEPENDENCY_COMPAT_FIX",
}


class Cancelled(Exception):
    pass


def new_state(run_id: str, cfg: dict[str, Any], settings: Any) -> dict[str, Any]:
    return {
        "run_id": run_id, "status": Status.CREATED.value, "config": cfg, "created_at": utcnow(),
        "scenario": None, "protocol": None, "inputs": {}, "inputs_digest": None,
        "budget": {"max_experiments": cfg["max_experiments"], "max_cost_units": cfg["max_cost_units"],
                   "experiments_used": 0, "cost_units_used": 0.0, "cpu_seconds": 0.0},
        "limits": {"max_seconds": settings.AUTOPILOT_EXPERIMENT_TIMEOUT_S,
                   "runner": settings.AUTOPILOT_RUNNER},
        "findings": [], "hypotheses": [], "belief_history": [], "rankings": [], "experiments": [],
        "denials": [], "research": {"state": "PENDING", "evidence": [], "calls": [], "query": None,
                                    "mapping": None, "reason_code": None},
        "planner": {"requested": cfg["planner"], "used": None, "calls": [], "note": None},
        "correction": None, "verification": None, "decision": None, "final_test_accesses": [],
        "review": None, "events": [], "seq": 0, "error": None,
    }


class Investigator:
    def __init__(self, state: dict[str, Any], data_dir: str, settings: Any,
                 on_change: Callable[[dict, dict], None] | None = None,
                 cancel_requested: Callable[[], bool] | None = None,
                 tavily: research.TavilyClient | None = None,
                 nemotron: NemotronPlanner | None = None,
                 narrator: Any = None):
        self.s = state
        self.settings = settings
        self.ws = Workspace(Path(data_dir) / "autopilot" / state["run_id"])
        self._on_change = on_change or (lambda s, e: None)
        self._cancel = cancel_requested or (lambda: False)
        self.tavily = tavily
        self.nemotron = nemotron
        self.narrator = narrator

    # ── plumbing ─────────────────────────────────────────────────────────────
    @property
    def status(self) -> Status:
        return Status(self.s["status"])

    def emit(self, kind: str, summary: str, **data: Any) -> None:
        self.s["seq"] += 1
        ev = {"seq": self.s["seq"], "at": utcnow(), "stage": self.s["status"], "kind": kind,
              "summary": summary, "data": data}
        self.s["events"].append(ev)
        self._on_change(self.s, ev)

    def move(self, to: Status, why: str = "") -> None:
        cur = self.status
        if to not in TRANSITIONS.get(cur, set()):
            raise RuntimeError(f"illegal transition {cur.value} → {to.value}")
        self.s["status"] = to.value
        self.emit("transition", f"{cur.value} → {to.value}" + (f": {why}" if why else ""),
                  frm=cur.value, to=to.value)

    def decide(self, decision: Decision, status: Status, reasons: list[str], summary: str,
               next_action: str) -> None:
        self.s["decision"] = {"decision": decision.value, "reason_codes": reasons, "summary": summary,
                              "next_action": next_action, "incumbent_changed": False, "at": utcnow()}
        self._narrate()                  # before the terminal move: clients stop polling at terminal
        self.move(status, decision.value)

    def _narrate(self) -> None:
        """Small-model summary of the final decision. Optional: any failure leaves the
        deterministic decision text as the only summary."""
        if self.narrator is None or self.s.get("stakeholder_summary"):
            return
        try:
            out, call = self.narrator.summarize(self.s)
        except Exception as e:  # noqa: BLE001
            out, call = {"status": "UNAVAILABLE", "text": None, "reason": type(e).__name__}, None
        self.s["stakeholder_summary"] = out
        if call is not None:
            self.s.setdefault("summary_calls", []).append(call.model_dump())
        self.emit("summary", f"Stakeholder summary: {out['status']}")

    def run_to_end(self, max_steps: int = 60) -> dict[str, Any]:
        for _ in range(max_steps):
            if self.status in TERMINAL:
                break
            self.step()
        return self.s

    def step(self) -> None:
        if self.status in TERMINAL:
            return
        if self._cancel():
            self.s["status"] = Status.CANCELLED.value
            self.s["decision"] = {"decision": Decision.CANCELLED.value, "reason_codes": ["E_CANCELLED"],
                                  "summary": "Cancelled by user; no further experiments, incumbent unchanged.",
                                  "next_action": "Start a new linked investigation if needed.",
                                  "incumbent_changed": False, "at": utcnow()}
            self.emit("transition", "→ CANCELLED", to="CANCELLED")
            return
        handler = {
            Status.CREATED: self._validate_start, Status.VALIDATING: self._validate,
            Status.BASELINING: self._baseline, Status.INVESTIGATING: self._investigate,
            Status.RESEARCHING: self._research, Status.PROPOSING: self._propose,
            Status.POLICY_CHECK: self._policy, Status.EXECUTING: self._execute,
            Status.OBSERVING: self._observe, Status.TESTING_CORRECTION: self._correct,
            Status.VERIFYING: self._verify,
        }[self.status]
        try:
            handler()
        except Exception as e:  # noqa: BLE001 — any unexpected failure ends FAILED, never "success"
            self.s["error"] = f"{type(e).__name__}: {str(e)[:300]}"
            self.s["status"] = Status.FAILED.value
            self.s["decision"] = {"decision": Decision.FAILED.value, "reason_codes": ["E_INTERNAL"],
                                  "summary": "Investigation failed; no result is reported and the incumbent "
                                             "is unchanged.", "next_action": "Inspect the error and retry.",
                                  "incumbent_changed": False, "at": utcnow()}
            self.emit("error", self.s["error"])

    # ── stages ───────────────────────────────────────────────────────────────
    def _validate_start(self) -> None:
        self.move(Status.VALIDATING)

    def _validate(self) -> None:
        cfg = self.s["config"]
        pack = scenarios.build(cfg["scenario_id"], cfg["seed"])
        if pack.target not in pack.reference.columns or pack.reference[pack.target].nunique() < 2:
            self.decide(Decision.BLOCKED, Status.BLOCKED, ["E_INPUT_CONTRACT"], "Target missing or constant.",
                        "Fix the dataset contract.")
            return
        proto = mlcore.make_protocol(pack.reference, pack.current, pack.target, cfg["seed"], pack.primary_metric)
        digests = self.ws.materialize(pack, proto)
        self.s["scenario"] = {"scenario_id": pack.scenario_id, "kind": pack.kind, "title": pack.title,
                              "symptom": pack.symptom, "notes": scenarios.INCIDENT_NOTES.get(pack.scenario_id, ""),
                              "primary_metric": pack.primary_metric,
                              "critical_segment": pack.critical_segment,
                              "rows": {"reference": len(pack.reference), "current": len(pack.current)}}
        self.s["protocol"] = proto["manifest"]
        self.s["inputs"] = digests
        self.s["inputs_digest"] = mlcore.canonical_hash(digests)[:24]
        self.emit("protocol_frozen", "Split roles frozen before any fitting",
                  counts=proto["manifest"]["counts"], protocol_hash=proto["manifest"]["protocol_hash"])
        self.move(Status.BASELINING)

    def _baseline(self) -> None:
        t0 = time.perf_counter()
        digests = profiler.train_baseline(self.ws)
        self.s["inputs"].update(digests)
        findings = profiler.profile(self.ws)
        self.s["budget"]["cpu_seconds"] += round(time.perf_counter() - t0, 3)
        self.s["findings"] = [f.model_dump() for f in findings]
        self.emit("findings", f"{len(findings)} machine findings published",
                  ids=[f.finding_id for f in findings])
        if self.s["scenario"]["kind"] == "challenger_review":
            self.move(Status.TESTING_CORRECTION, "reviewing a submitted challenger")
            return
        self.move(Status.INVESTIGATING)

    def _findings(self) -> list[Finding]:
        return [Finding(**f) for f in self.s["findings"]]

    def _hyps(self) -> list[Hypothesis]:
        return [Hypothesis(**h) for h in self.s["hypotheses"]]

    def _save_hyps(self, hyps: list[Hypothesis]) -> None:
        self.s["hypotheses"] = [h.model_dump() for h in hyps]

    def _investigate(self) -> None:
        if not self.s["hypotheses"]:
            gap = research.detect_gap(self._findings())
            self.s["research"]["reason_code"] = gap["reason_code"]
            if gap["required"]:
                self.move(Status.RESEARCHING, f"external evidence gap ({gap['reason_code']})")
                return
            self.s["research"]["state"] = "NOT_NEEDED"
            self.move(Status.PROPOSING)
            return
        self.move(Status.PROPOSING)

    def _research(self) -> None:
        r = self.s["research"]
        if self.s["config"]["research"] == "off":
            r["state"] = "OFF_ABLATION"
            self.emit("research", "Research disabled for this run (Tavily-off ablation arm)")
        elif self.tavily is None:
            r["state"] = "UNAVAILABLE"
            self.emit("research", "EXTERNAL_EVIDENCE_UNAVAILABLE: Tavily is not configured; "
                                  "continuing with local evidence only")
        else:
            pack = self.ws.pack()
            forbidden = set(self.ws.reference().columns) | {
                str(v) for c in pack["pipeline"]["categorical"] for v in self.ws.reference()[c].dropna().unique()}
            prev = ((pack.get("environment") or {}).get("previous") or {}).get("scikit-learn")
            out = research.run_research(self._findings(), forbidden, prev, self.tavily,
                                        self.settings.AUTOPILOT_MAX_EXTRACTS_PER_RUN,
                                        getattr(self.settings, "AUTOPILOT_MAX_SEARCHES_PER_RUN", 2))
            r.update(state=out["state"], query=out["query"], mapping=out["mapping"],
                     evidence=[e.model_dump() for e in out["evidence"]],
                     calls=[c.model_dump() for c in out["calls"]])
            acc = sum(1 for e in out["evidence"] if e.status == "ACCEPTED")
            self.emit("research", f"Tavily research {out['state']}: {len(out['evidence'])} results, "
                                  f"{acc} accepted by the source gate", calls=r["calls"])
        self.move(Status.PROPOSING)

    def _planner(self):
        req = self.s["config"]["planner"]
        if req in ("auto", "nemotron") and self.nemotron is not None:
            return self.nemotron
        if req == "nemotron":
            return None
        return DeterministicPlanner()

    def _propose(self) -> None:
        if self.s["hypotheses"]:
            self._select_next()
            return
        findings = self._findings()
        ctx = context(findings)
        sc = self.s.get("scenario") or {}
        ctx["incident"] = {"title": sc.get("title"), "report": sc.get("symptom"), "team_notes": sc.get("notes")}
        evidence = [ExternalEvidence(**e) for e in self.s["research"]["evidence"]]
        p = self.s["planner"]
        if not has_symptom(findings):
            p["used"] = "not needed"
            p["note"] = "No measured degradation or contract issue, so no model was asked for causes."
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, ["E_NO_SYMPTOM"],
                        "No measurable degradation or contract issue; no competing causes to test.",
                        "No action warranted. Keep monitoring; re-run when labels or alerts change.")
            return
        planner = self._planner()
        if planner is None:
            p["note"] = "Nemotron planner requested but no provider is configured (NEBIUS_API_KEY or NVIDIA_API_KEY)"
            self.decide(Decision.FAILED, Status.FAILED, ["E_PROVIDER_UNAVAILABLE"],
                        "Planner provider unavailable; nothing was executed.", "Configure NEBIUS_API_KEY or NVIDIA_API_KEY.")
            return
        hyps, call = planner.propose(findings, evidence, ctx)
        for c in getattr(planner, "last_calls", None) or ([call] if call is not None else []):
            p["calls"].append(c.model_dump())
        if call is not None:
            p["prompt_hash"] = prompt_hash()
        if planner is self.nemotron and not hyps:
            if self.s["config"]["planner"] == "nemotron":
                self.decide(Decision.FAILED, Status.FAILED, ["E_PROVIDER_UNAVAILABLE"],
                            f"Nemotron call failed or was invalid ({call.error if call else 'n/a'}); "
                            "nothing was executed.", "Retry or use the deterministic planner.")
                return
            p["note"] = f"Nemotron unusable ({call.error if call else 'n/a'}); deterministic planner used instead"
            hyps, _ = DeterministicPlanner().propose(findings, evidence, ctx)
            p["used"] = "deterministic (fallback)"
        else:
            p["used"] = planner.name
            anchored = getattr(planner, "last_anchored", [])
            if anchored:
                p["note"] = (f"Rule anchor added {', '.join(anchored)}: the model omitted a cause the "
                             "findings score highly, so it is tested too.")
        if not hyps:
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, ["E_NO_SYMPTOM"],
                        "No measurable degradation or contract issue; no competing causes to test.",
                        "No action warranted. Keep monitoring; re-run when labels or alerts change.")
            return
        self._save_hyps(hyps)
        self.s["belief_history"].append({"after": "planning", "weights": {h.hypothesis_id: h.belief_weight
                                                                          for h in hyps}})
        self.emit("hypotheses", f"{len(hyps)} competing hypotheses ({p['used']})",
                  causes=[h.cause.value for h in hyps])
        self._select_next()

    def _select_next(self) -> None:
        hyps = self._hyps()
        ctx = context(self._findings())
        if beliefs.supported(hyps):
            self._to_correction(hyps)
            return
        b = self.s["budget"]
        done = {e["signature"] for e in self.s["experiments"]}
        probe = ctx.get("probe_params")

        def sig(tid: str) -> str:
            params = probe if tid == "DEPENDENCY_API_PROBE" and probe else {}
            return policy.signature(self.s["inputs_digest"], self.s["protocol"]["protocol_hash"], tid, params)

        ranking = beliefs.rank(hyps, ctx, done, sig, b["max_cost_units"] - b["cost_units_used"])
        self.s["rankings"].append({"step": len(self.s["experiments"]) + 1,
                                   "candidates": [c.model_dump() for c in ranking]})
        best = self._choose(ranking)
        if best is None or b["experiments_used"] >= b["max_experiments"]:
            live = beliefs.live(hyps)
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE,
                        ["E_BUDGET_EXCEEDED"] if best else ["E_NO_DISCRIMINATING_TEST"],
                        f"{len(live)} hypotheses remain plausible; no approved test separates them "
                        "within budget.", "Collect more evidence (labels, logs) or raise the budget.")
            return
        params = probe if best.template_id == "DEPENDENCY_API_PROBE" else {}
        self.s["pending"] = {"template_id": best.template_id, "parameters": params,
                             "hypothesis_ids": [h.hypothesis_id for h in beliefs.live(hyps)],
                             "source_refs": [], "rationale": f"max information gain per cost "
                                                             f"({best.expected_information_bits} bits / "
                                                             f"{best.estimated_cost_units} units)"}
        self.emit("selection", f"Selected {best.template_id}: {best.expected_information_bits} bits for "
                               f"{best.estimated_cost_units} cost units", ranking_step=len(self.s["rankings"]))
        self.move(Status.POLICY_CHECK)

    def _choose(self, ranking):
        """Selection policy hook. Full Autopilot: highest information gain per cost.
        Benchmark arms override this (fixed order, random eligible)."""
        return next((c for c in ranking if c.eligible), None)

    def _policy(self) -> None:
        verdict = policy.check(self.s["pending"], self._policy_state())
        if not verdict["allow"]:
            self.s["denials"].append({"proposal": self.s["pending"], "code": verdict["code"],
                                      "reason": verdict["reason"], "at": utcnow()})
            self.emit("policy_denied", f"{verdict['code']}: {verdict['reason']}")
            self.s.pop("pending", None)
            self.decide(Decision.INCONCLUSIVE if verdict["code"] != "E_PROPOSAL_INVALID" else Decision.BLOCKED,
                        Status.INCONCLUSIVE if verdict["code"] != "E_PROPOSAL_INVALID" else Status.BLOCKED,
                        [verdict["code"]], f"Policy gate denied the next experiment: {verdict['reason']}",
                        "Review the denial; nothing was executed.")
            return
        exp = verdict["experiment"]
        self._admit(exp)
        self.s.pop("pending", None)
        self.emit("policy_allowed", f"{exp.template_id} admitted ({exp.experiment_id})",
                  experiment_id=exp.experiment_id, signature=exp.signature)
        self.move(Status.EXECUTING)

    def _eval_protocol_digest(self) -> str:
        sc = self.s["scenario"]
        return "sha256:" + mlcore.canonical_hash({"verifier": VERIFIER_VERSION, "metric": sc["primary_metric"],
                                                  "critical_segment": sc["critical_segment"],
                                                  "protocol": self.s["protocol"]["protocol_hash"]})

    def _admit(self, exp) -> None:
        rec = exp.model_dump()
        rec["envelope"] = policy.envelope(self.s, rec, self._eval_protocol_digest())
        self.s["experiments"].append(rec)
        b = self.s["budget"]
        b["experiments_used"] += 1
        b["cost_units_used"] = round(b["cost_units_used"] + exp.estimated_cost_units, 3)

    def _policy_state(self) -> dict[str, Any]:
        cfg = self.ws.pack().get("incumbent_pipeline") or self.ws.pack()["pipeline"]
        return {"hypotheses": self.s["hypotheses"] or [{"hypothesis_id": "REVIEW", "prediction_by_template": {}}],
                "evidence": self.s["research"]["evidence"], "budget": self.s["budget"],
                "experiments": self.s["experiments"], "protocol_hash": self.s["protocol"]["protocol_hash"],
                "inputs_digest": self.s["inputs_digest"], "numeric_features": cfg["numeric"],
                "labels_available": context(self._findings())["labels_available"], "limits": self.s["limits"]}

    def _run(self, exp: dict[str, Any]) -> dict[str, Any]:
        exp["status"] = "RUNNING"
        self.emit("execution_started", f"Running {exp['template_id']}", experiment_id=exp["experiment_id"])
        out = runner.run(exp["template_id"], str(self.ws.root), exp["parameters"], exp["experiment_id"],
                         self.s["limits"]["max_seconds"], self.s["limits"]["runner"])
        exp["actual_seconds"] = out["seconds"]
        exp["runner_mode"] = out["mode"]
        self.s["budget"]["cpu_seconds"] = round(self.s["budget"]["cpu_seconds"] + out["seconds"], 3)
        if out["status"] != "DONE":
            exp["status"], exp["error"] = out["status"], out["error"]
            self.emit("execution_failed", f"{exp['template_id']} {out['status']}: {out['error']}",
                      experiment_id=exp["experiment_id"])
            return out
        exp["status"] = "DONE"
        exp["observation"] = out["result"]
        exp["output_sha256"] = self.ws.write_output(exp["experiment_id"], out["result"])
        return out

    def _execute(self) -> None:
        exp = self.s["experiments"][-1]
        out = self._run(exp)
        if out["status"] != "DONE":
            self.decide(Decision.FAILED, Status.FAILED, ["E_WORKER_CRASH" if out["status"] == "FAILED"
                                                         else "E_TIMEOUT"],
                        "The diagnostic did not complete; no conclusion is drawn.", "Inspect the error and retry.")
            return
        self.emit("observation", f"{exp['template_id']} observed {out['result']['bucket']}",
                  experiment_id=exp["experiment_id"], bucket=out["result"]["bucket"])
        self.move(Status.OBSERVING)

    def _observe(self) -> None:
        exp = self.s["experiments"][-1]
        hyps = self._hyps()
        changes = beliefs.update(hyps, exp["template_id"], exp["observation"]["bucket"])
        self._save_hyps(hyps)
        exp["belief_update"] = changes
        self.s["belief_history"].append({"after": exp["experiment_id"], "template_id": exp["template_id"],
                                         "observed": exp["observation"]["bucket"],
                                         "weights": {h.hypothesis_id: h.belief_weight for h in hyps}})
        sup = beliefs.supported(hyps)
        self.emit("belief_update", f"Belief updated; supported: {sup.cause.value if sup else 'none yet'}",
                  changes=changes)
        if sup:
            self._to_correction(hyps)
        else:
            self.move(Status.PROPOSING)

    def _to_correction(self, hyps: list[Hypothesis]) -> None:
        sup = beliefs.supported(hyps)
        assert sup is not None
        labels = context(self._findings())["labels_available"]
        cause = sup.cause
        if cause == Cause.DATA_CONTRACT_BREAK:
            obs = next((e["observation"]["measurements"] for e in reversed(self.s["experiments"])
                        if e["template_id"] == "CONTRACT_DIAGNOSTIC" and e.get("observation")), {})
            self.decide(Decision.BLOCKED, Status.BLOCKED, ["E_INPUT_CONTRACT"],
                        "Input data violates the contract; retraining or re-thresholding on it would hide the "
                        "defect. Serving decisions on affected rows are unreliable.",
                        "Fix the upstream feed (null surge / unseen categories), then re-run. "
                        + (f"Evidence: {obs.get('null_rate_increase')}" if obs else ""))
            return
        if cause == Cause.FEATURE_SHIFT and not labels:
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, ["E_EVIDENCE_MISSING"],
                        "Covariate drift is real, but without outcome labels quality loss is unmeasured; "
                        "drift alone does not justify retraining.",
                        "Collect labels for the drifted window, then re-run to measure actual degradation.")
            return
        tid = CORRECTIVE_FOR.get(cause)
        if tid is None:
            self.decide(Decision.DIAGNOSIS_SUPPORTED, Status.INCONCLUSIVE, ["E_NO_APPROVED_CORRECTION"],
                        f"Diagnosis supported: {sup.statement} No approved bounded correction exists.",
                        "Escalate to an engineer with this evidence.")
            return
        params: dict[str, Any] = {}
        srcs: list[str] = []
        if tid == "PREPROCESSING_ABLATION":
            fix = next((e["observation"]["measurements"].get("suggested_fix") for e in reversed(self.s["experiments"])
                        if e["template_id"] == "TRAIN_SERVE_PARITY" and e.get("observation")), None)
            if not fix:
                self.decide(Decision.DIAGNOSIS_SUPPORTED, Status.INCONCLUSIVE, ["E_NO_APPROVED_CORRECTION"],
                            "Preprocessing mismatch supported but no unit factor was measured.",
                            "Inspect the upstream transformation.")
                return
            params = fix
        if tid == "DEPENDENCY_COMPAT_FIX":
            m = self.s["research"].get("mapping")
            if not m:
                self.decide(Decision.DIAGNOSIS_SUPPORTED, Status.INCONCLUSIVE, ["E_SOURCE_REQUIRED"],
                            f"Diagnosis supported: {sup.statement} No cited source names the replacement "
                            f"parameter (research state: {self.s['research']['state']}), and policy forbids "
                            "guessing API renames.", "Enable Tavily research or pin the previous library version.")
                return
            params = {"object": m["object"], "from": m["from"], "to": m["to"]}
            srcs = [m["source_ref"]]
        if not labels:
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, ["E_EVIDENCE_MISSING"],
                        f"Diagnosis supported ({cause.value}) but too few labelled rows to verify any correction.",
                        "Wait for more outcomes to mature, then re-run.")
            return
        self.s["pending"] = {"template_id": tid, "parameters": params, "hypothesis_ids": [sup.hypothesis_id],
                             "source_refs": srcs, "rationale": f"bounded correction for supported {cause.value}"}
        self.s["correction"] = {"template_id": tid, "for_hypothesis": sup.hypothesis_id, "cause": cause.value,
                                "mode": "non_inferior" if cause == Cause.LIBRARY_API_CHANGE else "improve"}
        self.emit("correction_proposed", f"Diagnosis supported ({cause.value}); proposing {tid}")
        self.move(Status.TESTING_CORRECTION)

    def _correct(self) -> None:
        if self.s["scenario"]["kind"] == "challenger_review" and self.s.get("correction") is None:
            self.s["pending"] = {"template_id": "CHALLENGER_REVIEW", "parameters": {}, "hypothesis_ids": ["REVIEW"],
                                 "source_refs": [], "rationale": "score submitted challenger on frozen protocol"}
            self.s["correction"] = {"template_id": "CHALLENGER_REVIEW", "for_hypothesis": None, "cause": None,
                                    "mode": "improve"}
        verdict = policy.check(self.s["pending"], self._policy_state())
        self.s.pop("pending", None)
        if not verdict["allow"]:
            self.s["denials"].append({"code": verdict["code"], "reason": verdict["reason"], "at": utcnow()})
            self.emit("policy_denied", f"{verdict['code']}: {verdict['reason']}")
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, [verdict["code"]],
                        f"Correction not admitted: {verdict['reason']}", "Nothing was executed.")
            return
        exp = verdict["experiment"]
        self._admit(exp)
        out = self._run(self.s["experiments"][-1])
        if out["status"] != "DONE":
            self.decide(Decision.FAILED, Status.FAILED, ["E_WORKER_CRASH"],
                        "Correction did not complete; incumbent unchanged.", "Inspect the error.")
            return
        self.emit("candidate_built", f"Candidate built: {out['result']['candidate']['label']}",
                  experiment_id=exp.experiment_id)
        self.move(Status.VERIFYING)

    def _verify(self) -> None:
        exp = self.s["experiments"][-1]
        obs = exp["observation"]
        ch = self.ws.pack().get("challenger") or {}
        env_ok = policy.envelope_intact(exp.get("envelope") or {})
        if not env_ok:
            self.decide(Decision.FAILED, Status.FAILED, ["E_ENVELOPE_MISMATCH"],
                        "The experiment record changed after admission; nothing is reported.",
                        "Re-run the investigation.")
            return
        kwargs = dict(candidate=obs["candidate"], self_report=obs["measurements"].get("self_reported_validation") or {},
                      mode=self.s["correction"]["mode"], expected_protocol_hash=self.s["protocol"]["protocol_hash"],
                      declared_split=ch.get("split", "same") if exp["template_id"] == "CHALLENGER_REVIEW" else "same")
        url = getattr(self.settings, "AUTOPILOT_VERIFIER_URL", "")
        if url:                                   # separate verifier service (container)
            out = runner.verify_remote(url, getattr(self.settings, "AUTOPILOT_VERIFIER_TOKEN", ""),
                                       self.s["run_id"], kwargs, may_open=not self.s["final_test_accesses"],
                                       timeout_s=getattr(self.settings, "AUTOPILOT_VERIFIER_TIMEOUT_S", 180))
        else:                                     # local child process (dev, tests)
            out = runner.run_verifier(str(self.ws.root), kwargs, may_open=not self.s["final_test_accesses"],
                                      timeout_s=self.s["limits"]["max_seconds"], mode=self.s["limits"]["runner"])
        if out["status"] != "DONE":
            self.decide(Decision.FAILED, Status.FAILED, ["E_VERIFIER_UNAVAILABLE"],
                        f"Verifier did not complete ({out.get('error')}); nothing is reported.", "Retry.")
            return
        rec = out["record"]
        if out["opened_for"]:
            self.s["final_test_accesses"].append({"candidate": out["opened_for"], "at": utcnow(),
                                                  "by": "verifier service" if out["mode"] == "service"
                                                  else f"verifier process ({out['mode']})"})
        rec["checks"].insert(0, {"check": "envelope_integrity", "status": "PASS",
                                 "detail": "experiment envelope digest matches the record admitted by policy",
                                 "evidence": {"envelope_digest": exp["envelope"]["envelope_digest"]}})
        rec["process"] = {"mode": out["mode"], "seconds": out.get("seconds"),
                          "isolated": out["mode"] in ("process", "service")}
        rec["experiment_id"] = exp["experiment_id"]
        self.s["verification"] = rec
        self.emit("verified", f"Verifier: {rec['decision']}", reason_codes=rec["reason_codes"])
        d = rec["decision"]
        if d == "CORRECTION_VERIFIED":
            self.decide(Decision.CORRECTION_VERIFIED, Status.REVIEW_READY, [],
                        f"{obs['candidate']['label']} verified on the frozen protocol; eligible for human review.",
                        "A reviewer may approve. Approval records a decision only; it never swaps the live model.")
        elif d == "REJECT":
            self.decide(Decision.REJECT, Status.REJECTED, rec["reason_codes"],
                        f"Do not promote {obs['candidate']['label']}. Incumbent preserved.",
                        "See the failed checks in the receipt.")
        else:
            self.decide(Decision.INCONCLUSIVE, Status.INCONCLUSIVE, rec["reason_codes"],
                        "Evidence insufficient to call the correction verified.",
                        "Collect more labelled data and re-run.")


def new_run_id() -> str:
    return str(uuid.uuid4())
