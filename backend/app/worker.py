"""arq worker entrypoint (Phase 4, opt-in).

Run a dedicated worker container with:
    arq app.worker.WorkerSettings

It pulls run jobs off the Redis queue and executes the pipeline — decoupled from
the API process, so runs survive API restarts and scale horizontally. Only used
when USE_JOB_QUEUE=true; otherwise the API runs pipelines in-process.
"""

from __future__ import annotations

from arq.connections import RedisSettings

from app.config import settings


async def run_pipeline_job(ctx, run_id: str, resume_from: str | None = None) -> None:
    from app.agents.orchestrator import run_pipeline
    await run_pipeline(run_id, resume_from=resume_from)


async def _startup(ctx) -> None:
    # Expose this worker's Prometheus metrics on :8001 so they're scraped too.
    # Pipelines run HERE when USE_JOB_QUEUE=true, so without this their metrics
    # (pipeline/agent/LLM counters) never reach Prometheus — the "No data" bug.
    try:
        from prometheus_client import start_http_server
        start_http_server(8001)
    except Exception:
        pass


def _redis_settings() -> RedisSettings:
    # Long pipeline runs (minutes) leave the Redis connection idle; Docker/Redis
    # can drop it, and arq then times out storing the result. Generous reconnect
    # timeouts + retries make that recoverable instead of crashing the worker.
    s = RedisSettings.from_dsn(settings.REDIS_URL)
    s.conn_timeout = 30
    s.conn_retries = 5
    s.conn_retry_delay = 2
    return s


class WorkerSettings:
    functions = [run_pipeline_job]
    on_startup = _startup
    redis_settings = _redis_settings()
    job_timeout = 3600
    max_jobs = 4
    # Touch Redis every 30s so the connection stays warm during long jobs and
    # doesn't get dropped while a pipeline runs.
    health_check_interval = 30
