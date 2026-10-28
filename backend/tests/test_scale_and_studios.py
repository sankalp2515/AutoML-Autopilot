"""Scale strategy (S2/S3 seam) + studio registry (platform seam)."""

import pytest

from app.core import scale, studios


def _csv(tmp_path, n_rows: int) -> str:
    p = tmp_path / "d.csv"
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("a,b,y\n")
        for i in range(n_rows):
            fh.write(f"{i},{i % 7},{i % 2}\n")
    return str(p)


def test_row_estimate_close(tmp_path):
    path = _csv(tmp_path, 50_000)
    est = scale.count_rows_fast(path)
    assert abs(est - 50_000) / 50_000 < 0.05      # within 5%


def test_plan_in_memory(tmp_path, monkeypatch):
    monkeypatch.setattr(scale.settings, "MAX_TRAIN_ROWS", 200_000)
    plan = scale.plan_for(_csv(tmp_path, 1_000))
    assert plan.strategy == "in_memory"


def test_plan_sampled(tmp_path, monkeypatch):
    monkeypatch.setattr(scale.settings, "MAX_TRAIN_ROWS", 10_000)
    plan = scale.plan_for(_csv(tmp_path, 50_000))
    assert plan.strategy == "sampled"
    assert "learning-curve" in plan.reason


def test_plan_streaming(monkeypatch, tmp_path):
    monkeypatch.setattr(scale.settings, "STREAMING_THRESHOLD_ROWS", 10_000, raising=False)
    plan = scale.plan_for(_csv(tmp_path, 50_000))
    assert plan.strategy == "streaming"
    assert "every row" in plan.reason


def test_plan_missing_file():
    assert scale.plan_for("/nope.csv").strategy == "in_memory"


# ── Studios ───────────────────────────────────────────────────────────────────

def test_studio_registry_shape():
    ids = {s["id"] for s in studios.list_studios()}
    assert {"tabular", "timeseries", "quant"} <= ids
    assert studios.get_studio("tabular").available
    assert not studios.get_studio("quant").available   # reserved slot


def test_unknown_pipeline_falls_back_to_tabular():
    assert studios.get_studio("garbage").id == "tabular"
    assert studios.get_studio("").id == "tabular"


def test_available_studios_build_graphs():
    for s in studios.STUDIOS.values():
        if s.available:
            assert s.graph_builder() is not None
