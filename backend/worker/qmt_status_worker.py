"""状态 Worker：Pub/Sub + 15s 轮询 → 广播 WebSocket。"""

import asyncio
import hashlib
import json
import threading

import redis
from loguru import logger

from backend.config import KEY_MARKET_STATUS, KEY_TRADE_STATUS, KEY_STATUS_NOTIFY
from backend.worker.trading_hours import is_trading_hours, seconds_until_trading_start


class QmtStatusWorker:
    """双路径状态监控：Pub/Sub 实时推送 + 15s 轮询兜底。"""

    def __init__(self, redis_client: redis.Redis, ws_broadcaster=None):
        self._redis = redis_client
        self._broadcaster = ws_broadcaster
        self._tasks: list[asyncio.Task] = []
        self._running = False
        self._last_hash = ""
        self._lock = threading.Lock()

    def start(self) -> list[asyncio.Task]:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="qmt-status-poll"),
            asyncio.create_task(self._pubsub_loop(), name="qmt-status-pubsub"),
        ]
        logger.info("QmtStatusWorker started")
        return self._tasks

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("QmtStatusWorker stopped")

    async def _pubsub_loop(self) -> None:
        """订阅 Redis Pub/Sub qmt:status:notify。"""
        while self._running:
            pubsub = self._redis.pubsub()
            try:
                pubsub.subscribe(KEY_STATUS_NOTIFY)
                logger.info("Subscribed to {}", KEY_STATUS_NOTIFY)

                while self._running:
                    # get_message 在独立线程中运行避免阻塞事件循环
                    msg = await asyncio.to_thread(
                        pubsub.get_message,
                        timeout=1.0,
                    )
                    if msg and msg["type"] == "message":
                        data = msg["data"]
                        if isinstance(data, bytes):
                            data = data.decode("utf-8")
                        await self._on_status_message(data)

            except asyncio.CancelledError:
                break
            except redis.ConnectionError:
                logger.error("Redis Pub/Sub connection lost, reconnecting in 5s...")
                await asyncio.sleep(5)
            except Exception:
                logger.exception("PubSub loop error")
                await asyncio.sleep(3)
            finally:
                try:
                    pubsub.unsubscribe()
                    pubsub.close()
                except Exception:
                    pass

    async def _poll_loop(self) -> None:
        """每 15s 轮询 qmt:market:status + qmt:trade:status。仅交易时间运行。"""
        while self._running:
            if not is_trading_hours():
                wait = seconds_until_trading_start()
                logger.debug("StatusWorker: outside trading hours, sleeping {:.0f}s", wait)
                await asyncio.sleep(min(wait, 60))
                continue

            try:
                await self._poll_status()
            except Exception:
                logger.exception("Poll status error")
            await asyncio.sleep(15)

    async def _poll_status(self) -> None:
        """轮询 Redis 状态键，MD5 去重。"""
        market_raw = await asyncio.to_thread(self._redis.get, KEY_MARKET_STATUS)
        trade_raw = await asyncio.to_thread(self._redis.get, KEY_TRADE_STATUS)

        market_data = None
        trade_data = None

        if market_raw:
            try:
                market_data = json.loads(market_raw) if isinstance(market_raw, str) else market_raw
            except (json.JSONDecodeError, TypeError):
                pass

        if trade_raw:
            try:
                trade_data = json.loads(trade_raw) if isinstance(trade_raw, str) else trade_raw
            except (json.JSONDecodeError, TypeError):
                pass

        combined = json.dumps({"market": market_data, "trade": trade_data}, sort_keys=True)
        current_hash = hashlib.md5(combined.encode()).hexdigest()

        with self._lock:
            if current_hash == self._last_hash:
                return
            self._last_hash = current_hash

        await self._broadcast({"market": market_data, "trade": trade_data})

    async def _on_status_message(self, data: str) -> None:
        """处理 Pub/Sub 消息，去重后广播。"""
        try:
            payload = json.loads(data) if isinstance(data, str) else data
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid status message: {}", data[:200])
            return

        # Pub/Sub 消息也需要去重
        combined = json.dumps(payload, sort_keys=True)
        current_hash = hashlib.md5(combined.encode()).hexdigest()

        with self._lock:
            if current_hash == self._last_hash:
                return
            self._last_hash = current_hash

        await self._broadcast(payload)

    async def _broadcast(self, status: dict) -> None:
        """广播状态到 WebSocket 客户端。"""
        if self._broadcaster:
            try:
                await self._broadcaster("qmt_status", status)
            except Exception:
                logger.exception("Status broadcast failed")
