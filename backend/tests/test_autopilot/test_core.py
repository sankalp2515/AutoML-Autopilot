"""Autopilot core: scenarios, protocol, selection, policy, runner, verifier, engine."""
import json

import pytest

from app.autopilot import beliefs, mlcore, policy, runner, scenarios
from app.autopilot.schemas import Cause, Hypothesis, TERMINAL, Status


# ── scenario catalog / oracle isolation ──────────────────────────────────────
def test_catalog_never_exposes_the_fault():
    text = json.dumps(scenarios.catalog())
    for sid in ("S02", "S03", "T01"):
        assert scenarios.oracle(sid)["fault"] not in text


def test_pack_has_no_oracle_fields():
    pack = scenarios.build("S02", 7)
    assert not any(k in vars(pack) for k in ("fault", "oracle", "expected"))


def test_protocol_is_deterministic_and_disjoint():
    p = scenarios.build("S03", 7)
    a = mlcore.make_protocol(p.reference, p.current, p.target, 7, "f1")
    b = mlcore.make_protocol(p.reference, p.current, p.target, 7, "f1")
    assert a["manifest"]["protocol_hash"] == b["manifest"]["protocol_hash"]
    sel, fin = set(a["roles"]["selection_validation"]), set(a["roles"]["final_test"])
    assert sel and fin and not sel & fin
    assert set(a["roles"]["train"]).isdisjoint(a["roles"]["ref_validation"])


# ── information gain / beliefs ───────────────────────────────────────────────
def _h(hid, cause, w, preds):
    return Hypothesis(hypothesis_id=hid, cause=cause, statement="x" * 12, belief_weight=w,
                      prediction_by_template=preds)


def test_information_gain_prefers_the_test_that_separates_all_causes():
    hyps = [_h("H1", Cause.FEATURE_SHIFT, 1 / 3, {"A": "x", "B": "p"}),
            _h("H2", Cause.PREPROCESSING_MISMATCH, 1 / 3, {"A": "y", "B": "p"}),
            _h("H3", Cause.THRESHOLD_MISCALIBRATION, 1 / 3, {"A": "z", "B": "q"})]
    ig_a, _ = beliefs.information_gain(hyps, "A")
    ig_b, _ = beliefs.information_gain(hyps, "B")
    assert ig_a == pytest.approx(1.585, abs=1e-3)       # log2(3)
    assert 0 < ig_b < ig_a


def test_a_hypothesis_without_a_prediction_is_not_counted_as_an_outcome():
    # H3 cannot say what test A will show, so A only separates H1 from H2.
    hyps = [_h("H1", Cause.FEATURE_SHIFT, 1 / 3, {"A": "x", "B": "p"}),
            _h("H2", Cause.PREPROCESSING_MISMATCH, 1 / 3, {"A": "y", "B": "q"}),
            _h("H3", Cause.THRESHOLD_MISCALIBRATION, 1 / 3, {"B": "r"})]
    ig_a, _ = beliefs.information_gain(hyps, "A")
    ig_b, _ = beliefs.information_gain(hyps, "B")
    assert ig_a < ig_b == pytest.approx(1.585, abs=1e-3)


def test_correctives_are_never_ranked_eligible_before_a_cause_is_supported():
    hyps = [_h("H1", Cause.FEATURE_SHIFT, .5, {}), _h("H2", Cause.THRESHOLD_MISCALIBRATION, .5, {})]
    ranking = beliefs.rank(hyps, {"labels_available": True, "current_rows": 4000}, set(), lambda t: t, 99)
    assert all(not c.eligible for c in ranking if c.kind == "corrective")


def test_high_prior_alone_is_not_support():
    hyps = [_h("H1", Cause.FEATURE_SHIFT, .95, {"A": "x"}), _h("H2", Cause.THRESHOLD_MISCALIBRATION, .05, {"A": "x"})]
    beliefs.update(hyps, "B", "anything")         # no prediction for B → no evidence
    assert beliefs.supported(hyps) is None


def test_inconsistent_hypothesis_is_downweighted_and_consistent_one_supported():
    hyps = [_h("H1", Cause.FEATURE_SHIFT, .5, {"A": "x"}), _h("H2", Cause.PREPROCESSING_MISMATCH, .5, {"A": "y"})]
    changes = beliefs.update(hyps, "A", "y")
    assert {c["hypothesis_id"]: c["verdict"] for c in changes} == {"H1": "inconsistent", "H2": "consistent"}
    assert beliefs.supported(hyps).hypothesis_id == "H2"


