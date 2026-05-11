"""QMT 核心代理服务。

封装所有与 QMT-Server 的交互：
- Redis-first 读取行情/账户缓存（无 HTTP 调用）
- MySQL + Redis 写入交易命令
- HTTP 转发调试/控制请求
"""

import json
import time
import uuid
from typing import Any

import httpx
import redis
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import (
    Settings,
    KEY_SNAPSHOT_TICK,
    KEY_SNAPSHOT_KLINE,
    KEY_ACCOUNT_ASSET,
    KEY_ACCOUNT_POSITIONS,
    KEY_ACCOUNT_ORDERS,
    KEY_ACCOUNT_TRADES,
    KEY_MARKET_STATUS,
    KEY_TRADE_STATUS,
    KEY_CMD_QUEUE,
    KEY_ORDER_STATUS,
    KEY_KILL_SWITCH,
)
from backend.models.qmt_order import QmtOrder
from backend.schemas.qmt import (
    QmtHealthResponse,
    QmtServiceStatus,
)


# HTTP 透传路径黑名单（禁止 Alpha 端直接调用）
_PROXY_BLOCKED_PREFIXES = (
    "/api/v1/trade/order",
    "/api/v1/trade/cancel",
    "/api/v1/control/",
)


class QmtService:
    """QMT 核心代理服务。"""

    def __init__(self, config: Settings, redis_client: redis.Redis, db_session_factory):
        self._config = config
        self._redis = redis_client
        self._db_session_factory = db_session_factory
        self._http = httpx.AsyncClient(
            base_url=config.QMT_SERVER_URL,
            timeout=config.QMT_SERVER_TIMEOUT,
            headers={"X-API-Key": config.QMT_SERVER_API_KEY},
        )

    # ------------------------------------------------------------------
    # Redis-first 读取（行情）
    # ------------------------------------------------------------------

    async def get_snapshot(self, codes: list[str]) -> dict:
        """批量获取 Tick 快照，HMGET qmt:snapshot:tick。"""
        if not codes:
            return {}
        data = await self._redis_hgetall(KEY_SNAPSHOT_TICK)
        result = {}
        for code in codes:
            raw = data.get(code)
            if raw:
                try:
                    result[code] = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, TypeError):
                    pass
        return result

    async def get_tick(self, code: str) -> dict | None:
        """获取单只股票最新 Tick。"""
        raw = await self._redis_hget(KEY_SNAPSHOT_TICK, code)
        if not raw:
            return None
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return None

    async def get_kline(self, code: str, period: str, count: int) -> list[dict]:
        """获取 K 线数据。"""
        key = KEY_SNAPSHOT_KLINE.format(period=period)
        raw = await self._redis_hget(key, code)
        if not raw:
            return []
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, list):
                return data[-count:]
            return [data]
        except (json.JSONDecodeError, TypeError):
            return []

    # ------------------------------------------------------------------
    # Redis-first 读取（账户）
    # ------------------------------------------------------------------

    async def get_asset(self) -> dict | None:
        """读取账户资金快照。"""
        return await self._redis_get_json(KEY_ACCOUNT_ASSET)

    async def get_positions(self) -> list[dict]:
        """读取持仓列表。"""
        data = await self._redis_get_json(KEY_ACCOUNT_POSITIONS)
        if isinstance(data, list):
            return data
        return []

    async def get_orders(self, cancelable_only: bool = False) -> list[dict]:
        """读取当日委托列表。"""
        data = await self._redis_get_json(KEY_ACCOUNT_ORDERS)
        if not isinstance(data, list):
            return []
        if cancelable_only:
            cancelable = {"submitted", "reported", "partial"}
            return [o for o in data if o.get("status", "") in cancelable]
        return data

    async def get_trades(self) -> list[dict]:
        """读取当日成交列表。"""
        data = await self._redis_get_json(KEY_ACCOUNT_TRADES)
        if isinstance(data, list):
            return data
        return []

    # ------------------------------------------------------------------
    # MySQL + Redis 写入（交易）
    # ------------------------------------------------------------------

    async def place_order(self, request) -> str:
        """下单：1) INSERT qmt_orders DRAFT  2) RPUSH 命令队列。"""
        req_id = f"alpha_{uuid.uuid4().hex[:16]}_{int(time.time())}"

        async with self._db_session_factory() as session:
            order = QmtOrder(
                req_id=req_id,
                account_id=self._config.QMT_ACCOUNT_ID,
                stock_code=request.stock_code,
                order_type=request.order_type,
                order_volume=request.order_volume,
                price_type=request.price_type,
                price=request.price,
                strategy_name=request.strategy_name,
                order_remark=request.order_remark,
                status="DRAFT",
            )
            session.add(order)
            await session.commit()

        cmd = {
            "req_id": req_id,
            "cmd": "place_order",
            "account_id": self._config.QMT_ACCOUNT_ID,
            "stock_code": request.stock_code,
            "order_type": request.order_type,
            "order_volume": request.order_volume,
            "price_type": request.price_type,
            "price": request.price,
            "strategy_name": request.strategy_name or "",
            "order_remark": request.order_remark or "",
            "retry_count": 0,
            "created_at": time.time(),
        }
        await self._redis_lpush(KEY_CMD_QUEUE, json.dumps(cmd))
        logger.info("Place order: req_id={} code={} type={} vol={} price={}",
                     req_id, request.stock_code, request.order_type,
                     request.order_volume, request.price)
        return req_id

    async def cancel_order(self, order_id: str) -> str:
        """撤单：构造 cancel_order 命令 → RPUSH 队列。"""
        req_id = f"alpha_cancel_{uuid.uuid4().hex[:12]}_{int(time.time())}"
        cmd = {
            "req_id": req_id,
            "cmd": "cancel_order",
            "account_id": self._config.QMT_ACCOUNT_ID,
            "order_id": order_id,
        }
        await self._redis_lpush(KEY_CMD_QUEUE, json.dumps(cmd))
        logger.info("Cancel order: req_id={} order_id={}", req_id, order_id)
        return req_id

    async def cancel_all(self) -> str:
        """全撤：构造 cancel_all 命令 → RPUSH 队列。"""
        req_id = f"alpha_cancelall_{uuid.uuid4().hex[:12]}_{int(time.time())}"
        cmd = {
            "req_id": req_id,
            "cmd": "cancel_all",
            "account_id": self._config.QMT_ACCOUNT_ID,
        }
        await self._redis_lpush(KEY_CMD_QUEUE, json.dumps(cmd))
        logger.info("Cancel all: req_id={}", req_id)
        return req_id

    async def get_order_status(self, req_id: str) -> dict | None:
        """查询订单状态（MySQL）。"""
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(QmtOrder).where(QmtOrder.req_id == req_id)
            )
            order = result.scalar_one_or_none()
            if not order:
                return None
            return {
                "req_id": order.req_id,
                "order_id": order.order_id,
                "stock_code": order.stock_code,
                "stock_name": order.stock_name,
                "order_type": order.order_type,
                "order_volume": order.order_volume,
                "traded_volume": order.traded_volume,
                "price_type": order.price_type,
                "price": float(order.price) if order.price else 0,
                "traded_price": float(order.traded_price) if order.traded_price else None,
                "status": order.status,
                "status_msg": order.status_msg,
                "strategy_name": order.strategy_name,
                "order_remark": order.order_remark,
                "retry_count": order.retry_count,
                "created_at": order.created_at.isoformat() if order.created_at else None,
                "updated_at": order.updated_at.isoformat() if order.updated_at else None,
            }

    # ------------------------------------------------------------------
    # HTTP 转发（调试/控制）
    # ------------------------------------------------------------------

    async def proxy_request(self, method: str, path: str,
                            params: dict | None = None,
                            body: dict | None = None) -> dict:
        """路径白名单校验后转发请求到 QMT-Server。"""
        # 黑名单校验
        for prefix in _PROXY_BLOCKED_PREFIXES:
            if path.startswith(prefix):
                return {"code": 403, "msg": f"Path '{path}' is not allowed for proxy"}

        url = path
        try:
            resp = await self._http.request(
                method=method.upper(),
                url=url,
                params=params,
                json=body,
            )
            return resp.json()
        except httpx.HTTPError as e:
            logger.error("Proxy request failed: {} {} → {}", method, path, e)
            return {"code": 502, "msg": f"QMT-Server request failed: {e}"}

    async def forward_to_market(self, method: str, path: str,
                                params: dict | None = None,
                                body: dict | None = None) -> dict:
        """转发请求到 QMT 行情服务 (:8091)。"""
        market_url = self._config.QMT_SERVER_URL.replace(":8090", ":8091")
        try:
            async with httpx.AsyncClient(
                base_url=market_url,
                timeout=self._config.QMT_SERVER_TIMEOUT,
                headers={"X-API-Key": self._config.QMT_SERVER_API_KEY},
            ) as client:
                resp = await client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=body,
                )
                return resp.json()
        except httpx.HTTPError as e:
            logger.error("Forward to market failed: {} {} → {}", method, path, e)
            return {"code": 502, "msg": f"QMT-Market request failed: {e}"}

    # ------------------------------------------------------------------
    # 状态检查
    # ------------------------------------------------------------------

    async def health(self) -> QmtHealthResponse:
        """读取 Redis 双键状态合并。"""
        market_raw = await self._redis_get(KEY_MARKET_STATUS)
        trade_raw = await self._redis_get(KEY_TRADE_STATUS)

        market_status = None
        trade_status = None

        if market_raw:
            try:
                d = json.loads(market_raw) if isinstance(market_raw, str) else market_raw
                market_status = QmtServiceStatus(
                    source="market",
                    status=d.get("status", "unknown"),
                    level=d.get("level", "offline"),
                    last_heartbeat=d.get("last_heartbeat"),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if trade_raw:
            try:
                d = json.loads(trade_raw) if isinstance(trade_raw, str) else trade_raw
                trade_status = QmtServiceStatus(
                    source="trade",
                    status=d.get("status", "unknown"),
                    level=d.get("level", "offline"),
                    last_heartbeat=d.get("last_heartbeat"),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        online = False
        if market_status and market_status.level != "offline":
            online = True
        if trade_status and trade_status.level != "offline":
            online = True

        return QmtHealthResponse(market=market_status, trade=trade_status, online=online)

    async def is_online(self) -> bool:
        """检查 TTL 判断服务是否在线。"""
        result = await self.health()
        return result.online

    # ------------------------------------------------------------------
    # 熔断
    # ------------------------------------------------------------------

    async def get_kill_switch(self) -> bool:
        raw = await self._redis_get(KEY_KILL_SWITCH)
        return raw == "1"

    async def set_kill_switch(self, active: bool) -> None:
        if active:
            await self._redis_set(KEY_KILL_SWITCH, "1")
        else:
            await self._redis_delete(KEY_KILL_SWITCH)
        logger.warning("Kill switch {}", "ACTIVATED" if active else "DEACTIVATED")

    # ------------------------------------------------------------------
    # 订单状态更新（供 Worker 调用）
    # ------------------------------------------------------------------

    async def update_order_from_event(self, event: dict) -> None:
        """根据订单事件更新 MySQL，幂等（ON DUPLICATE KEY UPDATE）。"""
        req_id = event.get("req_id")
        if not req_id:
            logger.warning("Order event missing req_id: {}", event)
            return

        status = event.get("status", "")
        update_data: dict[str, Any] = {"status": status}

        if event.get("order_id"):
            update_data["order_id"] = event["order_id"]
        if event.get("stock_name"):
            update_data["stock_name"] = event["stock_name"]
        if event.get("status_msg"):
            update_data["status_msg"] = event["status_msg"]

        if status in ("partial", "filled"):
            if event.get("traded_volume") is not None:
                update_data["traded_volume"] = event["traded_volume"]
            if event.get("traded_price") is not None:
                update_data["traded_price"] = event["traded_price"]

        async with self._db_session_factory() as session:
            stmt = mysql_insert(QmtOrder).values(req_id=req_id, **update_data)
            stmt = stmt.on_duplicate_key_update(**{
                k: stmt.inserted[k] for k in update_data
            })
            await session.execute(stmt)
            await session.commit()

        logger.info("Order updated: req_id={} status={}", req_id, status)

    # ------------------------------------------------------------------
    # Redis 异步包装（asyncio.to_thread 包装同步 redis 调用）
    # ------------------------------------------------------------------

    async def _redis_get(self, key: str) -> str | None:
        import asyncio
        return await asyncio.to_thread(self._redis.get, key)

    async def _redis_set(self, key: str, value: str) -> None:
        import asyncio
        await asyncio.to_thread(self._redis.set, key, value)

    async def _redis_delete(self, key: str) -> None:
        import asyncio
        await asyncio.to_thread(self._redis.delete, key)

    async def _redis_hget(self, key: str, field: str) -> str | None:
        import asyncio
        return await asyncio.to_thread(self._redis.hget, key, field)

    async def _redis_hgetall(self, key: str) -> dict:
        import asyncio
        return await asyncio.to_thread(self._redis.hgetall, key)

    async def _redis_lpush(self, key: str, value: str) -> None:
        import asyncio
        await asyncio.to_thread(self._redis.lpush, key, value)

    async def _redis_get_json(self, key: str) -> Any:
        raw = await self._redis_get(key)
        if not raw:
            return None
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()
