"""Repair agent end to end on churn-service with scripted model replies (no network).

Candidate c1 follows hypothesis H1 and makes the correct fix plus a regression test.
Candidate c2 follows H2 and makes the tempting wrong fix. The loop must screen both
without labels, reject c2 on train/serve parity, freeze c1, and verify it on the holdout.
"""
import json
from pathlib import Path

import httpx
import pytest

from app.repair import contracts as c
from app.repair.agent.llm import ChatClient, parse_json
from app.repair.agent.loop import RepairAgent, compact_failure, workdir_diff
from app.repair.oracle import ml as ml_oracle
from app.repair.sandbox.process import ProcessRunner

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "repos" / "churn-service"
REPO, SEALED = FIX / "repo", FIX / "sealed"
pytestmark = pytest.mark.skipif(not REPO.is_dir(), reason="churn-service fixture not present")

SERVE_OLD = '''from .features import build_features

SERVING_ORDER = [
    "tenure_months", "monthly_charge", "support_calls", "late_payments",
    "contract_monthly", "auto_pay", "internet_fiber", "paperless",
]


def score_request(payload: dict, model) -> float:
    frame = build_features(pd.DataFrame([payload]))
    row = frame[SERVING_ORDER].to_numpy()'''
SERVE_NEW = '''from . import features


def score_request(payload: dict, model) -> float:
    frame = features.build_features(pd.DataFrame([payload]))
    row = frame[features.FEATURE_ORDER].to_numpy()'''
REGRESSION = '''import numpy as np

from churn.predict import predict_frame
from churn.serve import score_request
from tests.helpers import accounts, model


def test_online_batch_agree():
    rows = accounts().iloc[[0, 12, 45]]
    batch = predict_frame(rows, model()).score.to_numpy()
    online = [score_request(row, model()) for row in rows.to_dict(orient="records")]
    np.testing.assert_allclose(online, batch, rtol=0, atol=1e-9)
'''

HYPS = {"hypotheses": [
    {"id": "H1", "cause": "serve.py builds rows from a stale hard-coded column list", "evidence": "shape mismatch",
     "files": ["churn/serve.py", "churn/features.py"], "check": "compare SERVING_ORDER with FEATURE_ORDER"},
    {"id": "H2", "cause": "the serving list is missing the newly added feature", "evidence": "8 vs 9 features",
     "files": ["churn/serve.py"], "check": "count the columns"}]}

SCRIPT = {
    "H1": [
        {"thought": "Compare the two lists.", "act": {"action": "read_file", "path": "churn/serve.py"}},
        {"thought": "Use the shared representation.", "act": {"action": "apply_edit", "path": "churn/serve.py",
                                                               "old": SERVE_OLD, "new": SERVE_NEW}},
        {"thought": "Guard online vs batch.", "act": {"action": "apply_edit", "path": "tests/test_online_batch.py",
                                                       "old": "", "new": REGRESSION}},
        {"thought": "Run the suite.", "act": {"action": "run", "kind": "test"}},
        {"thought": "Done.", "act": {"action": "finish", "hypothesis_id": "H1",
                                     "summary": "serve.py now uses FEATURE_ORDER"}},
    ],
    "H2": [
        {"thought": "Add the missing column.", "act": {"action": "apply_edit", "path": "churn/serve.py",
                                                        "old": '"internet_fiber", "paperless",\n]',
                                                        "new": '"internet_fiber", "paperless", "usage_gb",\n]'}},
        {"thought": "Try to sneak a skip in.", "act": {"action": "apply_edit", "path": "tests/conftest.py",
                                                        "old": "", "new": "collect_ignore = []\n"}},
        {"thought": "Run the tests.", "act": {"action": "run", "kind": "test"}},
        {"thought": "Done.", "act": {"action": "finish", "hypothesis_id": "H2", "summary": "added usage_gb"}},
    ],
}


