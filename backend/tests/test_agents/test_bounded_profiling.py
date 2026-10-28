"""Bounded-memory auditor profiling (P2.3).

For very large files the auditor profiles a capped SAMPLE (bounded RAM) but reports
the TRUE row count from a fast byte-estimate — so a 10M+ row file never materializes
fully at the audit step. Small files are read whole (exact).
"""
import numpy as np
import pandas as pd

from app.agents.data_auditor import PROFILING_CODE


def _run_profile(csv_path: str, row_cap: int) -> dict:
    code = 'target_column="target"\n' + PROFILING_CODE
    code = code.replace("_PROFILE_ROW_CAP = 500_000", f"_PROFILE_ROW_CAP = {row_cap}")
    ns = {"dataset_path": csv_path}
    exec(compile(code, "<profile>", "exec"), ns)
    return ns["RESULT"]


def _make_csv(tmp_path, n_rows: int) -> str:
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"a": rng.normal(size=n_rows), "b": rng.integers(0, 5, n_rows),
                       "target": rng.integers(0, 2, n_rows)})
    p = tmp_path / "d.csv"
    df.to_csv(p, index=False)
    return str(p)


def test_large_file_profiles_on_sample_but_reports_true_count(tmp_path):
    path = _make_csv(tmp_path, 40_000)
    res = _run_profile(path, row_cap=10_000)      # force the sample path
    assert res["profiled_on_sample"] is True
    assert res["profile_sample_rows"] == 10_000    # only 10k loaded into RAM
    # True row count reported (within the estimator's tolerance), NOT the sample size.
    assert res["shape"][0] > 30_000
    assert res["shape"][1] == 3


def test_small_file_read_whole_exact(tmp_path):
    path = _make_csv(tmp_path, 5_000)
    res = _run_profile(path, row_cap=500_000)      # no cap hit
    assert res["profiled_on_sample"] is False
    assert res["shape"][0] == 5_000                # exact
    assert res["profile_sample_rows"] is None


def test_profile_has_core_keys(tmp_path):
    res = _run_profile(_make_csv(tmp_path, 2_000), row_cap=500_000)
    for k in ("shape", "null_pct", "cardinality", "dtypes", "memory_mb"):
        assert k in res
