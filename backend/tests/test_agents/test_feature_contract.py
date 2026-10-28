"""Feature-engineer contract guard (bank+marketing fix).

Feature engineering is additive/best-effort: enriched.csv MUST be a superset of
processed.csv (all its columns + __target__). A malformed output (the agentic path
once wrote a 3-column file, losing __target__ and 50 features → model_selector
crash) must be REJECTED and fall back to the processed data, never propagated.
"""

import pandas as pd
import pytest


def _superset_ok(proc_cols: set, enr_cols: set) -> bool:
    """Mirror of the guard's condition in feature_engineer.run()."""
    return "__target__" in enr_cols and proc_cols.issubset(enr_cols)


def test_valid_superset_accepted(tmp_path):
    proc = tmp_path / "processed.csv"
    pd.DataFrame({"num__age": [1, 2], "cat__x": [0, 1], "__target__": [0, 1]}).to_csv(proc, index=False)
    enr = tmp_path / "enriched.csv"
    # all processed cols + target + a new engineered feature
    pd.DataFrame({"num__age": [1, 2], "cat__x": [0, 1], "__target__": [0, 1],
                  "age_sq": [1, 4]}).to_csv(enr, index=False)
    pc = set(pd.read_csv(proc, nrows=0).columns)
    ec = set(pd.read_csv(enr, nrows=0).columns)
    assert _superset_ok(pc, ec)


def test_missing_target_rejected(tmp_path):
    # The exact bank+marketing failure: enriched has the RAW target name, not __target__.
    pc = {"num__age", "cat__x", "__target__"}
    ec = {"duration", "y", "duration_sq"}      # what the agentic path actually wrote
    assert not _superset_ok(pc, ec)


def test_dropped_features_rejected(tmp_path):
    pc = {"num__age", "cat__x", "num__balance", "__target__"}
    ec = {"num__age", "__target__", "age_sq"}   # dropped cat__x and num__balance
    assert not _superset_ok(pc, ec)


def test_guard_condition_matches_source():
    """Pin that the guard logic in the agent still reads as written (a refactor
    that weakens it should fail here)."""
    import inspect
    from app.agents import feature_engineer
    src = inspect.getsource(feature_engineer.FeatureEngineerAgent.run)
    assert '"__target__" in enr_cols and proc_cols.issubset(enr_cols)' in src
    assert 'enriched_path": processed_path' in src   # falls back to processed data
