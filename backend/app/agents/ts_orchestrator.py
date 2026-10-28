"""
Time-Series Studio graph (P3–P6). Separate LangGraph pipeline on the shared chassis.

Sequence: ts_framer → ts_auditor → ts_feature_builder → ts_modeler → exporter,
with fail-fast routing to END on any agent failure (mirrors the tabular graph).
Reuses the tabular ExporterAgent for the notebook / model card / inference bundle.
"""
from __future__ import annotations

from typing import Literal

from langgraph.graph import END, StateGraph

from app.agents.exporter import ExporterAgent
from app.agents.ts_agents import (
    TSAuditorAgent,
    TSFeatureBuilderAgent,
    TSFramerAgent,
    TSModelerAgent,
)
from app.core.state import AgentState

_ts_framer = TSFramerAgent()
_ts_auditor = TSAuditorAgent()
_ts_feature_builder = TSFeatureBuilderAgent()
_ts_modeler = TSModelerAgent()
_exporter = ExporterAgent()


async def _n_framer(state: AgentState) -> dict:
    return await _ts_framer.run(state)


async def _n_auditor(state: AgentState) -> dict:
    return await _ts_auditor.run(state)


async def _n_features(state: AgentState) -> dict:
    return await _ts_feature_builder.run(state)


async def _n_modeler(state: AgentState) -> dict:
    return await _ts_modeler.run(state)


async def _n_exporter(state: AgentState) -> dict:
    return await _exporter.run(state)


def _failfast(next_node: str):
    def router(state: AgentState) -> str:
        return END if state.get("status") == "failed" else next_node
    return router


def build_ts_graph():
    g = StateGraph(AgentState)
    g.add_node("ts_framer", _n_framer)
    g.add_node("ts_auditor", _n_auditor)
    g.add_node("ts_feature_builder", _n_features)
    g.add_node("ts_modeler", _n_modeler)
    g.add_node("exporter", _n_exporter)

    g.set_entry_point("ts_framer")
    g.add_edge("ts_framer", "ts_auditor")
    g.add_conditional_edges("ts_auditor", _failfast("ts_feature_builder"),
                            {"ts_feature_builder": "ts_feature_builder", END: END})
    g.add_conditional_edges("ts_feature_builder", _failfast("ts_modeler"),
                            {"ts_modeler": "ts_modeler", END: END})
    g.add_conditional_edges("ts_modeler", _failfast("exporter"),
                            {"exporter": "exporter", END: END})
    g.add_edge("exporter", END)
    return g.compile()


_compiled_ts_graph = None


def get_ts_graph():
    global _compiled_ts_graph
    if _compiled_ts_graph is None:
        _compiled_ts_graph = build_ts_graph()
    return _compiled_ts_graph