# ── policy gate ──────────────────────────────────────────────────────────────
def _pstate(**over):
    st = {"hypotheses": [{"hypothesis_id": "H1", "prediction_by_template": {}}], "evidence": [],
          "budget": {"max_experiments": 4, "max_cost_units": 24, "experiments_used": 0, "cost_units_used": 0},
          "experiments": [], "protocol_hash": "p", "inputs_digest": "i",
          "numeric_features": ["income"], "labels_available": True, "limits": {}}
    st.update(over)
    return st


def test_policy_blocks_unknown_template_without_execution():
    v = policy.check({"template_id": "RUN_SHELL", "hypothesis_ids": ["H1"]}, _pstate())
    assert not v["allow"] and v["code"] == "E_PROPOSAL_INVALID" and v["experiment"] is None


def test_policy_rejects_extra_fields_and_unknown_hypothesis():
    assert policy.check({"template_id": "CONTRACT_DIAGNOSTIC", "hypothesis_ids": ["H1"], "cmd": "ls"},
                        _pstate())["code"] == "E_PROPOSAL_INVALID"
    assert policy.check({"template_id": "CONTRACT_DIAGNOSTIC", "hypothesis_ids": ["H9"]},
                        _pstate())["code"] == "E_PROPOSAL_INVALID"


@pytest.mark.parametrize("params", [{"rescale": {"income": 3}}, {"rescale": {"age": 1000}},
                                    {"rescale": {"income": 1e9}}, {"rescale": "x"}])
def test_policy_bounds_rescale_parameters(params):
    v = policy.check({"template_id": "PREPROCESSING_ABLATION", "hypothesis_ids": ["H1"], "parameters": params},
                     _pstate())
    assert not v["allow"]


def test_policy_budget_and_duplicate():
    full = _pstate(budget={"max_experiments": 1, "max_cost_units": 24, "experiments_used": 1, "cost_units_used": 1})
    assert policy.check({"template_id": "CONTRACT_DIAGNOSTIC", "hypothesis_ids": ["H1"]}, full)["code"] == \
        "E_BUDGET_EXCEEDED"
    first = policy.check({"template_id": "CONTRACT_DIAGNOSTIC", "hypothesis_ids": ["H1"]}, _pstate())
    exp = first["experiment"].model_dump() | {"status": "DONE"}
    again = policy.check({"template_id": "CONTRACT_DIAGNOSTIC", "hypothesis_ids": ["H1"]}, _pstate(experiments=[exp]))
    assert again["code"] == "E_DUPLICATE"


def test_policy_requires_accepted_source_for_api_rename():
    p = {"template_id": "DEPENDENCY_COMPAT_FIX", "hypothesis_ids": ["H1"],
         "parameters": {"object": "OneHotEncoder", "from": "sparse", "to": "sparse_output"}}
    assert policy.check(p, _pstate())["code"] == "E_SOURCE_REQUIRED"
    rejected = [{"source_ref": "S1", "status": "REJECTED_UNTRUSTED"}]
    assert policy.check(p | {"source_refs": ["S1"]}, _pstate(evidence=rejected))["code"] == "E_PROPOSAL_INVALID"


# ── runner ───────────────────────────────────────────────────────────────────
def test_process_runner_kills_on_timeout(tmp_path):
    out = runner.run("CONTRACT_DIAGNOSTIC", str(tmp_path), {}, "E1", timeout_s=0.05, mode="process")
    assert out["status"] in ("TIMEOUT", "FAILED") and "result" not in out


def test_runner_reports_template_errors(tmp_path):
    out = runner.run("CONTRACT_DIAGNOSTIC", str(tmp_path / "missing"), {}, "E1", 30, mode="inline")
    assert out["status"] == "FAILED" and out["error"]


# ── end-to-end against the hidden oracle ─────────────────────────────────────
@pytest.mark.parametrize("sid", ["S01", "S02", "S03", "S04", "S05", "S07", "S08", "S09", "S10", "S12", "T01"])
def test_scenario_reaches_an_oracle_approved_decision(run_investigation, sid):
    st, _ = run_investigation(sid)
    assert Status(st["status"]) in TERMINAL
    assert st["decision"]["decision"] in scenarios.oracle(sid)["expected"], st["decision"]
    assert st["decision"]["incumbent_changed"] is False
    assert len(st["final_test_accesses"]) <= 1


