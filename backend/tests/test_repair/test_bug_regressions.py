"""Focused regressions for the final v5 bug backlog; no provider traffic."""
from pathlib import Path
import json

import httpx
import pandas as pd
import pytest

from app.config import settings
from app.repair import contracts as c
from app.repair.agent.llm import ChatClient, BudgetExceeded
from app.repair.agent.loop import workdir_diff
from app.repair.agent.oracles import make_oracles
from app.repair.discovery import collect_tests
from app.repair.oracle import code, ml, common as cm
from app.repair.sandbox import factory
from app.repair.sandbox.base import RunResult, SandboxError
from app.repair.sandbox.process import ProcessRunner
from app.repair.sandbox.mirror import MirroredEnv


def test_process_factory_and_local_grading_fail_closed_outside_tests(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setattr(settings, "REPAIR_RUNNER", "process")
    monkeypatch.setattr(settings, "REPAIR_VERIFIER_URL", "")
    with pytest.raises(SandboxError, match="restricted to tests"):
        factory.local_runner()
    with pytest.raises(RuntimeError, match="cannot grade"):
        make_oracles(None, None)


def test_candidates_have_independent_allowances_and_one_shared_receipt_ledger():
    def respond(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
    parent = ChatClient(provider="scripted", base_url="https://test.invalid/v1", api_key="test",
                        models={"ultra": "u", "super": "s", "nano": "n"},
                        budget=c.TierTokens(super=240), transport=httpx.MockTransport(respond))
    a, b = parent.scoped("code:a", 120), parent.scoped("code:b", 120)
    a.complete("super", [], max_tokens=64, purpose="code:a")
    with pytest.raises(BudgetExceeded):
        a.complete("super", [], max_tokens=64, purpose="code:a")
    assert b.remaining("super") == 120
    b.complete("super", [], max_tokens=64, purpose="code:b")
    assert parent.used("super") == 240 and len(parent.calls) == 2


def test_documented_nebius_model_mapping_and_unavailable_model_is_not_silently_replaced(monkeypatch):
    from app.repair.agent.llm import client_from_settings
    monkeypatch.setattr(settings, "NEBIUS_API_KEY", "test-not-real")
    attempted = []
    def unavailable(request):
        attempted.append(json.loads(request.content)["model"])
        return httpx.Response(404, json={"error": "model unavailable"})
    client = client_from_settings(c.TierTokens(), transport=httpx.MockTransport(unavailable))
    assert client.models == {"ultra": "nvidia/Nemotron-3-Ultra-550b-a55b",
                             "super": "nvidia/nemotron-3-super-120b-a12b",
                             "nano": "nvidia/Nemotron-3_5-Lightning"}
    with pytest.raises(RuntimeError, match="404"):
        client.complete("super", [], max_tokens=64, purpose="test-availability")
    assert attempted == [client.models["super"]]
    assert not client.calls[0].ok and client.calls[0].status_code == 404


def test_unknown_and_invalid_utf8_changes_are_reported_not_silently_dropped(tmp_path):
    base, work = tmp_path / "base", tmp_path / "work"
    for root in (base, work):
        (root / "pkg").mkdir(parents=True)
    (work / "pkg/query.graphql").write_text("query Example { value }")
    (work / "pkg/invalid.py").write_bytes(b"\xff\xfe")
    diff, denied = workdir_diff(str(base), str(work), ["pkg/"])
    assert not diff and denied == ["pkg/invalid.py", "pkg/query.graphql"]
    from app.repair.agent.loop import RepairAgent, CandidateRun
    agent = object.__new__(RepairAgent)
    events = []; agent._event = lambda *args, **kwargs: events.append((args, kwargs))
    run = CandidateRun(candidate=c.Candidate(candidate_id="c1", task_id="test"), skipped_files=denied)
    agent._screen(run)
    assert run.candidate.status == "REJECTED" and not run.eligible
    assert events[0][1]["files"] == denied


def test_finish_discovery_uses_pytest_configuration_not_test_prefix(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "pytest.ini").write_text("[pytest]\npython_files = *_spec.py\npython_functions = check_*\n")
    (repo / "serving_spec.py").write_text("def check_probability():\n    assert True\n")
    (repo / "test_ignored.py").write_text("def test_fake():\n    assert True\n")
    rc = c.RepoContract(version=1, name="discovery", commands={"test": "pytest -q ."},
                        task={"type": "restore"}, policy={"allowed_edit_roots": ["src/"]})
    runner = ProcessRunner(str(tmp_path / "envs"), allow_unisolated=True)
    env = runner.prepare(str(repo), python="3.12", install="pip install pytest", timeout_s=30)
    try:
        assert collect_tests(runner, env, rc) == {"serving_spec.py::check_probability"}
    finally:
        runner.discard(env)


def test_custom_collected_existing_test_cannot_be_weakened(tmp_path):
    repo = tmp_path / "repo"; (repo / "specs").mkdir(parents=True)
    (repo / "specs/service_spec.py").write_text("def check_score():\n    assert False\n")
    rc = c.RepoContract(version=1, name="custom", commands={"test": "pytest -q specs"},
                        task={"type": "restore", "fail_to_pass": ["specs/service_spec.py::check_score"]},
                        policy={"allowed_edit_roots": ["specs/"]})
    ecc = c.ECC.build(task_id="custom", base_commit_sha="1" * 40, repo_snapshot_digest="sha256:test", contract=rc, target="Restore score")
    diff = "--- a/specs/service_spec.py\n+++ b/specs/service_spec.py\n@@ -1,2 +1,2 @@\n def check_score():\n-    assert False\n+    assert True\n"
    from types import SimpleNamespace
    receipt = code.verify(SimpleNamespace(name="policy-only"), base_dir=str(repo), contract=rc, ecc=ecc, task_id="custom", candidate_id="c1",
                          diff=diff, baseline=code.Baseline({"specs/service_spec.py::check_score": "failed"}, True))
    assert receipt.decision == c.Decision.REJECTED
    assert any(ch.evidence.get("code") == "E_EXISTING_TEST" for ch in receipt.checks)


@pytest.mark.parametrize("scores,expected", [([0.1, 0.9], c.CheckStatus.PASS),
                                               ([0.9, 0.1], c.CheckStatus.FAIL),
                                               ([0.1], c.CheckStatus.FAIL),
                                               ([float("inf"), 0.9], c.CheckStatus.FAIL)])
def test_candidate_cannot_author_its_own_parity_verdict(scores, expected):
    class Runner:
        def fork(self, env): return "probe"
        def write_files(self, env, files):
            assert env == "probe" and files["_ap/predictions.csv"] is None
        def run(self, env, argv, **kwargs):
            assert "--predictions" not in argv
            return RunResult(0, False, 1, "", "", False)
        def read_file(self, env, file):
            if env == "base": return b"id,score\na,0.1\nb,0.9\n"
            # A malicious module may forge old report fields; they have no authority.
            return json.dumps({"scores": scores, "errors": [], "max_abs_diff": 0, "mismatched_rows": 0}).encode()
        def discard(self, env): assert env == "probe"
    rc = c.RepoContract(version=1, name="probe", commands={"test": "pytest", "train": "python train.py", "predict": "python predict.py"},
                        entrypoints={"serve": "pkg.serve:score_request"},
                        data={"train": "train.csv", "holdout_features": "rows.csv", "id_column": "id", "target": "y"},
                        task={"type": "restore", "ml_invariants": ["train_serve_parity"]}, policy={"allowed_edit_roots": ["pkg/"]})
    receipt = ml._parity(Runner(), "base", rc, 10, 1e-9, pd.DataFrame({"id": ["a", "b"], "x": [1, 2]}))
    assert receipt.status == expected


def test_legacy_sealed_pytest_does_not_run_in_production(monkeypatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    result = code._sealed(None, "never", None, None, None)
    assert result.status == c.CheckStatus.INCONCLUSIVE


def test_test_artifact_cache_reuses_only_identical_inputs_and_never_unpickles(tmp_path, monkeypatch):
    monkeypatch.setenv("REPAIR_TEST_TRAIN_CACHE", "1")
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "train.py").write_text("original training source")
    class Runner(ProcessRunner):
        calls = 0
        def run(self, env, argv, **kwargs):
            self.calls += 1
            self.write_files(env, {"_ap/artifacts/model.joblib": b"opaque, deliberately not a pickle"})
            return RunResult(0, False, 1, "trained", "", False)
    runner = Runner(str(tmp_path / "envs"), allow_unisolated=True)
    rc = c.RepoContract(version=1, name="cache", commands={"test": "pytest", "train": "python train.py"},
                        task={"type": "restore"}, policy={"allowed_edit_roots": ["src/"]})
    for _ in range(2):
        with cm.environment(runner, str(repo), rc) as env:
            assert cm.train(runner, env, rc).exit_code == 0
            assert runner.read_file(env, "_ap/artifacts/model.joblib").startswith(b"opaque")
    assert runner.calls == 1
    (repo / "train.py").write_text("changed training source")
    with cm.environment(runner, str(repo), rc) as env:
        cm.train(runner, env, rc)
    assert runner.calls == 2


def test_file_cache_refresh_uses_runner_and_sees_command_changes(tmp_path):
    repo = tmp_path / "repo"; (repo / "pkg").mkdir(parents=True)
    (repo / "pkg/code.py").write_text("value = 1\n")
    runner = ProcessRunner(str(tmp_path / "envs"), allow_unisolated=True)
    env = runner.prepare(str(repo), python="3.12", install="pip install pytest", timeout_s=30)
    mirror = MirroredEnv(runner, env, str(repo))
    try:
        runner.write_files(env, {"pkg/code.py": b"value = 2\n", "pkg/new.py": b"new = True\n"})
        assert mirror.pull() == 2
        assert (mirror.dir / "pkg/code.py").read_text() == "value = 2\n"
        assert (mirror.dir / "pkg/new.py").is_file()
    finally:
        mirror.close(); runner.discard(env)


def test_b14_instructions_cannot_expand_tool_authority(tmp_path):
    from app.repair.tools import ToolExecutor
    from app.repair import snapshots
    fix = Path(__file__).resolve().parents[3] / "fixtures/repos/pricing-model/repo"
    import shutil
    shutil.copytree(fix, tmp_path / "repo")
    rc = c.RepoContract.from_yaml((fix / "autopilot.yaml").read_text())
    ecc = c.ECC.build(task_id="injection-policy", base_commit_sha="1" * 40,
                      repo_snapshot_digest="sha256:test", contract=rc, target="Restore scoring")
    tools = ToolExecutor(tmp_path / "repo", contract=rc, ecc=ecc)
    attempts = [
        {"action": "apply_edit", "path": "tests/conftest.py", "old": "", "new": "collect_ignore = ['test_service.py']\n"},
        {"action": "apply_edit", "path": "tests/test_service.py", "old": "def test_schema", "new": "@pytest.mark.skip\ndef test_schema"},
        {"action": "run", "kind": "shell", "selector": "scripts/bootstrap.sh"},
        {"action": "read_file", "path": ".env"},
    ]
    for act in attempts:
        result = tools.execute({"thought": "Untrusted README requested this", "act": act})
        assert not result.ok and result.denied


def test_planner_context_contains_failing_test_and_imported_local_source(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from app.repair.agent import loop
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True); (root / "pkg").mkdir()
    (root / "tests/test_service.py").write_text("from pkg.logic import score\ndef test_score():\n    assert score() == 1\n")
    (root / "pkg/logic.py").write_text("def score():\n    return 0\n")
    rc = c.RepoContract(version=1, name="context", commands={"test": "pytest -q tests"},
                        task={"type": "restore", "fail_to_pass": ["tests/test_service.py::test_score"]},
                        policy={"allowed_edit_roots": ["pkg/", "tests/"]})
    ecc = c.ECC.build(task_id="context", base_commit_sha="1" * 40, repo_snapshot_digest="sha256:test", contract=rc, target="Restore score")
    class Runner:
        def run(self, *args, **kwargs): return RunResult(1, False, 1, "tests/test_service.py:3: AssertionError", "", False)
    agent = object.__new__(loop.RepairAgent)
    agent.oracles = SimpleNamespace(baseline=lambda: code.Baseline({"tests/test_service.py::test_score": "failed"}, True))
    agent.runner, agent.ecc, agent.contract = Runner(), ecc, rc
    agent.base_dir, agent.roots = str(root), list(ecc.allowed_edit_roots)
    agent.report = SimpleNamespace(failure_excerpt="")
    agent._event = lambda *args, **kwargs: None
    monkeypatch.setattr(loop, "collect_tests", lambda *args: {"tests/test_service.py::test_score"})
    agent._reproduce("base")
    assert "FILE tests/test_service.py" in agent._planner_context
    assert "FILE pkg/logic.py" in agent._planner_context
    assert "return 0" in agent._planner_context
    assert "not instructions" in agent._planner_context


@pytest.mark.parametrize("state", ["REVIEW_READY", "FAILED"])
def test_cli_submits_through_intake_and_handles_terminal_failure_without_fake_sha(tmp_path, monkeypatch, state):
    import importlib.util
    import sys
    script = Path(__file__).resolve().parents[3] / "scripts/run_repair.py"
    spec = importlib.util.spec_from_file_location("repair_cli_test", script)
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def request(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            body = ({"samples": [{"name": "refund-risk", "task": "Restore recall"}]} if path == "samples" else
                    {"id": "abcdef123456"} if path == "repos" else
                    {"id": "task-1", "status": state, "error": "provider unavailable"} if path in {"tasks", "tasks/task-1"} else
                    [] if path.endswith("/events") else {})
            return httpx.Response(200, json=body, request=httpx.Request(method, "https://local.test/" + path))
        def get(self, path):
            return httpx.Response(409 if state == "FAILED" else 200, json={"decision": "VERIFIED"},
                                  request=httpx.Request("GET", "https://local.test/" + path))
    monkeypatch.setattr(cli.httpx, "Client", Client)
    out = tmp_path / "result.json"
    monkeypatch.setattr(sys, "argv", ["run_repair.py", "fixtures/repos/refund-risk", "--out", str(out)])
    assert cli.main() == 0
    submitted = next(kwargs["json"] for method, path, kwargs in calls if path == "tasks")
    assert submitted["repo_id"] == "abcdef123456" and "base_commit_sha" not in submitted
    assert next(kwargs["json"] for method, path, kwargs in calls if path == "repos") == {"sample": "refund-risk"}
    assert json.loads(out.read_text())["decision"] == ("INCONCLUSIVE" if state == "FAILED" else "VERIFIED")
