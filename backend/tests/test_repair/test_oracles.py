"""Code and ML oracles on the churn-service fixture (B02).

The headline case: the tempting wrong fix passes every visible test, so a "tests are
green" agent would ship it; the ML oracle rejects it because online scoring disagrees
with batch scoring. The reference fix passes both oracles.

Uses the unisolated process runner (tests only).
"""
import difflib
from pathlib import Path

import pytest

from app.repair import contracts as c
from app.repair.oracle import code as code_oracle
from app.repair.oracle import ml as ml_oracle
from app.repair.sandbox.process import ProcessRunner

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "repos" / "churn-service"
REPO, SEALED = FIX / "repo", FIX / "sealed"
pytestmark = pytest.mark.skipif(not REPO.is_dir(), reason="churn-service fixture not present")


def make_diff(changes: dict[str, str | None]) -> str:
    """Plain unified diff (a/ b/ prefixes) from new file contents; None deletes."""
    out = []
    for rel, new in changes.items():
        old_p = REPO / rel
        old = old_p.read_text(encoding="utf-8").splitlines(keepends=True) if old_p.is_file() else []
        new_l = [] if new is None else new.splitlines(keepends=True)
        a = f"a/{rel}" if old else "/dev/null"
        b = f"b/{rel}" if new is not None else "/dev/null"
        out.extend(difflib.unified_diff(old, new_l, a, b))
    return "".join(out)


@pytest.fixture(scope="module")
def runner(tmp_path_factory):
    return ProcessRunner(str(tmp_path_factory.mktemp("envs")), allow_unisolated=True)


