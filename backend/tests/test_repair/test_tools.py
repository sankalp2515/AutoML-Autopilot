from pathlib import Path

import pytest

from app.repair.contracts import AgentStep, ApplyEdit, ECC, RepoContract
from app.repair.sandbox.base import RunResult
from app.repair.tools import ToolExecutor


def contract():
    return RepoContract.model_validate({
        "version": 1, "name": "service", "commands": {
            "test": "pytest -q tests", "train": "python -m service.train --out {artifacts}/model.joblib",
            "predict": "python -m service.predict --input {holdout_features} --out {predictions}"},
        "task": {"type": "restore"}, "policy": {
            "allowed_edit_roots": ["src/", "tests/"],
            "immutable": ["src/config.py", "src/private/*"], "immutable_existing_tests": True}})


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, env_id, argv, **kwargs):
        self.calls.append((env_id, argv, kwargs))
        return RunResult(0, False, 10, "passed", "", False)


@pytest.fixture
def executor(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src/main.py").write_text("x = 1\nx = 2\n", encoding="utf-8")
    (tmp_path / "tests/test_existing.py").write_text("assert True\n", encoding="utf-8")
    (tmp_path / "tests/helpers.py").write_text("value = True\n", encoding="utf-8")
    rc = contract()
    ecc = ECC.build(task_id="task", base_commit_sha="a" * 40, repo_snapshot_digest="sha256:x",
                    contract=rc, target="repair service")
    return ToolExecutor(tmp_path, contract=rc, ecc=ecc, runner=Runner(), env_id="env")


def call(executor, action, **kwargs):
    return executor.execute({"act": {"action": action, **kwargs}})


@pytest.mark.parametrize("path", [
    "../outside.py", "src/../../outside.py", "/tmp/outside.py", "C:/outside.py",
    "src\\main.py", "src/main.py:stream", "src/main.py.", "src/main.py ",
    "src/NUL.py", "src/CON", "src/COM1.txt", "src/.env", "src/.ENV.local",
    "src/sealed/test.py", "src/_ap/grader.py", "src/.git/config",
    "src/MAIN~1.PY", "src/name?.py", "src/name*.py",
])
def test_path_escapes_are_denied(executor, path):
    result = call(executor, "apply_edit", path=path, old="", new="pass")
    assert not result.ok and result.denied


@pytest.mark.parametrize("path", [
    "tests/test_existing.py", "TESTS/test_existing.py", "tests/TEST_EXISTING.PY",
    "tests/helpers.py", "src/conftest.py", "tests/Conftest.PY", "src/pyproject.toml",
    "src/PyProject.TOML", "src/pytest.ini", "src/setup.cfg", "src/tox.ini",
    "src/config.py", "src/CONFIG.PY", "src/private/key.py", "outside.py",
    ".github/workflows/test.yml", "autopilot.yaml",
])
def test_protected_paths_are_denied(executor, path):
    result = call(executor, "apply_edit", path=path, old="", new="pass")
    assert not result.ok and result.denied


def test_old_must_match_exactly_once(executor):
    for old in ("missing", "x = "):
        result = call(executor, "apply_edit", path="src/main.py", old=old, new="changed")
        assert not result.ok and "E_MATCH_COUNT" in result.denied
    assert call(executor, "apply_edit", path="src/main.py", old="x = 1", new="x = 3").ok
    assert (executor.root / "src/main.py").read_text() == "x = 3\nx = 2\n"


def test_create_cannot_overwrite_even_empty_existing_file(executor):
    existing = executor.root / "src/empty.py"
    existing.touch()
    result = call(executor, "apply_edit", path="src/empty.py", old="", new="pass")
    assert not result.ok and "E_CREATE_EXISTS" in result.denied
    assert not existing.read_bytes()


def test_new_regression_test_allowed(executor):
    assert call(executor, "apply_edit", path="tests/test_new.py", old="", new="assert True\n").ok
    assert call(executor, "apply_edit", path="tests/test_new.py", old="True", new="1 == 1").ok


@pytest.mark.parametrize("payload", [b"\x00text", b"\xff\xfe"])
def test_binary_edits_denied(executor, payload):
    (executor.root / "src/blob.py").write_bytes(payload)
    assert not call(executor, "apply_edit", path="src/blob.py", old="text", new="other").ok
    assert not call(executor, "apply_edit", path="src/new.png", old="", new="ascii").ok


def test_symlinks_cannot_read_write_or_search_outside(executor, tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "value.py").write_text("secret-marker")
    link = executor.root / "src/link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation unavailable")
    assert not call(executor, "read_file", path="src/link/value.py").ok
    assert not call(executor, "apply_edit", path="src/link/value.py", old="secret-marker", new="changed").ok
    result = call(executor, "search", pattern="secret-marker")
    assert "secret-marker" not in result.output
    assert (outside / "value.py").read_text() == "secret-marker"


