"""The platform seam — studios (applications) on the shared chassis.

Each studio registers here: id, label, a graph builder, its dataset contract,
and the UI tabs it owns. run_pipeline dispatches THROUGH the registry (no more
string if/else), and /api/v1/studios drives the New Run modal — so shipping a
new studio is one registration + one graph module, with the whole chassis
(sandbox, LLM client, trust layer, memory, discovery, deploy rail,
observability) inherited for free.

See docs/SPEC_platform.md. Quant Research OS has a reserved slot (available:
False) — deliberately not implemented yet; it is its own workstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class Studio:
    id: str                      # run.pipeline value (backward-compatible)
    label: str
    tagline: str
    available: bool
    # Returns a COMPILED LangGraph (entry_point supported where resume works).
    graph_builder: Callable[..., Any] | None = None
    supports_resume: bool = False
    dataset_contract: str = ""
    ui_tabs: tuple[str, ...] = ()


def _tabular_graph(entry_point: str = "data_auditor"):
    from app.agents.orchestrator import build_graph, get_graph
    if entry_point != "data_auditor":
        return build_graph(entry_point=entry_point)
    return get_graph()


def _timeseries_graph(entry_point: str = ""):
    from app.agents.ts_orchestrator import get_ts_graph
    return get_ts_graph()


STUDIOS: dict[str, Studio] = {
    "tabular": Studio(
        id="tabular",
        label="AutoML Studio",
        tagline="Classification & regression — 10 agents, honest holdout, trust-gated",
        available=True,
        graph_builder=_tabular_graph,
        supports_resume=True,
        dataset_contract="CSV with a target column; binary/multiclass/multilabel/regression; free text OK",
        ui_tabs=("overview", "pipeline", "metrics", "trust", "investigate",
                 "compare", "playground", "drift", "notebook", "deploy", "tools", "llm"),
    ),
    "timeseries": Studio(
        id="timeseries",
        label="Forecasting Studio",
        tagline="Walk-forward forecasting — temporal validation by construction, DL sequence models",
        available=True,
        graph_builder=_timeseries_graph,
        supports_resume=False,
        dataset_contract="CSV with a timestamp column and a numeric target",
        ui_tabs=("overview", "pipeline", "metrics", "notebook", "deploy", "tools", "llm"),
    ),
    # Reserved slot — its own workstream (docs/SPEC_platform.md, Studio C).
    "quant": Studio(
        id="quant",
        label="Quant Research OS",
        tagline="Signal research on the trust chassis — purged CV, cost-aware backtests (coming soon)",
        available=False,
    ),
}


def get_studio(pipeline: str) -> Studio:
    """The studio for a run.pipeline value; unknown values fall back to tabular
    (never crash a run over a label)."""
    return STUDIOS.get(pipeline or "tabular", STUDIOS["tabular"])


def list_studios() -> list[dict[str, Any]]:
    return [
        {"id": s.id, "label": s.label, "tagline": s.tagline,
         "available": s.available, "dataset_contract": s.dataset_contract}
        for s in STUDIOS.values()
    ]
