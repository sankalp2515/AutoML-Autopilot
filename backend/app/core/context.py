"""Request-scoped execution context.

Each pipeline run executes as its own asyncio task (one ``graph.ainvoke`` call),
and ``contextvars`` are copied per task — so two concurrent runs each see their
own values with zero cross-talk. This replaces the old pattern of mutating
attributes on the *singleton* LLM client / agent instances, which silently
corrupted cost + decision attribution the moment two runs overlapped.

Set once at the start of every agent step (``BaseAgent._mark_step("running")``)
and read wherever we need to know "which run / agent is calling right now"
(e.g. ``LLMClient._record``). Never store this state on a shared instance.
"""

from __future__ import annotations

import contextvars

# Defaults make reads safe even outside a pipeline (e.g. ad-hoc LLM calls).
_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automl_run_id", default=None
)
_agent_name: contextvars.ContextVar[str] = contextvars.ContextVar(
    "automl_agent_name", default="unknown"
)
_agent_start: contextvars.ContextVar[float] = contextvars.ContextVar(
    "automl_agent_start", default=0.0
)
# Per-HTTP-request id (Layer 1) — also the correlation/trace id threaded through
# logs across components (Layer 5). Set by the request-id middleware.
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automl_request_id", default=None
)


# The run's CURRENT dataset path (repointed at train.csv by the data_splitter).
# The sandbox defaults its `dataset_path` global to the ORIGINAL upload — every
# post-split template that read that global silently trained on the full dataset
# INCLUDING the holdout (found 2026-07-02). The executor sends this override with
# every execution so templates, param-repairs, and agentic rewrites all see the
# same, correct path.
_dataset_path: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "automl_dataset_path", default=None
)


def set_agent_context(run_id: str, agent_name: str, start_time: float) -> None:
    """Bind the current task to a run + agent. Call at agent-step start."""
    _run_id.set(run_id)
    _agent_name.set(agent_name)
    _agent_start.set(start_time)


def set_dataset_path(path: str | None) -> None:
    """Bind the run's current dataset path (train.csv after the split)."""
    _dataset_path.set(path or None)


def get_dataset_path() -> str | None:
    return _dataset_path.get()


def set_request_id(request_id: str) -> None:
    _request_id.set(request_id)


def get_request_id() -> str | None:
    return _request_id.get()


def get_run_id() -> str | None:
    return _run_id.get()


def get_agent_name() -> str:
    return _agent_name.get()


def get_agent_start() -> float:
    return _agent_start.get()
