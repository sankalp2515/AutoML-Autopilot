"""Parallel task execution with a Ray backend and a sequential fallback (Tower #1).

Independent ML work — training candidate models, evaluating hyperparameter trials —
is embarrassingly parallel. `parallel_map` fans those tasks out across a Ray cluster
when `USE_RAY` is on (and Ray is importable), and otherwise runs them sequentially.
The fallback means enabling Ray is a deliberate, separately-verified step that can
never break a run: if Ray is missing or `ray.init()` fails, we degrade to a plain loop.

`fn` must be a TOP-LEVEL (picklable) function and each item must be picklable, because
Ray ships them to worker processes.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("parallel")


def parallel_map(fn: Callable[[Any], Any], items: Iterable[Any],
                 use_ray: bool | None = None, num_cpus: int = 1) -> list[Any]:
    """Apply `fn` to each item, in parallel via Ray when enabled, else sequentially.
    Order of results matches the order of `items`."""
    items = list(items)
    enabled = settings.USE_RAY if use_ray is None else use_ray

    if enabled:
        try:
            import ray
            if not ray.is_initialized():
                # S3 seam: RAY_ADDRESS attaches to an existing cluster
                # ("ray://head:10001" / "auto"); empty = local worker pool.
                # Same call path from one box to a fleet — no code change.
                addr = getattr(settings, "RAY_ADDRESS", "") or None
                ray.init(address=addr, ignore_reinit_error=True,
                         log_to_driver=False, logging_level="ERROR")
            remote_fn = ray.remote(num_cpus=num_cpus)(fn)
            refs = [remote_fn.remote(x) for x in items]
            results = ray.get(refs)
            _log.info("parallel_map_ray", n=len(items))
            return results
        except Exception as exc:  # missing ray, init failure, serialization issue
            _log.warning("parallel_map_ray_fallback_sequential", error=str(exc)[:200])

    return [fn(x) for x in items]
