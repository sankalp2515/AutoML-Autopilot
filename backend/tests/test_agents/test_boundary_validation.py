"""P0.3 — boundary-validation helpers + P0.1 train-row cap + P0.4 agent budget.

The permanent crash-proofing pattern: untrusted LLM/sandbox output is normalized
ONCE at the boundary so downstream access is safe by construction.
"""

import asyncio

import pytest

from app.agents.base_agent import BaseAgent
from app.agents.data_splitter import SPLIT_CODE_TEMPLATE, _MIN_ROWS_FOR_HOLDOUT


# ── Normalization helpers ─────────────────────────────────────────────────────

def test_norm_dicts_drops_non_dicts():
    assert BaseAgent.norm_dicts([{"a": 1}, "junk", 3, None, {"b": 2}]) == [{"a": 1}, {"b": 2}]
    assert BaseAgent.norm_dicts(None) == []
    assert BaseAgent.norm_dicts("not a list") == []


def test_norm_str_list_coerces_dict_entries():
    got = BaseAgent.norm_str_list(["col_a", {"column": "col_b", "reason": "leaky"},
                                   {"name": "col_c"}, 42, None, ""])
    assert got == ["col_a", "col_b", "col_c"]
    assert BaseAgent.norm_str_list({"not": "a list"}) == []


def test_norm_str_map_drops_bad_entries():
    got = BaseAgent.norm_str_map({"a": "median", "b": 3, 4: "mean", "c": None, "d": "knn"})
    assert got == {"a": "median", "d": "knn"}
    assert BaseAgent.norm_str_map(["not", "a", "dict"]) == {}


def test_norm_choice_clamps_to_vocabulary():
    allowed = {"standard", "minmax", "robust", "none"}
    assert BaseAgent.norm_choice("robust", allowed, "standard") == "robust"
    assert BaseAgent.norm_choice("zscore!!", allowed, "standard") == "standard"
    assert BaseAgent.norm_choice(None, allowed, "standard") == "standard"
    assert BaseAgent.norm_choice(["robust"], allowed, "standard") == "standard"


def test_result_dict_never_raises():
    assert BaseAgent.result_dict({"result": {"x": 1}}) == {"x": 1}
    assert BaseAgent.result_dict({"result": ["not", "a", "dict"]}) == {}
    assert BaseAgent.result_dict({"result": None}) == {}
    assert BaseAgent.result_dict({}) == {}


# ── P0.1 — MAX_TRAIN_ROWS cap in the split template ──────────────────────────

def _render_split(max_train_rows: int, max_hard_rows: int = 0) -> str:
    return (
        SPLIT_CODE_TEMPLATE
        .replace("__TARGET_COLUMN__", repr("y"))
        .replace("__TASK_TYPE__", repr("binary_classification"))
        .replace("__HOLDOUT_FRAC__", repr(0.2))
        .replace("__MIN_ROWS__", repr(_MIN_ROWS_FOR_HOLDOUT))
        .replace("__MAX_TRAIN_ROWS__", repr(max_train_rows))
        .replace("__MAX_HARD_ROWS__", repr(max_hard_rows))
        .replace("__KEEP_FULL__", repr(False))
    )


def _run_split(tmp_path, n_rows: int, max_train_rows: int) -> dict:
    import pandas as pd
    import numpy as np

    rng = np.random.default_rng(0)
    df = pd.DataFrame({"x1": rng.normal(size=n_rows),
                       "y": rng.integers(0, 2, size=n_rows)})
    ds = tmp_path / "data.csv"
    df.to_csv(ds, index=False)
    art = tmp_path / "run" / "artifacts"
    art.mkdir(parents=True)
    ns = {"dataset_path": str(ds), "artifacts_dir": str(art) + "/"}
    exec(compile(_render_split(max_train_rows), "<split>", "exec"), ns)
    return ns["RESULT"]


def test_split_caps_train_rows(tmp_path):
    res = _run_split(tmp_path, n_rows=1000, max_train_rows=300)
    assert res["split"] is True
    assert res["train_capped"] is True
    assert res["n_train"] == 300
    assert res["n_train_full"] == 800          # 80% of 1000 before the cap
    assert res["n_holdout"] == 200             # holdout NEVER sampled


def test_split_no_cap_when_under_limit(tmp_path):
    res = _run_split(tmp_path, n_rows=500, max_train_rows=0)
    assert res["split"] is True
    assert res["train_capped"] is False
    assert res["n_train"] == 400


# ── P0.4 — per-agent wall-clock budget in _safe_run ──────────────────────────

def test_safe_run_agent_timeout(monkeypatch):
    from app.agents import orchestrator as orch

    class SlowAgent:
        name = "slow_agent"

        async def run(self, state):
            await asyncio.sleep(5)
            return {"status": "completed"}

    monkeypatch.setattr(orch.settings, "MAX_AGENT_SECONDS", 1)
    out = asyncio.run(orch._safe_run(SlowAgent(), {"run_id": "t"}))
    assert out["status"] == "failed"
    assert "wall-clock budget" in out["error"]
