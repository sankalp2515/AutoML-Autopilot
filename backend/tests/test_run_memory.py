"""Phase 3 — cross-run memory: signature stability, writers, brief, caps."""

import pytest

from app.core import run_memory


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(run_memory.settings, "DATA_DIR", str(tmp_path))
    yield tmp_path


def _csv(tmp_path, name: str, header: str) -> str:
    p = tmp_path / name
    p.write_text(header + "\n1,2,3\n", encoding="utf-8")
    return str(p)


def test_signature_stable_across_order_and_case(tmp_path):
    a = run_memory.dataset_signature(_csv(tmp_path, "a.csv", "Age,Fare,Survived"))
    b = run_memory.dataset_signature(_csv(tmp_path, "b.csv", "survived,age,fare"))
    c = run_memory.dataset_signature(_csv(tmp_path, "c.csv", "age,fare,other"))
    assert a and a == b
    assert a != c


def test_signature_missing_file_is_empty():
    assert run_memory.dataset_signature("/nope/nothing.csv") == ""


def test_record_outcome_and_leakage_accumulate():
    sig = "cafebabe" * 2
    run_memory.record_run_outcome(sig, "r1", grade="F", leaked_columns=["threat_score"],
                                  winner_model="RandomForest", final_score=1.0)
    run_memory.record_run_outcome(sig, "r2", grade="A", leaked_columns=["threat_score", "ioc"],
                                  winner_model="RandomForest", final_score=0.91)
    mem = run_memory.load_memory(sig)
    assert mem["leakage_columns"]["threat_score"] == 2
    assert mem["leakage_columns"]["ioc"] == 1
    assert mem["winning_models"]["RandomForest"] == 2
    assert len(mem["runs"]) == 2


def test_record_investigation_dedupes_and_caps():
    sig = "deadbeef" * 2
    v = [{"verdict": "confirmed", "statement": "model relies on one feature",
          "primitive": "feature_ablation", "observed": 0.4},
         {"verdict": "confirmed", "statement": "model relies on one feature",  # dup
          "primitive": "feature_ablation", "observed": 0.4},
         {"verdict": "refuted", "statement": "slice X is weak",
          "primitive": "slice_performance", "observed": 0.01},
         "garbage", {"verdict": "inconclusive", "statement": "meh"}]
    run_memory.record_investigation(sig, "r1", v)
    mem = run_memory.load_memory(sig)
    assert len(mem["confirmed_hypotheses"]) == 1
    assert len(mem["refuted_hypotheses"]) == 1


def test_memory_brief_mentions_key_facts():
    sig = "beefcafe" * 2
    run_memory.record_run_outcome(sig, "r1", grade="F", leaked_columns=["secret_col"],
                                  winner_model="GradientBoosting", final_score=0.99)
    run_memory.record_investigation(sig, "r1", [
        {"verdict": "confirmed", "statement": "target leaks via secret_col",
         "primitive": "target_correlation", "observed": 0.999}])
    brief = run_memory.memory_brief(run_memory.load_memory(sig))
    assert "secret_col" in brief
    assert "GradientBoosting" in brief
    assert "target leaks via secret_col" in brief
    assert run_memory.memory_brief({}) == ""


def test_writers_never_raise_on_bad_input():
    run_memory.record_run_outcome("", "r1", grade="A")            # no sig → no-op
    run_memory.record_investigation("", "r1", [])
    run_memory.record_run_outcome("sig123", "r1", leaked_columns=[None, 42, ""])
    mem = run_memory.load_memory("sig123")
    assert mem["leakage_columns"] == {}
