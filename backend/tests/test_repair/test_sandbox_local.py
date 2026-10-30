"""Local runner boundary tests: a real Docker daemon is used when available."""
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from app.repair.sandbox.base import SandboxError, SandboxRunner, Upload
from app.repair.sandbox.local import HEAD_BYTES, TAIL_BYTES, LocalContainerRunner, _Capture
pytestmark=pytest.mark.docker


def test_output_capture_retains_only_head_and_tail():
    import io
    data = b"a" * HEAD_BYTES + b"discard" * 100_000 + b"z" * TAIL_BYTES
    capture = _Capture()
    capture.drain(io.BytesIO(data))
    assert capture.truncated
    assert capture.text() == "a" * HEAD_BYTES + "z" * TAIL_BYTES
    assert len(capture.head) + len(capture.tail) == HEAD_BYTES + TAIL_BYTES


def test_small_output_is_not_duplicated():
    import io
    capture = _Capture()
    capture.drain(io.BytesIO(b"a" * 9000))
    assert not capture.truncated
    assert capture.text() == "a" * 9000


def test_root_has_only_one_service_owner(tmp_path):
    from app.repair.sandbox.server import claim_root
    runner=LocalContainerRunner(tmp_path/"owned")
    first=claim_root(runner)
    try:
        with pytest.raises(SandboxError,match="another sandbox process"):
            claim_root(runner)
    finally:first.close()
    claim_root(runner).close()


def test_restart_reaps_only_its_namespace(tmp_path,monkeypatch):
    from app.repair.sandbox.base import RunResult
    runner=LocalContainerRunner(tmp_path/"owned");calls=[]
    def command(argv,**kwargs):
        calls.append(argv)
        return RunResult(exit_code=0,stdout="a"*64 if argv[0]=="ps" else "",stderr="",timed_out=False,duration_ms=0,truncated=False)
    monkeypatch.setattr(runner,"_command",command)
    assert runner.recover_orphan_containers()==1
    assert calls[0]==["ps","-aq","--filter",f"label=autopilot.runner.namespace={runner.namespace}"]
    assert calls[1]==["rm","--force","a"*64]


def test_pinned_prepare_never_installs_and_capacity_is_enforced(environment,monkeypatch):
    from app.config import settings
    runner,env_id=environment
    image=runner.environment_image(env_id)
    child=runner.prepare_pinned(str(runner.work_dir(env_id)),image=image,timeout_s=15)
    assert runner.environment_image(child)==image
    assert runner.read_file(child,"state.txt")==b"parent"
    monkeypatch.setattr(settings,"REPAIR_SANDBOX_MAX_ENVIRONMENTS",2)
    with pytest.raises(SandboxError,match="capacity"):
        runner.fork(env_id)


def test_expired_environment_cleanup_is_scoped(environment,tmp_path):
    import time
    runner,env_id=environment
    marker=runner.root/env_id/".environment.json"
    os.utime(marker,(1,1))
    unrelated=runner.root/"keep-me";unrelated.mkdir()
    counts=runner.cleanup(3600,now=time.time())
    assert counts["environments"]==1
    assert unrelated.is_dir()
    with pytest.raises(SandboxError,match="unknown"):
        runner.work_dir(env_id)


@pytest.mark.parametrize("listing_exit,remaining,raises", [(0, b"container-id", True),
                                                         (1, b"", True), (0, b"", False)])
def test_timeout_removal_must_be_confirmed(tmp_path, monkeypatch, listing_exit, remaining, raises):
    runner = LocalContainerRunner(tmp_path / "attempts")

    class Client:
        def __init__(self):
            self.stdout, self.stderr = io.BytesIO(), io.BytesIO()
            self.returncode = None
            self.killed = False

        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("docker run", timeout)
            return self.returncode

        def kill(self):
            self.killed = True
            self.returncode = -9

    process = Client()
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: process)

    def docker_result(argv, **kwargs):
        if argv[1] == "rm":
            return subprocess.CompletedProcess(argv, 1)
        assert argv[1:3] == ["container", "ls"]
        return subprocess.CompletedProcess(argv, listing_exit, remaining, b"")

    monkeypatch.setattr(subprocess, "run", docker_result)
    if raises:
        with pytest.raises(SandboxError, match="confirm timeout cleanup"):
            runner._command(["run"], timeout_s=1, kill_container="test-container")
    else:
        result = runner._command(["run"], timeout_s=1, kill_container="test-container")
        assert result.timed_out and result.exit_code == -1
    assert process.killed


