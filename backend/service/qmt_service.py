"""QMT 核心代理服务。

封装所有与 QMT-Server 的交互：
- Redis-first 读取行情/账户缓存（无 HTTP 调用）
- MySQL + Redis 写入交易命令
- HTTP 转发调试/控制请求
"""

import gzip
import json
import math
import pickle
import time
import uuid
from datetime import date
from typing import Any

import httpx
import redis
from loguru import logger
from sqlalchemy import select
from sqlalchemy.dialects.mysql import insert as mysql_insert
from backend.config import (
    Settings,
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
        if config.QMT_MARKET_URL:
            self._market_http = httpx.AsyncClient(
                base_url=config.QMT_MARKET_URL,
                timeout=config.QMT_SERVER_TIMEOUT,
            )
        else:
            self._market_http = None
        # 行情 pickle 数据需要 decode_responses=False，单独创建连接
        self._tick_redis = redis.Redis.from_url(
            config.REDIS_URL,
            decode_responses=False,
            socket_timeout=3,
            socket_connect_timeout=5,
        )

    # ------------------------------------------------------------------
    # 旧行情 Redis 读取（qmt_tick:{date}:{code}）
    # ------------------------------------------------------------------

    async def get_snapshot(self, codes: list[str]) -> dict:
        """批量获取 Tick 快照，从旧行情 Redis 读取最新 tick。"""
        if not codes:
            return {}
        import asyncio as _aio
        today = date.today().strftime("%Y%m%d")
        # pipeline 批量读每个股票的最新一条 tick
        def _batch_read():
            pipe = self._tick_redis.pipeline(transaction=False)
            for code in codes:
                pipe.lrange(f"qmt_tick:{today}:{code}", -1, -1)
            return pipe.execute()

        results = await _aio.to_thread(_batch_read)
        out = {}
        for code, raw_list in zip(codes, results):
            if raw_list:
                try:
                    row = pickle.loads(raw_list[0])
                    out[code] = _old_tick_to_dict(code, row)
                except Exception:
                    pass
        return out

    async def get_tick(self, code: str) -> dict | None:
        """获取单只股票最新 Tick。"""
        import asyncio as _aio
        today = date.today().strftime("%Y%m%d")
        raw_list = await _aio.to_thread(
            self._tick_redis.lrange, f"qmt_tick:{today}:{code}", -1, -1
        )
        if not raw_list:
            return None
        try:
            row = pickle.loads(raw_list[0])
            return _old_tick_to_dict(code, row)
        except Exception:
            return None

    async def get_kline(self, code: str, period: str, count: int) -> list[dict]:
        """获取 K 线数据，代理到 qmt-market 服务。"""
        if not self._market_http:
            return []
        try:
            resp = await self._market_http.get(
                "/quote/kline",
                params={"code": code, "period": period, "count": count},
            )
            data = resp.json()
            if data.get("code") == 0:
                result = data.get("data") or []
                # 首次查询无数据（ETF等），触发下载后重试
                if not result:
                    try:
                        await self._market_http.post(
                            "/quote/history",
                            json={"code": code, "period": period, "count": count},
                        )
                        resp2 = await self._market_http.get(
                            "/quote/kline",
                            params={"code": code, "period": period, "count": count},
                        )
                        data2 = resp2.json()
                        if data2.get("code") == 0:
                            result = data2.get("data") or []
                    except httpx.HTTPError:
                        pass
                return result
            return []
        except httpx.HTTPError as e:
            logger.error("Kline proxy failed: {}", e)
            return []

    # ------------------------------------------------------------------
    # 股票名称查询
    # ------------------------------------------------------------------

    _stock_name_cache: dict[str, str] = {}
    _stock_name_cache_date: str = ""

    async def get_stock_names(self, codes: list[str]) -> dict[str, str]:
        """从 Redis 读取股票基础信息，返回 {code: name} 映射。"""
        import asyncio as _aio
        today = date.today().isoformat()

        # 每日刷新缓存
        if self._stock_name_cache_date != today:
            self._stock_name_cache = {}
            self._stock_name_cache_date = today

        if not self._stock_name_cache:
            def _load():
                raw = self._tick_redis.get("股票基础信息")
                if not raw:
                    return {}
                try:
                    data = pickle.loads(gzip.decompress(raw))
                    if isinstance(data, dict):
                        names_dict = data.get("股票名称")
                        if isinstance(names_dict, dict):
                            return names_dict  # {code: name} already
                except Exception as e:
                    logger.error("Failed to load stock names from Redis: {}", e)
                return {}

            cache = await _aio.to_thread(_load)
            self._stock_name_cache = cache

        if not codes:
            return {}
        return {c: self._stock_name_cache.get(c, "") for c in codes}

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
                overall = d.get("overall_status", "unknown")
                market_status = QmtServiceStatus(
                    source="market",
                    status=overall,
                    level="offline" if overall == "offline" else overall,
                    last_heartbeat=d.get("server_time"),
                )
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

        if trade_raw:
            try:
                d = json.loads(trade_raw) if isinstance(trade_raw, str) else trade_raw
                overall = d.get("overall_status", "unknown")
                trade_status = QmtServiceStatus(
                    source="trade",
                    status=overall,
                    level="offline" if overall == "offline" else overall,
                    last_heartbeat=d.get("server_time"),
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

    async def _redis_lpush(self, key: str, value: str) -> None:
        import asyncio
        await asyncio.to_thread(self._redis.lpush, key, value)

    async def _redis_get_json(self, key: str) -> Any:
        raw = await self._redis_get(key)
        if not raw:
            return None
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            return _sanitize_floats(data)
        except (json.JSONDecodeError, TypeError):
            return None

    # ------------------------------------------------------------------
    # 关闭
    # ------------------------------------------------------------------

    async def close(self) -> None:
        await self._http.aclose()
        if self._market_http:
            await self._market_http.aclose()
        self._tick_redis.close()


def _sanitize_floats(obj):
    """递归清理 JSON 数据中的 NaN / Inf 浮点数，替换为 None。"""
    if isinstance(obj, float):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return obj
    if isinstance(obj, dict):
        return {k: _sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_floats(v) for v in obj]
    return obj


def _old_tick_to_dict(code: str, row: list) -> dict:
    """将旧实现 pickle tick 数据转为 API 响应格式。

    row = [time, lastPrice, open, high, low, lastClose, amount, volume,
           askPrice[5], bidPrice[5], askVol[5], bidVol[5]]
    """
    last_price = row[1]
    last_close = row[5]
    volume = row[7]
    change = round(last_price - last_close, 4) if last_close else 0
    pct_change = round(change / last_close * 100, 4) if last_close else 0
    avg_price = round(row[6] / (volume * 100), 4) if volume else 0
    ask_p = row[8] if len(row) > 8 else [0] * 5
    bid_p = row[9] if len(row) > 9 else [0] * 5
    ask_v = row[10] if len(row) > 10 else [0] * 5
    bid_v = row[11] if len(row) > 11 else [0] * 5
    return {
        "code": code,
        "time": row[0],
        "open": row[2],
        "high": row[3],
        "low": row[4],
        "last": last_price,
        "close": last_close,
        "amount": row[6],
        "volume": volume,
        "ask1": ask_p[0], "ask2": ask_p[1], "ask3": ask_p[2], "ask4": ask_p[3], "ask5": ask_p[4],
        "bid1": bid_p[0], "bid2": bid_p[1], "bid3": bid_p[2], "bid4": bid_p[3], "bid5": bid_p[4],
        "ask_vol1": ask_v[0], "ask_vol2": ask_v[1], "ask_vol3": ask_v[2], "ask_vol4": ask_v[3], "ask_vol5": ask_v[4],
        "bid_vol1": bid_v[0], "bid_vol2": bid_v[1], "bid_vol3": bid_v[2], "bid_vol4": bid_v[3], "bid_vol5": bid_v[4],
        "change": change,
        "pct_change": pct_change,
        "avg_price": avg_price,
    }
