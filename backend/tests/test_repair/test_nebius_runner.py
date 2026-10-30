import base64
import json
import uuid

import httpx
import pytest

from app.repair.sandbox.base import SandboxError, Upload
from app.repair.sandbox.pinned import immutable_image, pinned
from app.repair.sandbox.tokenfactory import TokenFactoryRunner


class ControlPlane:
    def __init__(self):
        self.base = str(uuid.uuid4())
        self.images = {self.base: {}}
        self.operations = {}
        self.requests = []

    def __call__(self, request):
        path = request.url.path.removeprefix("/sandboxes/v1")
        if path == "/images":
            return httpx.Response(200, json={"images": [{"uuid": self.base, "tag": "python:3.12-slim"}]})
        if path == "/instances":
            payload = json.loads(request.content)
            self.requests.append(payload)
            assert request.headers["authorization"] == "Bearer test-private-key"
            assert request.headers["project"] == "test-project"
            assert b"test-private-key" not in request.content
            body = json.loads(payload["stdin"]["value"])
            image, operation = str(uuid.uuid4()), str(uuid.uuid4())
            files = dict(self.images[payload["image"]])
            for name, value in body.get("files", {}).items():
                if value is None:
                    files.pop(name, None)
                else:
                    files[name] = base64.b64decode(value)
            self.images[image] = files
            result = {"ok": True, "files": {k: len(v) for k, v in files.items()}}
            if body["action"] == "run":
                result.pop("ok")
                result.update(exit_code=0, timed_out=False, duration_ms=1,
                              stdout="executed", stderr="", truncated=False, artifacts={})
            self.operations[operation] = {"status": "SUCCESS", "result_image_uuid": image,
                "metadata": {"result": {"state": {"exit_code": 0},
                    "stdout": {"value": json.dumps(result), "encoding": "ascii"}}}}
            return httpx.Response(201, json={"uuid": operation})
        if path.startswith("/operations/"):
            return httpx.Response(200, json=self.operations[path.split("/")[2]])
        if path.startswith("/inspect/"):
            image = path.split("/")[2]
            content = self.images[image].get(request.url.params["path"].removeprefix("/work/"))
            return httpx.Response(404) if content is None else httpx.Response(200, content=content)
        raise AssertionError(path)


@pytest.fixture
def prepared(tmp_path):
    source = tmp_path / "repo"
    source.mkdir()
    (source / "requirements.txt").write_text("pytest==8.3.3\n")
    (source / "main.py").write_text("original")
    plane = ControlPlane()
    runner = TokenFactoryRunner(tmp_path / "state", api_key="test-private-key", project_id="test-project",
                                transport=httpx.MockTransport(plane), poll_s=0)
    ident = runner.prepare(str(source), python="3.12", install="pip install -r requirements.txt", timeout_s=30)
    return runner, ident, source, plane


def test_network_and_credential_boundaries_and_exact_pins(prepared):
    runner, ident, _, plane = prepared
    runner.run(ident, ["python", "-c", "print(1)"], timeout_s=10)
    assert plane.requests[0]["networking"] == {"enabled": True}
    prep = json.loads(plane.requests[0]["stdin"]["value"])
    assert prep["pins"] == ["pytest==8.3.3"]
    assert "files" not in prep
    for request in plane.requests[1:]:
        assert request["networking"] == {"enabled": False}
        assert request["shell"] is False
        assert request["preserve_env"] is False
        assert request["resources_limits"]["max_layer_bytes"] == 2 * 1024 ** 3
        assert set(request["env"]) == {"PATH", "HOME"}


def test_fork_is_independent_and_pinned_image_is_reused(prepared):
    runner, parent, source, plane = prepared
    image = runner.environment_image(parent)
    child = runner.fork(parent)
    runner.write_files(child, {"main.py": b"changed"})
    assert runner.read_file(parent, "main.py") == b"original"
    assert runner.read_file(child, "main.py") == b"changed"
    assert immutable_image(image)
    reused = runner.prepare_pinned(str(source), image=image, timeout_s=30)
    assert runner.environment_image(reused) == image
    assert sum(p["networking"]["enabled"] for p in plane.requests) == 1
    runner.prepare(str(source), python="3.12", install="pip install -r requirements.txt", timeout_s=30)
    assert sum(p["networking"]["enabled"] for p in plane.requests) == 1


