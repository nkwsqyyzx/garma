"""数据推送 Worker：监听 Redis 账户数据变化 → WebSocket 推送到前端。"""

import asyncio
import json

import redis
from loguru import logger

from backend.config import (
    KEY_ACCOUNT_ASSET,
    KEY_ACCOUNT_POSITIONS,
    KEY_ACCOUNT_ORDERS,
    KEY_ACCOUNT_TRADES,
)
from backend.worker.trading_hours import is_trading_hours, seconds_until_trading_start


class QmtDataPushWorker:
    """监听账户数据变化并推送到 /ws/qmt-data 的 WebSocket 客户端。"""

    # 需要监听的 Redis key → 推送类型映射
    _WATCH_KEYS = {
        KEY_ACCOUNT_ASSET: "asset",
        KEY_ACCOUNT_POSITIONS: "positions",
        KEY_ACCOUNT_ORDERS: "orders",
        KEY_ACCOUNT_TRADES: "trades",
    }

    def __init__(self, redis_client: redis.Redis, qmt_service):
        self._redis = redis_client
        self._svc = qmt_service
        self._connections: list[asyncio.Queue] = []
        self._subscriptions: dict[int, set[str]] = {}  # queue_id -> subscribed types
        self._running = False
        self._tasks: list[asyncio.Task] = []

    def start(self) -> list[asyncio.Task]:
        self._running = True
        self._tasks = [
            asyncio.create_task(self._poll_loop(), name="qmt-data-poll"),
        ]
        logger.info("QmtDataPushWorker started")
        return self._tasks

    def add_connection(self, queue: asyncio.Queue, subscriptions: set[str] | None = None):
        """注册一个新的 WebSocket 连接的消息队列。"""
        self._connections.append(queue)
        self._subscriptions[id(queue)] = subscriptions or {"asset", "positions", "orders", "trades"}

    def remove_connection(self, queue: asyncio.Queue):
        """移除一个 WebSocket 连接。"""
        if queue in self._connections:
            self._connections.remove(queue)
        self._subscriptions.pop(id(queue), None)

    def update_subscriptions(self, queue: asyncio.Queue, types: set[str]):
        """更新某个连接的订阅类型。"""
        self._subscriptions[id(queue)] = types

    async def send_initial_snapshot(self, queue: asyncio.Queue, subscriptions: set[str] | None = None):
        """连接建立后推送一次全量快照。"""
        subs = subscriptions or {"asset", "positions", "orders", "trades"}
        try:
            if "asset" in subs:
                data = await self._svc.get_asset()
                await queue.put({"type": "asset", "data": data})
            if "positions" in subs:
                data = await self._svc.get_positions()
                await queue.put({"type": "positions", "data": data})
            if "orders" in subs:
                data = await self._svc.get_orders()
                await queue.put({"type": "orders", "data": data})
            if "trades" in subs:
                data = await self._svc.get_trades()
                await queue.put({"type": "trades", "data": data})
        except Exception:
            logger.exception("Failed to send initial snapshot")

    async def _poll_loop(self) -> None:
        """每 2 秒轮询 Redis key，检测变化后推送。仅交易时间运行。"""
        last_values: dict[str, str] = {}

        while self._running:
            if not is_trading_hours():
                wait = seconds_until_trading_start()
                logger.debug("DataPush: outside trading hours, sleeping {:.0f}s", wait)
                await asyncio.sleep(min(wait, 60))
                continue

            try:
                for key, msg_type in self._WATCH_KEYS.items():
                    raw = await asyncio.to_thread(self._redis.get, key)
                    raw_str = raw if isinstance(raw, str) else (raw.decode() if raw else "")

                    if raw_str != last_values.get(key):
                        last_values[key] = raw_str
                        if raw_str:
                            try:
                                data = json.loads(raw_str)
                            except (json.JSONDecodeError, TypeError):
                                data = None
                            await self._broadcast(msg_type, data)

            except Exception:
                logger.exception("Data push poll error")

            await asyncio.sleep(2)

    async def _broadcast(self, msg_type: str, data) -> None:
        """推送到所有订阅了该类型的连接。"""
        disconnected = []
        for queue in self._connections:
            subs = self._subscriptions.get(id(queue), set())
            if msg_type not in subs:
                continue
            try:
                queue.put_nowait({"type": msg_type, "data": data})
            except asyncio.QueueFull:
                logger.warning("Queue full for connection, dropping message")
            except Exception:
                disconnected.append(queue)

        for q in disconnected:
            self.remove_connection(q)