@pytest.mark.skipif(os.name == "nt", reason="Windows does not preserve POSIX executable mode bits")
def test_snapshot_preserves_executable_bits(tmp_path):
    from app.repair.sandbox.local import _copy_tree, _snapshot_digest
    source, destination = tmp_path / "source", tmp_path / "destination"
    source.mkdir()
    executable = source / "entrypoint"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    _copy_tree(source, destination)
    assert (destination / "entrypoint").stat().st_mode & 0o111 == 0o111
    digest = _snapshot_digest(destination)
    (destination / "entrypoint").chmod(0o666)
    assert _snapshot_digest(destination) != digest


def test_protocol_and_missing_environment(tmp_path):
    runner = LocalContainerRunner(tmp_path / "attempts")
    assert isinstance(runner, SandboxRunner)
    assert runner.isolated
    with pytest.raises(SandboxError, match="unknown"):
        runner.run("../../host", ["python"], timeout_s=1)
    runner.discard("unknown")


@pytest.mark.parametrize("bad", ["pip install x; echo x", "pip install x\nRUN echo x", "curl x",
                                  "pip install x && echo x"])
def test_install_is_not_a_shell_program(tmp_path, bad):
    runner = LocalContainerRunner(tmp_path / "attempts")
    with pytest.raises(SandboxError, match="install"):
        runner.prepare(str(tmp_path), python="3.12", install=bad, timeout_s=5)


