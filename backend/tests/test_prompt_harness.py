"""Prompt-regression harness — CI-deterministic tests.

Two guarantees:
1. VALIDATORS are correct: 'good' golden responses pass their contract; 'bad' ones
   (realistic degraded-prompt outputs) are caught. This is what makes the harness
   able to detect a prompt regression when run live (tools/record_golden.py).
2. The LLM REPLAY hook works: with LLM_REPLAY_FILE set, the client returns recorded
   responses with no network — so agent parsing can be exercised deterministically.
"""
import json
import os
from pathlib import Path

import pytest

from tests.prompt_harness import validators as V

_GOLDEN = json.loads((Path(__file__).parent / "prompt_harness" / "golden" / "responses.json").read_text())


# ── 1. Validators: good passes, bad is caught ─────────────────────────────────

@pytest.mark.parametrize("agent", list(V.VALIDATORS))
def test_good_golden_passes_its_validator(agent):
    good = _GOLDEN[agent]["good"]
    violations = V.VALIDATORS[agent](good, _ctx_for(agent))
    assert violations == [], f"{agent} good response should be valid, got: {violations}"


def _ctx_for(agent):
    if agent == "problem_framer":
        return {"columns": ["age", "job", "y"]}
    if agent == "model_selector":
        return {"allowed_classes": ["XGBClassifier", "HistGradientBoostingClassifier",
                                    "RandomForestClassifier", "LogisticRegression"]}
    return {}


def _bad_cases():
    cases = []
    for agent, entries in _GOLDEN.items():
        if agent.startswith("_"):
            continue
        for key in entries:
            if key.startswith("bad_"):
                cases.append((agent, key))
    return cases


@pytest.mark.parametrize("agent,key", _bad_cases())
def test_bad_golden_is_caught(agent, key):
    resp = _GOLDEN[agent][key]
    violations = V.VALIDATORS[agent](resp, _ctx_for(agent))
    assert violations, f"{agent}/{key} should have been flagged but passed clean"


def test_validators_never_crash_on_garbage():
    for fn in V.VALIDATORS.values():
        for junk in (None, [], "str", 42, {"random": "keys"}):
            out = fn(junk, {})
            assert isinstance(out, list)   # returns violations, never raises


# ── 2. The replay hook is deterministic and prompt-version sensitive ──────────

def test_replay_hook_returns_recorded_response(tmp_path, monkeypatch):
    from app.core import llm
    system, user = "SYSTEM PROMPT v1", "user dossier"
    recorded = '{"task_type": "regression", "primary_metric": "rmse", "target_column": "price"}'
    fixture = {llm.replay_key(system, user): recorded}
    fpath = tmp_path / "replay.json"
    fpath.write_text(json.dumps(fixture))
    monkeypatch.setenv("LLM_REPLAY_FILE", str(fpath))
    llm._replay_loaded_from = None   # force reload

    assert llm._replay_lookup(system, user) == recorded
    # A SYSTEM-prompt change misses the fixture → forces a real (re-recorded) response.
    assert llm._replay_lookup("SYSTEM PROMPT v2 CHANGED", user) is None
    # Unset → hook is inert (production path).
    monkeypatch.delenv("LLM_REPLAY_FILE")
    llm._replay_loaded_from = None
    assert llm._replay_lookup(system, user) is None


def test_replay_key_stable_for_same_inputs():
    from app.core import llm
    k1 = llm.replay_key("sys", "usr")
    k2 = llm.replay_key("sys", "usr")
    assert k1 == k2 and ":" in k1
