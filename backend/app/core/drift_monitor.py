"""MLOps — scheduled drift monitoring with automatic challenger retraining.

Opt-in (DRIFT_MONITOR_INTERVAL_S > 0): a background loop checks every ACTIVE
deployment's live-traffic drift (same PSI/KS computation as the Drift tab).
When a deployment goes "drifted", it auto-launches ONE challenger re-run on the
same dataset+goal (via the shared clone_and_submit rail the trust gate uses),
throttled by a per-run cooldown so a persistently drifted model doesn't spawn
a run per tick. The champion stays deployed — the challenger's result appears
as a normal run for the human to promote (champion/challenger, not auto-swap).

This closes the loop the UI's "Retrain challenger" button does manually.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sqlalchemy import select

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("drift_monitor")

_COOLDOWN_S = 24 * 3600   # at most one auto-challenger per deployment per day


async def _check_once() -> None:
    from app.database import AsyncSessionLocal
    from app.models.run import Deployment
    from app.api.routes.inference import drift_report
    from app.redis_client import get_redis
    from app.core.rerun import clone_and_submit
    from app.redis_client import publish_progress

    async with AsyncSessionLocal() as db:
        deployments = (await db.execute(
            select(Deployment).where(Deployment.status == "active")
        )).scalars().all()
        run_ids = [d.run_id for d in deployments]

    for run_id in run_ids:
        try:
            report = await drift_report(run_id)
        except Exception as e:
            _log.warning("drift_check_failed", run_id=run_id, error=str(e)[:150])
            continue
        if report.get("overall_status") != "drifted":
            continue

        redis = await get_redis()
        cooldown_key = f"run:{run_id}:drift_challenger_at"
        try:
            if await redis.get(cooldown_key):
                continue  # already launched a challenger recently
            await redis.set(cooldown_key, datetime.now(timezone.utc).isoformat(),
                            ex=_COOLDOWN_S)
        except Exception:
            continue  # no Redis → no dedupe → skip rather than spam runs

        new_id = await clone_and_submit(run_id, exclude_columns=[], mark_autocorrect=False)
        _log.info("drift_challenger_launched", run_id=run_id, challenger=new_id,
                  max_psi=report.get("max_psi"))
        await publish_progress(run_id, {
            "agent": "drift_monitor",
            "message": (f"Live-traffic drift detected (max PSI {report.get('max_psi')}) — "
                        f"auto-launched challenger run {str(new_id)[:8]}. The champion stays "
                        "deployed until you promote the challenger."),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })


async def drift_monitor_loop() -> None:
    interval = int(getattr(settings, "DRIFT_MONITOR_INTERVAL_S", 0) or 0)
    if interval <= 0:
        return
    _log.info("drift_monitor_started", interval_s=interval)
    while True:
        try:
            await _check_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:  # the monitor must never die
            _log.warning("drift_monitor_tick_failed", error=str(e)[:200])
        await asyncio.sleep(interval)