def test_primary_demo_picks_cheap_parity_test_before_any_retrain(run_investigation):
    st, _ = run_investigation("S02")
    first = st["rankings"][0]["candidates"]
    chosen = next(c for c in first if c["eligible"])
    retrain = next(c for c in first if c["template_id"] == "MODEL_FAMILY_CHALLENGER")
    assert chosen["template_id"] == "TRAIN_SERVE_PARITY" and not retrain["eligible"]
    assert st["experiments"][0]["observation"]["bucket"] == "SCALE_MISMATCH"
    assert st["verification"]["decision"] == "CORRECTION_VERIFIED"


def test_segment_regression_is_rejected_despite_aggregate_gain(run_investigation):
    st, _ = run_investigation("S10")
    checks = {c["check"]: c["status"] for c in st["verification"]["checks"]}
    assert checks["segment_guardrail"] == "FAIL" and st["decision"]["decision"] == "REJECT"
    assert st["verification"]["metrics"]["delta"]["delta"] > 0      # headline F1 did go up


def test_leaky_challenger_rejected_before_holdout_is_spent(run_investigation):
    st, _ = run_investigation("S08")
    assert "E_LEAKAGE" in st["decision"]["reason_codes"] and st["final_test_accesses"] == []


def test_worker_failure_never_reports_success(run_investigation, monkeypatch):
    monkeypatch.setattr(runner, "run", lambda *a, **k: {"status": "FAILED", "error": "E_WORKER_CRASH: boom",
                                                        "seconds": 0.1, "mode": "inline"})
    st, _ = run_investigation("S02")
    assert st["status"] == "FAILED" and st["decision"]["decision"] == "FAILED"
    assert st["verification"] is None


def test_cancel_stops_without_side_effects(run_investigation):
    st, inv = run_investigation("S02", steps=2)
    inv._cancel = lambda: True
    inv.step()
    assert st["status"] == "CANCELLED" and st["decision"]["incumbent_changed"] is False
    assert st["experiments"] == []


def test_budget_exhaustion_is_inconclusive(run_investigation):
    st, _ = run_investigation("S02", max_experiments=1)
    assert st["decision"]["decision"] == "INCONCLUSIVE"
    assert "E_BUDGET_EXCEEDED" in st["decision"]["reason_codes"]


def test_illegal_transition_raises(run_investigation):
    st, inv = run_investigation("S01", steps=1)
    with pytest.raises(RuntimeError):
        inv.move(Status.REVIEW_READY)


# ── v4.1: envelope + verifier process isolation ──────────────────────────────
def test_every_experiment_carries_an_intact_envelope(run_investigation):
    from app.autopilot import policy
    st, _ = run_investigation("S02")
    envs = [e["envelope"] for e in st["experiments"]]
    assert envs and all(policy.envelope_intact(e) for e in envs)
    e = envs[0]
    assert e["split_protocol_digest"] == st["protocol"]["protocol_hash"]
    assert e["dataset_manifest_digest"] == st["inputs_digest"]
    assert st["verification"]["checks"][0]["check"] == "envelope_integrity"


def test_a_changed_parameter_breaks_the_envelope_digest(run_investigation):
    st, _ = run_investigation("S02")
    from app.autopilot import policy
    exp = st["experiments"][-1]
    exp["parameters"] = {"rescale": {"income": 1.0}}
    assert not policy.envelope_intact({**exp["envelope"], "typed_parameters": exp["parameters"]})


def test_experiment_processes_cannot_read_the_final_test(tmp_path):
    from app.autopilot import workspace
    prev = workspace.lock_final_test()
    try:
        with pytest.raises(PermissionError):
            workspace.Workspace(tmp_path).rows("final_test")
    finally:
        workspace.lock_final_test(prev)


def test_verifier_runs_in_its_own_process_and_records_the_single_holdout_open(run_investigation, ap_settings):
    ap_settings.AUTOPILOT_RUNNER = "process"
    st, _ = run_investigation("S03")
    v = st["verification"]
    assert v["process"]["isolated"] is True and st["decision"]["decision"] == "CORRECTION_VERIFIED"
    assert len(st["final_test_accesses"]) == 1 and "verifier process" in st["final_test_accesses"][0]["by"]
