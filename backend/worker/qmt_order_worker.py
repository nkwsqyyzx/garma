"""订单回报 Worker：Consumer Group 消费 qmt:event:order_update → 更新 MySQL。"""

import asyncio
import json

import redis
from loguru import logger

from backend.config import KEY_EVENT_ORDER_UPDATE
from backend.worker.trading_hours import is_trading_hours, seconds_until_trading_start

GROUP_NAME = "alpha-order-worker"
CONSUMER_NAME = "worker-1"


class QmtOrderWorker:
    """后台消费订单回报事件，更新 MySQL 订单表。"""

    def __init__(self, redis_client: redis.Redis, qmt_service=None):
        self._redis = redis_client
        self._service = qmt_service  # QmtService 实例
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> asyncio.Task:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="qmt-order-worker")
        logger.info("QmtOrderWorker started")
        return self._task

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("QmtOrderWorker stopped")

    def _ensure_group(self) -> None:
        """确保 Consumer Group 存在。"""
        try:
            self._redis.xgroup_create(
                name=KEY_EVENT_ORDER_UPDATE,
                groupname=GROUP_NAME,
                id="0",
                mkstream=True,
            )
            logger.info("Consumer group '{}' created", GROUP_NAME)
        except redis.ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug("Consumer group '{}' already exists", GROUP_NAME)
            else:
                raise

    async def _run(self) -> None:
        await asyncio.to_thread(self._ensure_group)
        while self._running:
            # 非交易时间挂起
            if not is_trading_hours():
                wait = seconds_until_trading_start()
                logger.debug("OrderWorker: outside trading hours, sleeping {:.0f}s", wait)
                await asyncio.sleep(min(wait, 60))
                continue

            try:
                # XREADGROUP 阻塞 5000ms
                results = await asyncio.to_thread(
                    self._redis.xreadgroup,
                    groupname=GROUP_NAME,
                    consumername=CONSUMER_NAME,
                    streams={KEY_EVENT_ORDER_UPDATE: ">"},
                    count=10,
                    block=5000,
                )
                if not results:
                    continue

                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        await self._handle_message(msg_id, fields)

            except asyncio.CancelledError:
                break
            except redis.ConnectionError:
                logger.error("Redis connection lost, reconnecting in 3s...")
                await asyncio.sleep(3)
            except Exception:
                logger.exception("QmtOrderWorker error")
                await asyncio.sleep(1)

    async def _handle_message(self, msg_id: str, fields: dict) -> None:
        """处理单条订单事件消息。"""
        try:
            # fields 中 data 字段包含 JSON
            raw = fields.get("data") or fields.get("event")
            if not raw:
                logger.warning("Empty event data in msg {}", msg_id)
                await self._ack(msg_id)
                return

            if isinstance(raw, str):
                event = json.loads(raw)
            else:
                event = raw

            req_id = event.get("req_id")
            status = event.get("status", "")

            if not req_id:
                logger.warning("Event missing req_id: {}", event)
                await self._ack(msg_id)
                return

            # 更新 MySQL
            if self._service:
                await self._service.update_order_from_event(event)

            # 成交/失败通知（预留企业微信接口）
            if status in ("filled", "rejected"):
                await self._send_notification(event)

            # 处理成功 → ACK
            await self._ack(msg_id)
            logger.debug("Order event processed: req_id={} status={}", req_id, status)

        except json.JSONDecodeError:
            logger.error("Invalid JSON in order event msg {}", msg_id)
            await self._ack(msg_id)  # 无效数据直接 ACK，避免重复消费
        except Exception:
            logger.exception("Failed to handle order event msg {}", msg_id)
            # 不 ACK，下次重投递

    async def _ack(self, msg_id: str) -> None:
        """确认消息已处理。"""
        await asyncio.to_thread(
            self._redis.xack,
            KEY_EVENT_ORDER_UPDATE,
            GROUP_NAME,
            msg_id,
        )

    async def _send_notification(self, event: dict) -> None:
        """预留企业微信通知接口。"""
        # TODO: 接入企业微信 Webhook
        status = event.get("status")
        stock_code = event.get("stock_code", "?")
        logger.info("Notification: {} {} status={}", stock_code, event.get("order_type"), status)