def test_snapshot_refuses_secret_paths_without_reading_them(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    # A name alone is enough; deliberately do not open any existing secret file.
    (repo / ".env.sentinel").mkdir()
    runner = LocalContainerRunner(tmp_path / "attempts")
    monkeypatch.setattr(runner, "_command", lambda *a, **k: pytest.fail("must fail before Docker"))
    with pytest.raises(SandboxError, match="secret"):
        runner.prepare(str(repo), python="3.12", install="pip install -r requirements.txt", timeout_s=5)
    assert not list((tmp_path / "attempts").iterdir())


@pytest.fixture(scope="module")
def docker_base():
    if shutil.which("docker") is None:
        pytest.skip("Docker CLI is unavailable")
    try:
        info = subprocess.run(["docker", "info", "--format", "{{.OSType}}"],
                              capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("Docker daemon is unavailable")
    if info.returncode or info.stdout.strip() != "linux":
        pytest.skip("Linux Docker daemon is unavailable")
    # Production defaults to python:<version>-slim. Reuse an existing trusted
    # Python image for offline local validation, without contacting a registry.
    for image in ("python:3.12-slim", "automl-autopilot-backend:latest"):
        found = subprocess.run(["docker", "image", "inspect", image, "--format", "{{.Id}}"],
                               capture_output=True, timeout=15)
        if found.returncode == 0:
            return image
    # A fresh Linux CI host may obtain the official image during prepare(),
    # the only phase where the runner permits network access.
    return "python:3.12-slim"


@pytest.fixture
def environment(tmp_path, docker_base):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("", encoding="utf-8")
    (repo / "state.txt").write_text("parent", encoding="utf-8")
    runner = LocalContainerRunner(tmp_path / "attempts", base_image=docker_base)
    env_id = runner.prepare(str(repo), python="3.12", install="pip install --no-index -r requirements.txt",
                            timeout_s=180)
    yield runner, env_id
    for identifier in list(runner._environments):
        runner.discard(identifier)


def test_network_blocked_and_nonroot_readonly(environment):
    runner, env_id = environment
    result = runner.run(env_id, ["python", "-c", """
import os, socket
assert os.getuid() == 10001
try:
    socket.create_connection(('192.0.2.1', 80), timeout=1)
except OSError:
    pass
else:
    raise AssertionError('network allowed')
try:
    open('/forbidden-root-write', 'w').write('bad')
except OSError:
    pass
else:
    raise AssertionError('root filesystem writable')
open('/tmp/scratch', 'w').write('ok')
print('isolated')
"""], timeout_s=15)
    assert result.exit_code == 0, result.stderr
    assert result.stdout.strip() == "isolated"


def test_host_and_sibling_files_are_invisible(environment, tmp_path):
    runner, parent = environment
    sibling = runner.fork(parent)
    runner.write_files(sibling, {"sibling-secret.txt": b"private"})
    host = tmp_path / "host-only.txt"
    host.write_text("private", encoding="utf-8")
    result = runner.run(parent, ["python", "-c", """
import pathlib, sys
for path in sys.argv[1:]:
    assert not pathlib.Path(path).exists(), path
assert not pathlib.Path('/work/sibling-secret.txt').exists()
assert not pathlib.Path('/build').exists()
""", str(host), str(runner._environment(sibling).root / "work/sibling-secret.txt")], timeout_s=10)
    assert result.exit_code == 0, result.stderr


def test_timeout_kills_container(environment, monkeypatch):
    runner, env_id = environment
    original = runner._command
    names = []

    def record(argv, **kwargs):
        names.append(kwargs["kill_container"])
        return original(argv, **kwargs)

    monkeypatch.setattr(runner, "_command", record)
    result = runner.run(env_id, ["python", "-c", "import time; time.sleep(60)"], timeout_s=1)
    assert result.exit_code == -1 and result.timed_out
    running = subprocess.run(["docker", "ps", "--filter", f"name=^/{names[0]}$", "--format", "{{.Names}}"],
                             capture_output=True, text=True, timeout=15)
    assert not running.stdout.strip()


def test_fork_is_independent_and_artifacts_are_hashed(environment):
    runner, parent = environment
    child = runner.fork(parent)
    assert runner.work_dir(parent) != runner.work_dir(child)
    result = runner.run(child, ["python", "-c", """
from pathlib import Path
Path('state.txt').write_bytes(b'child')
Path('/out/result.bin').write_bytes(b'output')
Path('_ap').mkdir(exist_ok=True)
Path('_ap/predictions.csv').write_bytes(b'predictions')
"""], timeout_s=10, collect=["result.bin", "_ap/predictions.csv"])
    assert result.exit_code == 0, result.stderr
    assert result.artifacts == {"result.bin": hashlib.sha256(b"output").hexdigest(),
                                "_ap/predictions.csv": hashlib.sha256(b"predictions").hexdigest()}
    assert runner.read_file(parent, "state.txt") == b"parent"
    assert runner.read_file(child, "state.txt") == b"child"
    assert runner.read_file(child, "result.bin") == b"output"


def test_output_is_bounded_and_environment_is_explicit(environment, monkeypatch):
    runner, env_id = environment
    monkeypatch.setenv("AP_PROVIDER_SENTINEL", "must-not-reach-container")
    result = runner.run(env_id, ["python", "-c", """
import os, sys
assert 'AP_PROVIDER_SENTINEL' not in os.environ
assert os.environ['TASK_VALUE'] == 'visible'
sys.stdout.write('H'*4096 + 'x'*1000000 + 'T'*12288)
sys.stderr.write('E'*200000)
"""], timeout_s=15, env={"TASK_VALUE": "visible"})
    assert result.exit_code == 0, result.stderr
    assert result.truncated
    assert result.stdout == "H" * HEAD_BYTES + "T" * TAIL_BYTES
    assert len(result.stderr) == HEAD_BYTES + TAIL_BYTES


def test_uploads_and_nonzero_exit(environment):
    runner, env_id = environment
    result = runner.run(env_id, ["python", "-c", "from pathlib import Path; print(Path('input.txt').read_text()); raise SystemExit(3)"],
                        uploads=[Upload("input.txt", b"uploaded")], timeout_s=10)
    assert result.exit_code == 3 and not result.timed_out
    assert result.stdout.strip() == "uploaded"
    reserved = runner.run(env_id, ["python", "-c", "raise SystemExit(125)"], timeout_s=10)
    assert reserved.exit_code == 125 and not reserved.timed_out


def test_ambiguous_artifacts_fail_closed(environment):
    runner, env_id = environment
    result = runner.run(env_id, ["python", "-c", "from pathlib import Path; Path('same.txt').write_text('work'); Path('/out/same.txt').write_text('out')"],
                        timeout_s=10)
    assert result.exit_code == 0, result.stderr
    with pytest.raises(SandboxError, match="ambiguous artifact"):
        runner.read_file(env_id, "same.txt")
    with pytest.raises(SandboxError, match="ambiguous artifact"):
        runner.run(env_id, ["python", "-c", "pass"], timeout_s=10, collect=["same.txt"])


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "file:stream",
                                  "sub/../../outside", "sub\\outside", "state.txt."])
def test_paths_cannot_escape(environment, path):
    runner, env_id = environment
    with pytest.raises(SandboxError):
        runner.write_files(env_id, {path: b"bad"})
    with pytest.raises(SandboxError):
        runner.read_file(env_id, path)


def test_symlink_artifacts_and_reads_are_refused(environment):
    runner, env_id = environment
    result = runner.run(env_id, ["python", "-c", "import os; os.symlink('/etc/passwd', 'escape')"], timeout_s=10)
    assert result.exit_code == 0, result.stderr
    with pytest.raises(SandboxError, match="symlink"):
        runner.read_file(env_id, "escape")
    with pytest.raises(SandboxError, match="symlink"):
        runner.run(env_id, ["python", "-c", "pass"], timeout_s=10, collect=["escape"])
    with pytest.raises(SandboxError, match="symlink"):
        runner.fork(env_id)


