"""MLflow tracking — concurrency-safe and failure-safe (2026-07-03 rewrite).

The original implementation used MLflow's FLUENT api (mlflow.start_run /
mlflow.log_metric), whose "active run" is PROCESS-GLOBAL state. With two
pipelines in one backend process the second start_run raised "Run ... is
already active", killing the task before `pipeline_started` and leaving the
run a zombie — and mid-run metrics could attribute to whichever run was
globally active. Same anti-pattern as the old LLM-client singleton race.

This version:
- binds the MLflow run id in a **contextvar** (per asyncio task = per pipeline
  run, like app.core.context), so concurrent runs never collide;
- uses the low-level **MlflowClient** with an explicit run id for every write
  (no global state at all);
- wraps EVERY call so MLflow being down/slow degrades to a warning — telemetry
  must never take down a pipeline (production doctrine).

The public API is unchanged — no agent edits required.
"""
from __future__ import annotations

import contextvars
import logging
import os
from typing import Any

from app.config import settings

_log = logging.getLogger("mlflow_tracker")

_EXPERIMENT = "automl-orchestrator"

# This task's MLflow run id (set by start_run, used by every logger below).
_ctx_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mlflow_run_id", default=None
)

_client = None
_experiment_id: str | None = None


def setup_mlflow() -> None:
    try:
        import mlflow
        mlflow.set_tracking_uri(settings.MLFLOW_TRACKING_URI)
    except Exception as exc:
        _log.warning("mlflow setup failed (tracking disabled): %s", exc)


def _get_client():
    global _client
    if _client is None:
        from mlflow.tracking import MlflowClient
        _client = MlflowClient(tracking_uri=settings.MLFLOW_TRACKING_URI)
    return _client


def _get_experiment_id() -> str:
    global _experiment_id
    if _experiment_id is None:
        c = _get_client()
        exp = c.get_experiment_by_name(_EXPERIMENT)
        _experiment_id = exp.experiment_id if exp else c.create_experiment(_EXPERIMENT)
    return _experiment_id


def start_run(run_id: str, tags: dict[str, str] | None = None) -> str:
    """Create an MLflow run for THIS pipeline task. Returns '' (and disables
    tracking for the task) if MLflow is unavailable — never raises."""
    try:
        c = _get_client()
        run = c.create_run(
            _get_experiment_id(),
            tags={"mlflow.runName": run_id, "run_id": run_id, **(tags or {})},
        )
        _ctx_run_id.set(run.info.run_id)
        return run.info.run_id
    except Exception as exc:
        _log.warning("mlflow start_run failed for %s (tracking disabled): %s", run_id, exc)
        _ctx_run_id.set(None)
        return ""


def end_run(status: str = "FINISHED") -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        _get_client().set_terminated(rid, status=status)
    except Exception as exc:
        _log.warning("mlflow end_run failed: %s", exc)
    _ctx_run_id.set(None)


def log_params(params: dict[str, Any]) -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        from mlflow.entities import Param
        safe = [Param(k, str(v)[:250]) for k, v in params.items() if v is not None]
        _get_client().log_batch(rid, params=safe)
    except Exception as exc:
        _log.warning("mlflow log_params failed: %s", exc)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        import time
        from mlflow.entities import Metric
        ts = int(time.time() * 1000)
        ms = [Metric(k, float(v), ts, step or 0)
              for k, v in metrics.items() if v is not None]
        _get_client().log_batch(rid, metrics=ms)
    except Exception as exc:
        _log.warning("mlflow log_metrics failed: %s", exc)


def log_metric(key: str, value: float, step: int | None = None) -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        _get_client().log_metric(rid, key, float(value), step=step or 0)
    except Exception as exc:
        _log.warning("mlflow log_metric failed: %s", exc)


def log_artifact(local_path: str) -> None:
    """Upload a local file to the MLflow artifact store via HTTP (requires --serve-artifacts)."""
    rid = _ctx_run_id.get()
    if not rid:
        return
    if not os.path.exists(local_path):
        _log.warning("log_artifact skipped — file not found: %s", local_path)
        return
    try:
        _get_client().log_artifact(rid, local_path)
    except Exception as exc:
        _log.error("log_artifact failed for %s: %s", local_path, exc)


def set_tag(key: str, value: str) -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        _get_client().set_tag(rid, key, str(value))
    except Exception as exc:
        _log.warning("mlflow set_tag failed: %s", exc)


def log_dict(data: Any, filename: str) -> None:
    rid = _ctx_run_id.get()
    if not rid:
        return
    try:
        _get_client().log_dict(rid, data, filename)
    except Exception as exc:
        _log.error("log_dict failed for %s: %s", filename, exc)
