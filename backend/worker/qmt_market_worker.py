"""行情 Worker：消费 qmt:stream:agg → 广播 WebSocket。"""

import asyncio
import json

import redis
from loguru import logger

from backend.config import KEY_STREAM_AGG


class QmtMarketWorker:
    """后台消费聚合行情流，广播到前端 WebSocket。"""

    def __init__(self, redis_client: redis.Redis, ws_broadcaster=None):
        self._redis = redis_client
        self._broadcaster = ws_broadcaster  # WebSocket 广播回调
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> asyncio.Task:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="qmt-market-worker")
        logger.info("QmtMarketWorker started")
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("QmtMarketWorker stopped")

    async def _run(self) -> None:
        last_id = "0"
        while self._running:
            try:
                # XREAD 阻塞 100ms
                results = await asyncio.to_thread(
                    self._redis.xread,
                    {KEY_STREAM_AGG: last_id},
                    count=50,
                    block=100,
                )
                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        last_id = msg_id
                        tick = fields.get("data") or fields.get("tick")
                        if not tick:
                            continue
                        if isinstance(tick, str):
                            try:
                                tick = json.loads(tick)
                            except json.JSONDecodeError:
                                pass
                        await self._broadcast_ws(tick)

            except asyncio.CancelledError:
                break
            except redis.ConnectionError:
                logger.error("Redis connection lost, reconnecting in 3s...")
                await asyncio.sleep(3)
            except Exception:
                logger.exception("QmtMarketWorker error")
                await asyncio.sleep(1)

    async def _broadcast_ws(self, tick) -> None:
        """广播 tick 到 WebSocket 客户端。"""
        if self._broadcaster:
            try:
                await self._broadcaster("qmt_tick", tick)
            except Exception:
                logger.exception("WebSocket broadcast failed")