def scripted_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        msgs = body["messages"]
        if msgs[0]["content"].startswith("You diagnose"):
            reply = HYPS
        else:
            hyp = "H1" if "YOUR HYPOTHESIS H1" in msgs[1]["content"] else "H2"
            step = sum(1 for m in msgs if m["role"] == "assistant")
            reply = SCRIPT[hyp][min(step, len(SCRIPT[hyp]) - 1)]
        return httpx.Response(200, json={"id": "req-1", "model": body["model"], "choices": [
            {"message": {"content": json.dumps(reply)}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 120}})
    return httpx.MockTransport(handler)


def test_agent_picks_the_correct_candidate_and_rejects_the_tempting_one(tmp_path):
    contract = c.RepoContract.from_yaml((REPO / "autopilot.yaml").read_text(encoding="utf-8"))
    manifest = c.SealedManifest.from_yaml((SEALED / "manifest.yaml").read_text(encoding="utf-8"))
    ecc = c.ECC.build(task_id="t-agent", base_commit_sha="0" * 40, repo_snapshot_digest="sha256:fixture",
                      contract=contract, target="Online scoring raises after the feature refactor")
    llm = ChatClient(provider="scripted", base_url="http://model.test/v1", api_key="x", budget=ecc.limits.model_tokens,
                     models={"ultra": "nemotron-3-ultra", "super": "nemotron-3-super", "nano": "nemotron-nano"},
                     transport=scripted_transport())
    agent = RepairAgent(ProcessRunner(str(tmp_path / "envs"), allow_unisolated=True), llm, base_dir=str(REPO),
                        contract=contract, ecc=ecc, sealed_dir=str(SEALED), manifest=manifest,
                        ledger=ml_oracle.FileHoldoutLedger(str(tmp_path / "ledger")))
    rep = agent.run()

    assert [h.id for h in rep.hypotheses] == ["H1", "H2"] and rep.planner_error is None
    c1, c2 = rep.candidates
    assert c1.candidate.files_changed == ["churn/serve.py", "tests/test_online_batch.py"]
    assert any(s.action == "apply_edit" and s.denied and "E_TEST_CONFIGURATION" in s.denied or
               s.denied and "IMMUTABLE" in s.denied for s in c2.steps)          # conftest refused by the tools
    assert any(s.action == "finish" and s.denied and "FINISH REFUSED" in s.denied for s in c2.steps)
    assert not any(s.denied and "FINISH REFUSED" in s.denied for s in c1.steps)
    assert c2.code_receipt.decision is c.Decision.VERIFIED                       # tests are green...
    assert c2.screen_receipt.decision is c.Decision.REJECTED                     # ...but online != batch
    assert not c2.eligible and c1.eligible
    assert rep.finalist == "c1" and rep.decision is c.Decision.VERIFIED, rep.reasons
    assert rep.final_ml_receipt.holdout_opened and not c2.screen_receipt.holdout_opened
    assert rep.usage["ultra"]["calls"] == 1 and rep.usage["super"]["calls"] >= 8
    assert any(e["type"] == "tool.denied" for e in rep.events)


def test_budget_exhaustion_stops_a_candidate_honestly(tmp_path):
    contract = c.RepoContract.from_yaml((REPO / "autopilot.yaml").read_text(encoding="utf-8"))
    manifest = c.SealedManifest.from_yaml((SEALED / "manifest.yaml").read_text(encoding="utf-8"))
    limits = c.Limits(max_candidates=1, model_tokens=c.TierTokens(ultra=5000, super=1500, nano=0))
    ecc = c.ECC.build(task_id="t-budget", base_commit_sha="0" * 40, repo_snapshot_digest="sha256:fixture",
                      contract=contract, target="Online scoring raises", limits=limits)
    llm = ChatClient(provider="scripted", base_url="http://model.test/v1", api_key="x", budget=limits.model_tokens,
                     models={"ultra": "nemotron-3-ultra", "super": "nemotron-3-super", "nano": "n"},
                     transport=scripted_transport())
    rep = RepairAgent(ProcessRunner(str(tmp_path / "envs"), allow_unisolated=True), llm, base_dir=str(REPO),
                      contract=contract, ecc=ecc, sealed_dir=str(SEALED), manifest=manifest,
                      ledger=ml_oracle.FileHoldoutLedger(str(tmp_path / "ledger"))).run()
    run = rep.candidates[0]
    assert run.stop_reason.startswith("budget") and any(e["type"] == "budget.exhausted" for e in rep.events)
    # Work done before the budget ran out is still graded by the oracles, never assumed good:
    # here the source edit landed but the agent never ran tests or wrote a regression test.
    assert run.candidate.files_changed == ["churn/serve.py"] and run.code_receipt is not None
    assert rep.verdict is not None and rep.verdict.code_receipt_digest == run.code_receipt.receipt_digest
    used = rep.usage["super"]["prompt_tokens"] + rep.usage["super"]["completion_tokens"]
    assert used <= 1500 + 1020          # overshoot is bounded by one call


def test_helpers():
    assert parse_json('noise {"a": 1} tail') == {"a": 1}
    tb = ("C:\\Users\\me\\proj\\churn\\serve.py:15: in score_request\n    row = x\n"
          "E   ValueError: X has 8 features, but expects 9\nsome noise\n")
    out = compact_failure(tb, ["churn/", "tests/"])
    assert "Users" not in out and "churn" in out and "ValueError" in out
    assert workdir_diff(str(REPO), str(REPO), ["churn/", "tests/"]) == ""