def test_hardlink_cannot_mutate_existing_test(executor):
    original = executor.root / "tests/test_existing.py"
    alias = executor.root / "src/alias.py"
    try:
        alias.hardlink_to(original)
    except OSError:
        pytest.skip("hardlink creation unavailable")
    result = call(executor, "apply_edit", path="src/alias.py", old="True", new="False")
    assert not result.ok and "E_HARDLINK" in result.denied
    assert original.read_text() == "assert True\n"


def test_junction_ancestor_is_denied(executor, monkeypatch):
    link = executor.root / "src/alias"
    link.mkdir()
    (link / "value.py").write_text("value = 1\n")
    # Exercise the junction guard on every OS, without requiring Windows shell
    # privileges to create an actual NTFS reparse point.
    monkeypatch.setattr(type(link), "is_junction", lambda path: path == link, raising=False)
    assert not call(executor, "read_file", path="src/alias/value.py").ok
    assert not call(executor, "apply_edit", path="src/alias/value.py", old="1", new="2").ok
    assert (link / "value.py").read_text() == "value = 1\n"


def test_file_listing_and_line_ranges(executor):
    (executor.root / "src/many.py").write_text("".join(f"line-{i}\n" for i in range(1, 401)))
    listing = call(executor, "list_files", path="src")
    assert listing.ok and "src/main.py" in listing.output
    result = call(executor, "read_file", path="src/many.py", start_line=10, end_line=399)
    assert len(result.output.splitlines()) == 200 and result.truncated
    assert result.output.startswith("10: line-10\n")
    assert result.output.endswith("209: line-209")


def test_literal_search_capped_at_50(executor):
    (executor.root / "src/many.py").write_text("a.*b\n" * 60)
    result = call(executor, "search", pattern="a.*b")
    assert result.ok and result.truncated and len(result.output.splitlines()) == 50
    assert not call(executor, "search", pattern="a.+b").output


def test_run_only_contract_argv_and_placeholders(executor):
    result = call(executor, "run", kind="train")
    assert result.ok and result.duration_ms == 10
    env_id, argv, kwargs = executor.runner.calls[-1]
    assert env_id == "env" and argv == ["python", "-m", "service.train", "--out", "_ap/artifacts/model.joblib"]
    assert kwargs == {"timeout_s": 120}
    assert not call(executor, "run", kind="shell").ok
    assert not call(executor, "run", kind="train", selector="tests/test_existing.py").ok


@pytest.mark.parametrize("selector", ["../tests/a.py", "/tmp/test_x.py", "tests/a.py;echo", "--override-ini=x.py"])
def test_selector_escapes_denied(executor, selector):
    assert not call(executor, "run", kind="test", selector=selector).ok
    assert not executor.runner.calls


def test_safe_selector_and_no_host_fallback(executor):
    assert call(executor, "run", kind="test", selector="tests/test_existing.py::test_a").ok
    assert executor.runner.calls[-1][1] == ["pytest", "-q", "tests", "tests/test_existing.py::test_a"]
    executor.runner = None
    assert "E_NO_SANDBOX" in call(executor, "run", kind="test").denied


def test_policy_rechecks_paths_even_for_model_construct(executor):
    step = AgentStep.model_construct(act=ApplyEdit.model_construct(
        action="apply_edit", path="src/../escape.py", old="", new="pass"))
    assert not executor.execute(step).ok


def test_mutated_ecc_denied(executor):
    executor.ecc.allowed_edit_roots.append("/")
    assert "E_CONTRACT" in call(executor, "list_files").denied


def test_mutated_repo_contract_cannot_change_execution(executor):
    executor.contract.commands.test = "python other.py"
    assert "E_CONTRACT" in call(executor, "run", kind="test").denied
    assert not executor.runner.calls
