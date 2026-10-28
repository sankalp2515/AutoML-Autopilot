import json
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

_redis_pool: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def close_redis() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


async def publish_progress(run_id: str, payload: dict[str, Any]) -> None:
    """Publish a live progress message AND append it to a replayable log.

    Pub/sub is ephemeral — a client that connects after a message is published
    never sees it. So we also store every message in a capped, TTL'd Redis list
    that the WebSocket replays on connect, so a late-joining browser still sees
    the whole run from the start.
    """
    redis = await get_redis()
    msg = json.dumps(payload)
    log_key = f"run:{run_id}:progress:log"
    pipe = redis.pipeline()
    pipe.publish(f"run:{run_id}:progress", msg)
    pipe.rpush(log_key, msg)
    pipe.ltrim(log_key, -1000, -1)   # keep at most the last 1000 messages
    pipe.expire(log_key, 86400)
    await pipe.execute()


async def get_progress_log(run_id: str) -> list[str]:
    """Replay buffer: every progress message published for this run so far."""
    redis = await get_redis()
    return await redis.lrange(f"run:{run_id}:progress:log", 0, -1)


async def set_run_state(run_id: str, state: dict[str, Any]) -> None:
    redis = await get_redis()
    await redis.set(f"run:{run_id}:state", json.dumps(state, default=str), ex=86400)


async def get_run_state(run_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(f"run:{run_id}:state")
    return json.loads(raw) if raw else None


async def set_checkpoint(run_id: str, node: str, state: dict[str, Any]) -> None:
    """P1.8 — after each successful graph node, persist the merged state so Retry
    can resume from the failed node instead of restarting from scratch."""
    redis = await get_redis()
    payload = json.dumps({"node": node, "state": state}, default=str)
    await redis.set(f"run:{run_id}:checkpoint", payload, ex=604800)


async def get_checkpoint(run_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(f"run:{run_id}:checkpoint")
    return json.loads(raw) if raw else None


async def clear_checkpoint(run_id: str) -> None:
    redis = await get_redis()
    await redis.delete(f"run:{run_id}:checkpoint")


async def set_trust_report(run_id: str, report: dict[str, Any]) -> None:
    redis = await get_redis()
    await redis.set(f"run:{run_id}:trust", json.dumps(report, default=str), ex=604800)


async def get_trust_report(run_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(f"run:{run_id}:trust")
    return json.loads(raw) if raw else None


async def set_autocorrect_origin(new_run_id: str, origin_run_id: str) -> None:
    """Mark a run as an auto-correction so its own trust-gate won't recurse."""
    redis = await get_redis()
    await redis.set(f"run:{new_run_id}:autocorrect_origin", origin_run_id, ex=604800)


async def get_autocorrect_origin(run_id: str) -> str | None:
    redis = await get_redis()
    return await redis.get(f"run:{run_id}:autocorrect_origin")


async def set_investigation_report(run_id: str, report: dict[str, Any]) -> None:
    redis = await get_redis()
    await redis.set(f"run:{run_id}:investigation", json.dumps(report, default=str), ex=604800)


async def get_investigation_report(run_id: str) -> dict[str, Any] | None:
    redis = await get_redis()
    raw = await redis.get(f"run:{run_id}:investigation")
    return json.loads(raw) if raw else None