@pytest.fixture(scope="module")
def contract():
    return c.RepoContract.from_yaml((REPO / "autopilot.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def manifest():
    return c.SealedManifest.from_yaml((SEALED / "manifest.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ecc(contract):
    return c.ECC.build(task_id="t-b02", base_commit_sha="0" * 40, repo_snapshot_digest="sha256:fixture",
                       contract=contract, target="Online scoring raises after the feature refactor")


@pytest.fixture(scope="module")
def base_tests(runner, contract):
    return code_oracle.measure_baseline(runner, str(REPO), contract)


@pytest.fixture(scope="module")
def base_preds(runner, contract):
    return ml_oracle.baseline_predictions(runner, str(REPO), contract)


def _code(runner, contract, ecc, base_tests, diff, cid="c1"):
    return code_oracle.verify(runner, base_dir=str(REPO), contract=contract, ecc=ecc, task_id=ecc.task_id,
                              candidate_id=cid, diff=diff, baseline=base_tests, sealed_dir=str(SEALED))


def _ml(runner, contract, ecc, manifest, base_preds, diff, ledger, cid="c1"):
    return ml_oracle.verify(runner, base_dir=str(REPO), contract=contract, ecc=ecc, task_id=ecc.task_id,
                            candidate_id=cid, diff=diff, sealed_dir=str(SEALED), manifest=manifest,
                            ledger=ledger, baseline=base_preds)


def by_name(receipt):
    return {ch.name: ch for ch in receipt.checks}


def test_baseline_reproduces_the_declared_failure(base_tests, base_preds):
    assert base_tests.parsed
    assert base_tests.outcomes["tests/test_serve.py::test_score_request_returns_probability"] == "failed"
    assert base_preds is not None and len(base_preds) > 1000      # batch pipeline works on the base


def test_tempting_wrong_fix_is_green_on_tests_but_rejected_by_the_ml_oracle(
        runner, contract, ecc, manifest, base_tests, base_preds, tmp_path):
    diff = (SEALED / "wrong_fix.patch").read_text(encoding="utf-8")
    code = _code(runner, contract, ecc, base_tests, diff)
    assert code.decision is c.Decision.VERIFIED, [ch.model_dump() for ch in code.checks]
    ml = _ml(runner, contract, ecc, manifest, base_preds, diff, ml_oracle.FileHoldoutLedger(str(tmp_path)))
    parity = by_name(ml)["train_serve_parity"]
    assert ml.decision is c.Decision.REJECTED and parity.status is c.CheckStatus.FAIL
    assert parity.evidence["mismatched_rows"] > 100
    assert not ml.holdout_opened                      # rejected before the labels were touched
    final = c.VerifiedChangeReceipt.combine(code, ml, ml_required=True)
    assert final.decision is c.Decision.REJECTED


def test_reference_fix_passes_both_oracles(runner, contract, ecc, manifest, base_tests, base_preds, tmp_path):
    diff = (SEALED / "reference_fix.patch").read_text(encoding="utf-8")
    code = _code(runner, contract, ecc, base_tests, diff)
    assert code.decision is c.Decision.VERIFIED, [ch.model_dump() for ch in code.checks]
    assert "tests/test_online_batch.py" in str(by_name(code)["pass_to_pass"].evidence["added_tests"])
    ledger = ml_oracle.FileHoldoutLedger(str(tmp_path))
    ml = _ml(runner, contract, ecc, manifest, base_preds, diff, ledger)
    assert ml.decision is c.Decision.VERIFIED, [ch.model_dump() for ch in ml.checks]
    assert ml.holdout_opened and ml.metrics["holdout"]["candidate"] >= 0.75
    final = c.VerifiedChangeReceipt.combine(code, ml, ml_required=True)
    assert final.decision is c.Decision.VERIFIED
    # Single-use holdout: the same finalist may be replayed, a different candidate may not.
    assert _ml(runner, contract, ecc, manifest, base_preds, diff, ledger).decision is c.Decision.VERIFIED
    other = _ml(runner, contract, ecc, manifest, base_preds, diff, ledger, cid="c2")
    assert other.decision is c.Decision.REJECTED and "E_HOLDOUT_REUSE" in other.reasons


def test_a_change_that_fixes_nothing_fails_fail_to_pass(runner, contract, ecc, base_tests):
    src = (REPO / "churn/serve.py").read_text(encoding="utf-8")
    code = _code(runner, contract, ecc, base_tests, make_diff({"churn/serve.py": src + "\n# tidy\n"}))
    assert code.decision is c.Decision.REJECTED
    assert by_name(code)["fail_to_pass"].status is c.CheckStatus.FAIL


@pytest.mark.parametrize("changes,needle", [
    ({"tests/test_serve.py": "def test_score_request_returns_probability():\n    assert True\n"},
     "existing test files"),
    ({"tests/conftest.py": "collect_ignore = ['test_serve.py']\n"}, "immutable"),
    ({"conftest.py": "import pytest\n"}, "immutable"),
    ({"pytest.ini": "[pytest]\naddopts = -k 'not serve'\n"}, "immutable"),
    ({"tests/test_new.py": "import pytest\n\n@pytest.mark.skip\ndef test_x():\n    pass\n"}, "test-skipping"),
    ({"data/train.csv": "customer_id,churned\n"}, "immutable"),
    ({"setup_hack.py": "print('hi')\n"}, "outside allowed edit roots"),
])
def test_gaming_and_out_of_scope_edits_are_rejected_before_anything_runs(
        runner, contract, ecc, base_tests, changes, needle):
    code = _code(runner, contract, ecc, base_tests, make_diff(changes))
    pol = by_name(code)["diff_policy"]
    assert code.decision is c.Decision.REJECTED and pol.status is c.CheckStatus.FAIL
    assert needle in pol.detail
    assert "diff_applies" not in by_name(code)          # nothing was executed


def test_tampered_contract_is_refused(runner, contract, ecc, base_tests):
    widened = ecc.model_copy(update={"allowed_edit_roots": ["", "churn/", "tests/", "data/"]})
    code = _code(runner, contract, widened, base_tests, (SEALED / "reference_fix.patch").read_text("utf-8"))
    assert code.decision is c.Decision.REJECTED and code.reasons == ["E_CONTRACT_TAMPERED"]


def test_diff_reader_handles_git_and_plain_formats():
    from app.repair.oracle.common import parse_diff
    git = ("diff --git a/x.py b/x.py\nnew file mode 100644\n--- /dev/null\n+++ b/x.py\n@@ -0,0 +1 @@\n+a\n"
           "diff --git a/y.py b/y.py\n--- a/y.py\n+++ b/y.py\n@@ -1 +1 @@\n--- old dash line\n+++ new plus line\n")
    ch = parse_diff(git)
    assert [(x.path, x.status) for x in ch] == [("x.py", "added"), ("y.py", "modified")]
    assert ch[1].added_lines == ["++ new plus line"] and ch[1].removed == 1
