from pathlib import Path
import shutil
import subprocess

import pytest

from app.repair.intake import IntakeError, intake_repository
from app.repair.tools import platform_diff

YAML = """version: 1
name: service
commands:
  test: pytest -q tests
entrypoints:
  serve: service.serve:score
task:
  type: restore
policy:
  allowed_edit_roots: [service/, tests/]
  immutable: []
"""


def git(root, *args):
    return subprocess.check_output(["git", "-c", "user.name=Tests", "-c", "user.email=tests@example.invalid",
                                    "-c", "core.autocrlf=false", "-c", "core.protectNTFS=false", *args], cwd=root).decode().strip()


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    git(root, "init", "-q")
    (root / "service").mkdir()
    (root / "service/serve.py").write_text("def score(row, model): return 0.5\n")
    (root / "tests").mkdir()
    (root / "tests/test_score.py").write_text("assert True\n")
    (root / "autopilot.yaml").write_text(YAML)
    git(root, "add", ".")
    git(root, "commit", "-qm", "base")
    return root, git(root, "rev-parse", "HEAD")


def test_pinned_sha_digest_map_and_untracked_exclusion(source, tmp_path):
    root, sha = source
    (root / "untracked.txt").write_text("not admitted")
    a = intake_repository(str(root), sha, tmp_path / "a")
    (root / "service/serve.py").write_text("new commit\n")
    git(root, "add", "service/serve.py")
    git(root, "commit", "-qm", "later")
    b = intake_repository(str(root), sha, tmp_path / "b")
    assert a.commit_sha == b.commit_sha == sha
    assert a.digest == b.digest and a.digest.startswith("sha256:")
    assert a.entrypoints == {"serve": "service.serve:score"}
    assert not (a.directory / "untracked.txt").exists()
    assert any(file.path == "tests/test_score.py" and file.is_test for file in a.files)


@pytest.mark.parametrize("sha", ["main", "HEAD", "a" * 39, "z" * 40])
def test_requires_exact_sha(source, tmp_path, sha):
    with pytest.raises(IntakeError, match="exact"):
        intake_repository(str(source[0]), sha, tmp_path / "snapshot")


def test_existing_destination_preserved(source, tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    (destination / "sentinel").write_text("safe")
    with pytest.raises(IntakeError, match="already exists"):
        intake_repository(str(source[0]), source[1], destination)
    assert (destination / "sentinel").read_text() == "safe"


@pytest.mark.parametrize("name", ["sealed/labels.csv", ".env", "src/.ENV.local", "a./file.py"])
def test_sensitive_or_nonportable_tracked_paths_rejected_before_checkout(source, tmp_path, name):
    root, _ = source
    # Inject only index metadata: no environment file is opened or checked out,
    # and Windows need not materialize an otherwise illegal trailing-dot path.
    blob = subprocess.check_output(["git", "hash-object", "-w", "--stdin"],
                                   cwd=root, input=b"synthetic index object").decode().strip()
    git(root, "update-index", "--add", "--cacheinfo", f"100644,{blob},{name}")
    git(root, "commit", "-qm", "unsupported")
    with pytest.raises(IntakeError):
        intake_repository(str(root), git(root, "rev-parse", "HEAD"), tmp_path / "snapshot")


def test_symlink_rejected(source, tmp_path):
    root, _ = source
    try:
        (root / "service/link.py").symlink_to("serve.py")
    except OSError:
        pytest.skip("symlink creation unavailable")
    git(root, "add", "service/link.py")
    git(root, "commit", "-qm", "symlink")
    with pytest.raises(IntakeError):
        intake_repository(str(root), git(root, "rev-parse", "HEAD"), tmp_path / "snapshot")


@pytest.mark.parametrize("source", ["http://example.invalid/x", "ssh://example.invalid/x", "https://user:secret@example.invalid/x", "https://example.invalid/x?token=secret"])
def test_unsafe_remote_source_rejected_without_network(tmp_path, source):
    with pytest.raises(IntakeError, match="credential-free"):
        intake_repository(source, "a" * 40, tmp_path / "snapshot")


def test_missing_entrypoint_cleans_temporary_clone(source, tmp_path):
    root, _ = source
    (root / "autopilot.yaml").write_text(YAML.replace("service.serve:score", "missing.module:score"))
    git(root, "add", "autopilot.yaml")
    git(root, "commit", "-qm", "bad declaration")
    with pytest.raises(IntakeError, match="entrypoint"):
        intake_repository(str(root), git(root, "rev-parse", "HEAD"), tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
    assert not list(tmp_path.glob("repair-intake-*"))


def test_malformed_contract_rejected(source, tmp_path):
    root, _ = source
    (root / "autopilot.yaml").write_text("name: [unterminated")
    git(root, "add", "autopilot.yaml")
    git(root, "commit", "-qm", "invalid contract")
    with pytest.raises(IntakeError, match="valid repository contract"):
        intake_repository(str(root), git(root, "rev-parse", "HEAD"), tmp_path / "snapshot")


def test_git_diff_includes_created_file(source, tmp_path):
    root, sha = source
    snapshot = intake_repository(str(root), sha, tmp_path / "snapshot")
    (snapshot.directory / "service/serve.py").write_text("def score(row, model): return 0.8\n")
    (snapshot.directory / "tests/test_new.py").write_text("assert 1 == 1\n")
    diff = platform_diff(snapshot.directory, sha)
    assert "diff --git a/service/serve.py" in diff
    assert "diff --git a/tests/test_new.py" in diff
    assert "+assert 1 == 1" in diff


def test_diff_uses_external_metadata_without_mutating_snapshot_index(source, tmp_path):
    root, sha = source
    snapshot = intake_repository(str(root), sha, tmp_path / "snapshot")
    work = tmp_path / "candidate"
    shutil.copytree(snapshot.directory, work, ignore=shutil.ignore_patterns(".git"))
    before = (snapshot.directory / ".git/index").read_bytes()
    (work / "tests/test_new.py").write_text("assert True\n")
    diff = platform_diff(work, sha, snapshot_dir=snapshot.directory)
    assert "tests/test_new.py" in diff
    assert not (work / ".git").exists()
    assert (snapshot.directory / ".git/index").read_bytes() == before
