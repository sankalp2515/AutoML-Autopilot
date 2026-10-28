"""Clone-and-resubmit a run with extra excluded columns.

Shared by the manual `POST /runs/{id}/rerun` endpoint and the inline trust-gate's
auto-correction. Opens its own DB session so it's safe to call from inside the
pipeline (the worker) as well as from a request handler.
"""
from __future__ import annotations

import os
import shutil
import uuid

from sqlalchemy import select

from app.config import settings
from app.core.job_queue import submit_run
from app.core.logging import get_logger
from app.database import AsyncSessionLocal
from app.models.run import Run
from app.redis_client import set_autocorrect_origin

_log = get_logger("rerun")


async def clone_and_submit(
    origin_run_id: str,
    extra_exclude: list[str],
    tenant: str | None = None,
    background_tasks=None,
    mark_autocorrect: bool = False,
) -> str | None:
    """Create a new run on the same dataset/goal with `extra_exclude` added, submit
    it, and return the new run id (or None if the original/dataset is gone)."""
    async with AsyncSessionLocal() as db:
        orig = (await db.execute(select(Run).where(Run.id == origin_run_id))).scalar_one_or_none()
        if not orig or not orig.dataset_path or not os.path.exists(orig.dataset_path):
            return None

        new_id = str(uuid.uuid4())
        new_dir = os.path.join(settings.DATA_DIR, new_id)
        os.makedirs(new_dir, exist_ok=True)
        new_path = os.path.join(new_dir, "dataset.csv")
        shutil.copy(orig.dataset_path, new_path)

        merged = sorted(set((orig.exclude_columns or []) + [c.strip() for c in extra_exclude if c.strip()]))
        new_run = Run(
            id=new_id, status="queued", tenant_id=tenant or orig.tenant_id,
            dataset_filename=orig.dataset_filename, dataset_path=new_path,
            user_goal=orig.user_goal, exclude_columns=merged,
            fp_fn_preference=orig.fp_fn_preference,
            interpretability_required=orig.interpretability_required,
            pipeline=orig.pipeline,
        )
        db.add(new_run)
        await db.commit()

    if mark_autocorrect:
        try:
            await set_autocorrect_origin(new_id, origin_run_id)
        except Exception:
            pass
    await submit_run(new_id, background_tasks)
    _log.info("run_cloned", origin=origin_run_id, new_run=new_id, excluded=len(merged))
    return new_id