def test_prepare_cache_is_content_bound(environment, tmp_path):
    runner, first = environment
    repo = tmp_path / "another-repo"
    repo.mkdir()
    (repo / "requirements.txt").write_text("", encoding="utf-8")
    (repo / "state.txt").write_text("parent", encoding="utf-8")
    second = runner.prepare(str(repo), python="3.12", install="pip install --no-index -r requirements.txt", timeout_s=180)
    assert runner._environment(first).image == runner._environment(second).image
    assert runner._environment(first).root != runner._environment(second).root
    (repo / "state.txt").write_text("new revision", encoding="utf-8")
    third = runner.prepare(str(repo), python="3.12", install="pip install --no-index -r requirements.txt", timeout_s=180)
    assert runner._environment(third).image != runner._environment(first).image


def test_discard_recovers_uid_owned_directories(environment, monkeypatch):
    runner, env_id = environment
    root = runner._environment(env_id).root
    original = runner._remove
    calls = []

    def permission_once(path):
        calls.append(path)
        if len(calls) == 1:
            raise PermissionError("simulated Linux UID ownership")
        original(path)

    monkeypatch.setattr(runner, "_remove", permission_once)
    runner.discard(env_id)
    assert not root.exists()
    assert env_id not in runner._environments
    assert len(calls) == 2


def test_trusted_probe_has_same_image_but_no_candidate_workspace(environment):
    runner,parent=environment
    runner.write_files(parent,{"evil.py":b"raise RuntimeError('candidate import')\n",
                              "conftest.py":b"raise RuntimeError('candidate pytest plugin')\n"})
    script=b"from pathlib import Path\nassert not Path('state.txt').exists()\nassert not Path('evil.py').exists()\nassert not Path('conftest.py').exists()\nPath('_ap/result.json').write_text('{\"passed\":true}')\n"
    result,files=runner.trusted_run(parent,["python","-I","-S","_ap/probe.py"],uploads=[Upload("_ap/probe.py",script)],
        timeout_s=30,collect=["_ap/result.json"])
    assert result.exit_code==0,result.stderr
    assert files["_ap/result.json"]==b'{"passed":true}'
    with pytest.raises(FileNotFoundError):runner.read_file(parent,"_ap/probe.py")
    assert runner.read_file(parent,"state.txt")==b"parent"


def test_external_artifact_tests_ignore_candidate_verdict_and_plugins(tmp_path,docker_base):
    import json
    from app.repair import contracts as c
    from app.repair.oracle.external import sealed_tests
    repo=tmp_path/"repo";repo.mkdir()
    runner=LocalContainerRunner(tmp_path/"envs",base_image=docker_base)
    env=runner.prepare(str(repo),python="3.12",install="pip install pytest==8.3.4",timeout_s=180)
    sealed=tmp_path/"sealed";(sealed/"tests").mkdir(parents=True)
    (sealed/"external-probes.json").write_text(json.dumps({"version":1,"kind":"artifact-tests","exports":{"observations.json":"observations.json"}}))
    (sealed/"tests/test_external.py").write_text("import os,json\nfrom pathlib import Path\ndef test_value():\n assert not Path('/work/conftest.py').exists()\n assert not Path('/work/never-upload.csv').exists()\n d=json.loads((Path(os.environ['AP_INPUT_DIR'])/'observations.json').read_text())\n assert d['value']==1\n")
    manifest=c.SealedManifest(version=1,labels="never-upload.csv",tests="tests")
    contract=c.RepoContract.model_validate({"version":1,"name":"external-proof","install":"pip install pytest==8.3.4",
        "commands":{"test":"pytest -q tests"},"task":{"type":"restore"},"policy":{"allowed_edit_roots":["src/"]}})
    try:
        runner.write_files(env,{"conftest.py":b"import os;os._exit(0)\n","observations.json":b'{"value":0,"verdict":"PASS"}'})
        rejected=sealed_tests(runner,env,contract,sealed,manifest)
        assert rejected.status==c.CheckStatus.FAIL and rejected.evidence["external_container"]
        runner.write_files(env,{"observations.json":b'{"value":1,"verdict":"FAIL"}'})
        accepted=sealed_tests(runner,env,contract,sealed,manifest)
        assert accepted.status==c.CheckStatus.PASS and not accepted.evidence["candidate_source_present"]
    finally:runner.discard(env)
