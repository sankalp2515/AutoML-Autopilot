import asyncio
import json

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/runs/{run_id}/progress")
async def websocket_progress(websocket: WebSocket, run_id: str) -> None:
    await websocket.accept()

    redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    pubsub = redis.pubsub()

    try:
        await websocket.send_json({"type": "connected", "run_id": run_id})

        # Replay the run's progress so far (covers the case where the browser
        # connects after the pipeline already started), THEN subscribe for live
        # updates. Without this, early agent messages are lost and the UI looks
        # stuck until a late message arrives.
        history = await redis.lrange(f"run:{run_id}:progress:log", 0, -1)
        for raw in history:
            await websocket.send_text(raw)
        await pubsub.subscribe(f"run:{run_id}:progress")

        async def _listen() -> None:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    await websocket.send_text(message["data"])

        listen_task = asyncio.create_task(_listen())

        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                if data == "ping":
                    await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "heartbeat"})
            except WebSocketDisconnect:
                break

        listen_task.cancel()
    finally:
        await pubsub.unsubscribe(f"run:{run_id}:progress")
        await pubsub.aclose()
        await redis.aclose()
