"""Run submission abstraction (Phase 4).

Default: an in-process background task (the pre-Phase-4 behavior — unchanged).
Opt-in (``USE_JOB_QUEUE=true``): enqueue to a durable **arq** Redis queue so runs
survive an API restart and can be processed by dedicated worker containers (the
real multi-tenant/production path). The flag defaults OFF, so enabling it is a
deliberate, separately-verified step — it cannot break the running app.

The worker entrypoint lives in app/worker.py.
"""

from __future__ import annotations

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("job_queue")


async def submit_run(run_id: str, background_tasks=None, resume_from: str | None = None) -> None:
    """Schedule a pipeline run. Uses arq when USE_JOB_QUEUE is set, else an
    in-process background task (requires `background_tasks`). `resume_from`
    (P1.8) re-enters the graph at a failed node using the run's checkpoint."""
    if settings.USE_JOB_QUEUE:
        try:
            from arq.connections import create_pool, RedisSettings
            pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
            await pool.enqueue_job("run_pipeline_job", run_id, resume_from)
            await pool.close()
            _log.info("run_enqueued", run_id=run_id, queue="arq", resume_from=resume_from)
            return
        except Exception as exc:
            # Never strand a run: if the queue is unreachable, fall back to inline.
            _log.error("job_queue_enqueue_failed_fallback_inline", run_id=run_id, error=str(exc)[:200])

    if background_tasks is not None:
        background_tasks.add_task(_run_inline, run_id, resume_from)
    else:
        import asyncio
        asyncio.create_task(_run_inline(run_id, resume_from))


async def _run_inline(run_id: str, resume_from: str | None = None) -> None:
    from app.agents.orchestrator import run_pipeline
    await run_pipeline(run_id, resume_from=resume_from)
