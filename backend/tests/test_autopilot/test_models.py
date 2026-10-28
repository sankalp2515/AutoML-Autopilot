"""Nemotron usage: incident context, corrective retry, stakeholder summary grounding, cost receipts."""
import json

import httpx
import pytest

from app.autopilot import narrator as narr
from app.autopilot.planner import NemotronPlanner
from app.autopilot.pricing import est_cost_usd

GOOD = {"hypotheses": [
    {"cause": "PREPROCESSING_MISMATCH", "statement": "Income arrives in thousands upstream.",
     "supporting_findings": ["F03", "F05"], "belief_weight": 0.6},
    {"cause": "FEATURE_SHIFT", "statement": "Applicant mix genuinely changed.",
     "supporting_findings": ["F05"], "belief_weight": 0.3}]}
BAD_REF = {"hypotheses": [dict(GOOD["hypotheses"][0], source_refs=["S9"]), GOOD["hypotheses"][1]]}


def _chat(replies, seen):
    """Fake OpenAI-compatible endpoint returning `replies` in order."""
    it = iter(replies)

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        status, payload = next(it)
        if status != 200:
            return httpx.Response(status, json={})
        return httpx.Response(200, json={"id": f"chatcmpl-{len(seen)}", "model": "nvidia/Nemotron-3-Ultra-550b-a55b",
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 500},
                                         "choices": [{"message": {"content": json.dumps(payload)}}]})
    return httpx.MockTransport(handler)


def _planner(replies, seen):
    p = NemotronPlanner("k", "https://tf.example/v1", "nvidia/Nemotron-3-Ultra-550b-a55b",
                        transport=_chat(replies, seen), provider="nebius_token_factory")
    p.RETRY_PAUSE_S = 0
    return p


def test_planner_sees_measured_values_and_team_notes(run_investigation):
    seen = []
    run_investigation("S02", planner="nemotron", nemotron=_planner([(200, GOOD)], seen))
    user = json.loads(seen[0]["messages"][1]["content"])
    assert "standardised monetary fields" in user["incident"]["team_notes"]
    assert any(f["kind"] == "feature_shift" and f["value"] for f in user["findings"])


def test_invalid_citation_gets_one_corrective_retry(run_investigation):
    seen = []
    st, _ = run_investigation("S02", planner="nemotron", nemotron=_planner([(200, BAD_REF), (200, GOOD)], seen))
    calls = st["planner"]["calls"]
    assert [c["ok"] for c in calls] == [False, True] and "unknown source_ref ['S9']" in calls[0]["error"]
    assert "Your reply was rejected" in seen[1]["messages"][-1]["content"]      # the model is told why
    assert st["planner"]["used"] == "nemotron" and st["decision"]["decision"] == "CORRECTION_VERIFIED"


def test_two_invalid_replies_fall_back_honestly(run_investigation):
    seen = []
    st, _ = run_investigation("S02", planner="auto", nemotron=_planner([(200, BAD_REF), (200, BAD_REF)], seen))
    assert len(st["planner"]["calls"]) == 2 and st["planner"]["used"] == "deterministic (fallback)"


def test_transient_503_is_retried_once(run_investigation):
    seen = []
    st, _ = run_investigation("S02", planner="nemotron", nemotron=_planner([(503, None), (200, GOOD)], seen))
    assert [c["status_code"] for c in st["planner"]["calls"]] == [503, 200]


def test_cost_estimate_uses_nebius_list_prices():
    assert est_cost_usd("nvidia/Nemotron-3-Ultra-550b-a55b", 1_000_000, 1_000_000) == 4.0
    assert est_cost_usd("nvidia/Nemotron-3_5-Lightning", 1000, 500) == pytest.approx(0.00018)
    assert est_cost_usd("unknown-model", 10, 10) is None


def test_summary_with_an_invented_number_is_rejected():
    blob = json.dumps({"holdout_evaluation": {"current_model_score": 0.4833, "change_from_fix": 0.0864}})
    assert narr.grounded("Score moved from 0.4833 by +0.0864 after the fix.", blob)
    assert not narr.grounded("The fix saves $40,000 a month.", blob)


def test_narrator_end_to_end_on_a_finished_run(run_investigation):
    st, _ = run_investigation("S02")
    def reply(text):
        def h(req):
            return httpx.Response(200, json={"id": "c1", "model": "nvidia/Nemotron-3_5-Lightning",
                                             "usage": {"prompt_tokens": 200, "completion_tokens": 40},
                                             "choices": [{"message": {"content": text}}]})
        return narr.Narrator("k", "https://tf.example/v1", "nvidia/Nemotron-3_5-Lightning",
                             "nebius_token_factory", transport=httpx.MockTransport(h))
    ok, call = reply("A unit mismatch was confirmed and a fix passed review checks.").summarize(st)
    assert ok["status"] == "OK" and call.est_cost_usd and call.provider == "nebius_token_factory"
    bad, _ = reply("The fix lifts F1 by 0.25.").summarize(st)
    assert bad["status"] == "REJECTED_UNGROUNDED" and bad["text"] is None


def test_a_healthy_model_is_never_sent_to_the_model_for_causes(run_investigation):
    """Live ablation: asked about a healthy model, Nemotron still named causes (S01 0/5)."""
    seen = []
    st, _ = run_investigation("S01", planner="nemotron", nemotron=_planner([(200, GOOD)], seen))
    assert seen == [] and st["planner"]["calls"] == []
    assert st["decision"]["decision"] == "INCONCLUSIVE" and "E_NO_SYMPTOM" in st["decision"]["reason_codes"]


def test_compat_fix_changes_only_the_renamed_parameter(run_investigation):
    """Live ablation: re-tuning the threshold inside the fix made it worse on 2 of 5 seeds."""
    import sys
    sys.path.insert(0, "tests/test_autopilot")
    from test_research import OFFICIAL, fake_tavily
    for seed in (101, 102):
        st, _ = run_investigation("T01", seed=seed, tavily=fake_tavily([OFFICIAL]))
        fix = next(e for e in st["experiments"] if e["template_id"] == "DEPENDENCY_COMPAT_FIX")
        assert fix["observation"]["candidate"]["threshold"] == 0.3            # the serving threshold
        assert st["decision"]["decision"] == "CORRECTION_VERIFIED"