def test_trusted_evaluation_has_empty_workspace_and_cannot_mutate_candidate(prepared):
    runner, parent, _, plane = prepared
    runner.write_files(parent, {"candidate-only.py": b"not trusted"})
    runner.trusted_run(parent, ["python", "_ap/trusted.py"], timeout_s=10,
                       uploads=[Upload("_ap/trusted.py", b"print(1)")])
    operation = plane.requests[-1]
    assert operation["image"] == runner.environment_image(parent).removeprefix("nebius:")
    assert "candidate-only.py" not in plane.images[operation["image"]]
    assert runner.read_file(parent, "candidate-only.py") == b"not trusted"
    with pytest.raises(FileNotFoundError):
        runner.read_file(parent, "_ap/trusted.py")


@pytest.mark.parametrize("path", ["../outside", "/etc/passwd", "C:/host", "a\\b", ".git/config", ".env"])
def test_path_escapes_denied_before_provider_call(prepared, path):
    runner, ident, _, plane = prepared
    before = len(plane.requests)
    with pytest.raises((SandboxError, ValueError)):
        runner.write_files(ident, {path: b"denied"})
    assert len(plane.requests) == before


def test_platform_scratch_is_allowed_but_source_inventory_excludes_it(prepared):
    runner, ident, _, _ = prepared
    runner.write_files(ident, {"_ap/result.json": b"{}"})
    assert runner.read_file(ident, "_ap/result.json") == b"{}"
    assert "_ap/result.json" not in runner.inventory(ident)
    with pytest.raises(SandboxError, match="read limit"):
        runner.read_file(ident, "main.py", max_bytes=1)


def test_unknown_or_mutable_dependency_identity_refused(prepared):
    runner, _, source, _ = prepared
    with pytest.raises(SandboxError):
        runner.prepare_pinned(str(source), image="tag:python:3.12", timeout_s=30)
    with pytest.raises(SandboxError):
        runner.prepare_pinned(str(source), image="nebius:" + str(uuid.uuid4()), timeout_s=30)
    assert not immutable_image("sha256:wrong")
    assert not immutable_image("nebius:../escape")


def test_remote_pinned_prepare_propagates_concrete_runner_name(tmp_path):
    from app.repair.sandbox.remote import RemoteRunner
    image = "nebius:" + str(uuid.uuid4())
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "env_id": "a" * 32, "image": image, "runner": "tokenfactory-sandbox"}))
    remote = RemoteRunner("http://sandbox.test", "test-token", transport=transport)
    remote.prepare_pinned(str(tmp_path), image=image, timeout_s=30)
    assert remote.name == "tokenfactory-sandbox"
    assert remote.environment_image("a" * 32) == image


@pytest.mark.parametrize("tier", ["super", "nano"])
def test_nebius_disables_hidden_reasoning_for_bounded_tool_calls(monkeypatch, tier):
    from app.config import settings
    from app.repair.agent.llm import client_from_settings
    from app.repair.contracts import TierTokens
    monkeypatch.setattr(settings, "NEBIUS_API_KEY", "test-private-key")
    monkeypatch.setattr(settings, "NEBIUS_FAST_MODEL", "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B")
    def response(request):
        body = json.loads(request.content)
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        return httpx.Response(200, json={"id": "test", "model": body["model"],
            "usage": {"prompt_tokens": 22, "completion_tokens": 3},
            "choices": [{"finish_reason": "stop", "message": {"content": "READY"}}]})
    client = client_from_settings(TierTokens(), transport=httpx.MockTransport(response))
    assert client.complete(tier, [{"role": "user", "content": "READY"}], max_tokens=16,
                           purpose="bounded probe", json_mode=False) == "READY"
