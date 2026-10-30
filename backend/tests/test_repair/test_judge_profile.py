"""Operator judge policy survives restart without provisioning or provider calls."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest


@pytest.fixture
def stack_module(monkeypatch):
    root=Path(__file__).resolve().parents[3]
    spec=importlib.util.spec_from_file_location("judge_stack_under_test",root/"scripts/v5_stack.py")
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def command(argv,**kwargs):
        if argv[1:3]==["volume","inspect"]:return json.dumps([{"Mountpoint":"/tmp/judge-profile-test"}]).encode()
        return b""
    monkeypatch.setattr(module,"command",command)
    for key in ("NEBIUS_API_KEY","NVIDIA_API_KEY","NVIDIA_API_KEY_2","NVIDIA_API_KEY_3","NVIDIA_API_KEY_4","TAVILY_API_KEY","REPAIR_INVITE_CODES","REPAIR_TOTAL_USD_CAP","REPAIR_MAX_ACTIVE_TASKS"):
        monkeypatch.delenv(key,raising=False)
    return module


def args(tmp_path):
    return SimpleNamespace(project="judge-profile-test",state_dir=str(tmp_path),command="up",judge_only=True,allow_live=False,port=3133)


def test_judge_policy_persists_and_keeps_legacy_worker_keyless(stack_module,tmp_path):
    first=args(tmp_path);stack=stack_module.Stack(first)
    assert stack.env["REPAIR_JUDGE_ONLY"]=="true" and stack.env["REPAIR_TOTAL_USD_CAP"]=="20"
    first.judge_only=False
    restarted=stack_module.Stack(first)
    assert restarted.env["REPAIR_JUDGE_ONLY"]=="true"
    assert "judge_only" not in restarted.env
    override=json.loads((tmp_path/"runner-volume.json").read_text())
    assert not any(override["services"]["worker"]["environment"].values())
    assert not any(restarted.env[k] for k in ("NEBIUS_API_KEY","NVIDIA_API_KEY","TAVILY_API_KEY"))


def test_live_judge_profile_requires_token_factory_and_invitation(stack_module,tmp_path,monkeypatch):
    request=args(tmp_path);request.allow_live=True
    monkeypatch.setenv("NVIDIA_API_KEY","synthetic-nvidia-key")
    with pytest.raises(ValueError,match="requires a Token Factory key"):
        stack_module.Stack(request)
    monkeypatch.setenv("NEBIUS_API_KEY","synthetic-token-factory-key")
    monkeypatch.setenv("REPAIR_INVITE_CODES","synthetic-private-code")
    stack=stack_module.Stack(request)
    assert stack.env["NEBIUS_API_KEY"]=="synthetic-token-factory-key"
    assert not stack.env["NVIDIA_API_KEY"]


@pytest.mark.parametrize("key",["REPAIR_TOTAL_USD_CAP","REPAIR_MAX_ACTIVE_TASKS"])
def test_judge_profile_rejects_disabling_required_limits(stack_module,tmp_path,monkeypatch,key):
    monkeypatch.setenv(key,"0")
    with pytest.raises(ValueError,match="positive lifetime allowance"):
        stack_module.Stack(args(tmp_path))
