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
from sqlalchemy import select, text, update
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
from backend.models.strategy_trade import StrategyTrade
from backend.schemas.qmt import (
    QmtHealthResponse,
    QmtServiceStatus,
)


def _parse_remark(remark: str | None) -> tuple[str | None, str | None, str | None]:
    """解析 order_remark 为 (strategy, factor, remark)。

    格式: 策略名:因子:序号:股票名  或  策略名$子策略:因子:序号:股票名
    如果不匹配冒号分隔格式，返回 (None, None, 原始值)。
    """
    if not remark:
        return None, None, None
    parts = remark.split(":")
    if len(parts) >= 4:
        return parts[0], parts[1], remark
    return None, None, remark


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

    def _find_latest_tick_date(self) -> str | None:
        """查找最近有 tick 数据的交易日（最多回溯 7 天）。"""
        from datetime import timedelta
        today = date.today()
        for i in range(7):
            d = today - timedelta(days=i)
            d_str = d.strftime("%Y%m%d")
            # 用第一只股票的 key 探测即可，或者直接 keys 数量
            if self._tick_redis.exists(f"qmt_tick:{d_str}:*"):
                return d_str
            # exists 不支持通配符，改用 scan
            _, keys = self._tick_redis.scan(
                cursor=0, match=f"qmt_tick:{d_str}:*", count=1
            )
            if keys:
                return d_str
        return None

    async def get_snapshot(self, codes: list[str]) -> dict:
        """批量获取 Tick 快照，从旧行情 Redis 读取最新 tick。"""
        if not codes:
            return {}
        import asyncio as _aio

        # 今天没数据时往前找最近的交易日
        tick_date = await _aio.to_thread(self._find_latest_tick_date)
        if not tick_date:
            return {}

        # pipeline 批量读每个股票的最新一条 tick
        def _batch_read():
            pipe = self._tick_redis.pipeline(transaction=False)
            for code in codes:
                pipe.lrange(f"qmt_tick:{tick_date}:{code}", -1, -1)
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
        tick_date = await _aio.to_thread(self._find_latest_tick_date)
        if not tick_date:
            return None
        raw_list = await _aio.to_thread(
            self._tick_redis.lrange, f"qmt_tick:{tick_date}:{code}", -1, -1
        )
        if not raw_list:
            return None
        try:
            row = pickle.loads(raw_list[0])
            return _old_tick_to_dict(code, row)
        except Exception:
            return None

    async def get_kline(self, code: str, period: str, count: int) -> list[dict]:
        """获取 K 线数据，Redis 缓存 + 代理到 qmt-market 服务，日K追加当日 tick。"""
        cache_key = f"qmt:kline:{code}:{period}:{count}"

        # 1) 尝试从 Redis 缓存读取
        import asyncio as _aio
        cached = await _aio.to_thread(self._redis.get, cache_key)
        result = None
        if cached:
            try:
                result = json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                pass

        # 2) 缓存未命中，请求 qmt-market
        if not result:
            result = await self._fetch_kline(code, period, count)
            if not result:
                result = []
            # 写入缓存，过期时间到次日 09:15
            if result:
                ttl = self._seconds_until_next_0915()
                await _aio.to_thread(
                    self._redis.setex, cache_key, ttl,
                    json.dumps(result, ensure_ascii=False, default=str),
                )
                logger.debug("Kline cached: key={} ttl={}s", cache_key, ttl)

        # 3) 日K线：追加当日 tick 数据作为最新一根 K 线
        if period == "1d":
            today_bar = await self._today_tick_to_kline(code)
            if today_bar:
                today_str = date.today().strftime("%Y-%m-%d")
                today_compact = date.today().strftime("%Y%m%d")
                _appended = False
                if result:
                    last_date = (
                        result[-1].get("time")
                        or result[-1].get("date")
                        or result[-1].get("datetime")
                        or ""
                    )
                    if last_date in (today_str, today_compact) or last_date.startswith(today_str):
                        result[-1] = today_bar
                        _appended = True
                if not _appended:
                    result.append(today_bar)

        return result

    async def _today_tick_to_kline(self, code: str) -> dict | None:
        """从 Redis tick 数据构造今日 K 线 bar（取最新一条 tick，其 OHLCV 已是日累计值）。"""
        import asyncio as _aio
        today = date.today().strftime("%Y%m%d")
        try:
            raw_list = await _aio.to_thread(
                self._tick_redis.lrange, f"qmt_tick:{today}:{code}", -1, -1
            )
            if not raw_list:
                return None
            row = pickle.loads(raw_list[0])
            # row = [time, lastPrice, open, high, low, lastClose, amount, volume, ...]
            if len(row) < 8:
                return None
            return {
                "time": date.today().strftime("%Y-%m-%d"),
                "open": row[2],
                "close": row[1],
                "high": row[3],
                "low": row[4],
                "volume": row[7],
                "amount": row[6],
            }
        except Exception as e:
            logger.debug("Failed to build today kline from tick for {}: {}", code, e)
            return None

    def _seconds_until_next_0915(self) -> int:
        """计算到下一个交易日 09:15 的秒数。"""
        from datetime import datetime, time, timedelta
        now = datetime.now()
        target = datetime.combine(now.date(), time(9, 15))
        if now >= target:
            target = datetime.combine(now.date() + timedelta(days=1), time(9, 15))
        delta = int((target - now).total_seconds())
        return max(delta, 60)  # 至少 60 秒

    async def _fetch_kline(self, code: str, period: str, count: int) -> list[dict]:
        """实际请求 qmt-market 获取 K 线。"""
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
        """读取持仓列表。Redis 无数据时从 daily_positions 表兜底。"""
        data = await self._redis_get_json(KEY_ACCOUNT_POSITIONS)
        if isinstance(data, list) and data:
            return data
        # fallback: 从 daily_positions 读取最近一个交易日的快照
        return await self._get_positions_from_db()

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

    async def _get_positions_from_db(self) -> list[dict]:
        """从 daily_positions 表读取最近一个交易日的持仓快照，格式兼容 Redis 返回。"""
        from sqlalchemy import select, func, desc
        from backend.models.daily_position import DailyPosition

        async with self._db_session_factory() as session:
            # 取最近的 snapshot_date
            date_result = await session.execute(
                select(func.max(DailyPosition.snapshot_date))
                .where(DailyPosition.account_id == self._config.QMT_ACCOUNT_ID)
            )
            latest_date = date_result.scalar()
            if not latest_date:
                return []

            result = await session.execute(
                select(DailyPosition).where(
                    DailyPosition.snapshot_date == latest_date,
                    DailyPosition.account_id == self._config.QMT_ACCOUNT_ID,
                )
            )
            positions = result.scalars().all()

        if not positions:
            return []

        # 获取最新行情用于填充市值/盈亏
        codes = list({p.stock_code for p in positions})
        snapshot = await self.get_snapshot(codes)

        out = []
        for p in positions:
            tick = snapshot.get(p.stock_code, {})
            current_price = tick.get("last", float(p.avg_price))
            market_value = round(current_price * p.volume, 2)
            profit_loss = round((current_price - float(p.avg_price)) * p.volume, 2)
            profit_loss_ratio = round(
                (current_price / float(p.avg_price) - 1) * 100, 2
            ) if float(p.avg_price) else 0
            out.append({
                "stock_code": p.stock_code,
                "stock_name": p.stock_name or "",
                "volume": p.volume,
                "can_use_volume": p.volume,
                "market_value": market_value,
                "profit_loss": profit_loss,
                "profit_loss_ratio": profit_loss_ratio,
                "open_price": float(p.avg_price),
            })
        return out

    # ------------------------------------------------------------------
    # MySQL + Redis 写入（交易）
    # ------------------------------------------------------------------

    async def place_order(self, request) -> str:
        """下单：1) INSERT qmt_orders DRAFT  2) RPUSH 命令队列。"""
        req_id = f"alpha_{uuid.uuid4().hex[:16]}_{int(time.time())}"

        # 卖出且带有 linked_req_id 时，从 strategy_trades 继承策略信息
        strategy_name = request.strategy_name
        order_remark = request.order_remark
        stock_name = None

        if (not strategy_name or not order_remark) and request.linked_req_id:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(
                        StrategyTrade.strategy,
                        StrategyTrade.factor,
                        StrategyTrade.remark,
                        StrategyTrade.stock_name,
                    )
                    .where(StrategyTrade.order_req_id == request.linked_req_id)
                    .limit(1)
                )
                row = result.first()
                if row:
                    if not strategy_name and row[0]:
                        strategy_name = row[0]
                    if row[1] or row[2]:
                        # 从继承的策略信息重建 order_remark
                        factor = row[1] or "-"
                        remark_base = row[2] or ""
                        if not order_remark:
                            # 尝试从 remark 中提取名称部分
                            name = request.stock_code
                            if remark_base and ":" in remark_base:
                                parts = remark_base.split(":")
                                if len(parts) >= 4:
                                    name = parts[3]
                            order_remark = f"{strategy_name or '-'}:{factor}:0:{name}"
                    if row[3]:
                        stock_name = row[3]

        # stock_name 兜底：从 Redis 缓存获取
        if not stock_name:
            names = await self.get_stock_names([request.stock_code])
            stock_name = names.get(request.stock_code) or None

        async with self._db_session_factory() as session:
            order = QmtOrder(
                req_id=req_id,
                account_id=self._config.QMT_ACCOUNT_ID,
                stock_code=request.stock_code,
                stock_name=stock_name,
                order_type=request.order_type,
                order_volume=request.order_volume,
                price_type=request.price_type,
                price=request.price,
                strategy_name=strategy_name,
                order_remark=order_remark,
                linked_req_id=request.linked_req_id,
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
            "linked_req_id": request.linked_req_id or "",
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
                    tick_delay=d.get("tick_delay_seconds"),
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
        """根据订单事件更新 MySQL（纯 UPDATE，行不存在则跳过）。"""
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

        status_lower = status.lower()
        if status_lower in ("partial", "filled"):
            if event.get("traded_volume") is not None:
                update_data["traded_volume"] = event["traded_volume"]
            if event.get("traded_price") is not None:
                update_data["traded_price"] = event["traded_price"]

        async with self._db_session_factory() as session:
            stmt = (
                update(QmtOrder)
                .where(QmtOrder.req_id == req_id)
                .values(**update_data)
            )
            result = await session.execute(stmt)
            await session.commit()

        if result.rowcount > 0:
            logger.info("Order updated: req_id={} status={}", req_id, status)
        else:
            logger.debug("Order not found, skip update: req_id={} status={}", req_id, status)

    async def record_strategy_trade(self, event: dict) -> None:
        """订单成交时写入 strategy_trades 流水记录。"""
        req_id = event.get("req_id")
        status = event.get("status", "")
        if not req_id:
            return
        # 仅在成交时记录（兼容大小写）
        if status.lower() not in ("partial", "filled"):
            return
        traded_volume = event.get("traded_volume")
        traded_price = event.get("traded_price")
        if not traded_volume or traded_volume <= 0:
            return

        # 查 qmt_orders 获取完整的 strategy_name / order_remark
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(QmtOrder).where(QmtOrder.req_id == req_id)
            )
            order = result.scalar_one_or_none()

        if not order:
            logger.warning("record_strategy_trade: order not found req_id={}", req_id)
            return

        strategy, factor, remark = _parse_remark(order.order_remark)
        # 如果 remark 解析不出 strategy，fallback 到 order.strategy_name
        if not strategy and order.strategy_name:
            strategy = order.strategy_name
        # 卖出订单仍无 strategy：从 linked buy 的 strategy_trades 继承
        if (not strategy or not factor) and getattr(order, "linked_req_id", None):
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(StrategyTrade.strategy, StrategyTrade.factor, StrategyTrade.remark)
                    .where(StrategyTrade.order_req_id == order.linked_req_id)
                    .limit(1)
                )
                row = result.first()
                if row:
                    if not strategy:
                        strategy = row[0]
                    if not factor:
                        factor = row[1]
                    if not remark:
                        remark = row[2]

        volume = int(traded_volume)
        price = float(traded_price or 0)
        direction = "buy" if order.order_type == "buy" else "sell"

        async with self._db_session_factory() as session:
            trade = StrategyTrade(
                account_id=order.account_id,
                stock_code=order.stock_code,
                stock_name=order.stock_name,
                direction=direction,
                volume=volume,
                price=price,
                amount=round(volume * price, 4),
                strategy=strategy,
                factor=factor,
                remark=remark,
                trade_date=date.today(),
                source="order",
                order_req_id=req_id,
                linked_req_id=getattr(order, "linked_req_id", None),
            )
            session.add(trade)
            await session.commit()

        logger.info("Strategy trade recorded: req_id={} {} {} vol={} price={}",
                     req_id, direction, order.stock_code, volume, price)

    async def reconcile_strategy_trades(self) -> int:
        """从 Redis qmt:account:trades 对账，补录缺失的 strategy_trades。

        匹配逻辑：通过 stock_code + order_type + price 近似匹配 Redis 成交记录，
        对于已有成交但 strategy_trades 中缺失的订单，补录流水。

        Returns: 补录条数
        """
        # 1. 从 Redis 读取今日成交
        trades_raw = await self._redis_get_json(KEY_ACCOUNT_TRADES)
        if not isinstance(trades_raw, list) or not trades_raw:
            return 0

        # 2. 查所有今日的 qmt_orders（需要补录的候选）
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(QmtOrder).where(
                    QmtOrder.created_at >= date.today(),
                )
            )
            orders = result.scalars().all()

        if not orders:
            return 0

        # 2b. 对于卖出订单（没有 order_remark），预加载 linked buy order 的信息
        linked_buy_map: dict[str, QmtOrder] = {}
        sell_req_ids = [o.req_id for o in orders if o.order_type == "sell" and o.linked_req_id]
        if sell_req_ids:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(QmtOrder).where(QmtOrder.req_id.in_(
                        {o.linked_req_id for o in orders if o.linked_req_id}
                    ))
                )
                for lo in result.scalars().all():
                    linked_buy_map[lo.req_id] = lo

        # 2c. 查 strategy_trades 中 linked buy order 的 strategy/factor
        linked_buy_st_map: dict[str, dict] = {}
        linked_req_ids = {o.linked_req_id for o in orders if o.linked_req_id}
        if linked_req_ids:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(
                        StrategyTrade.order_req_id,
                        StrategyTrade.strategy,
                        StrategyTrade.factor,
                        StrategyTrade.remark,
                    ).where(StrategyTrade.order_req_id.in_(linked_req_ids))
                )
                for row in result.all():
                    linked_buy_st_map[row[0]] = {
                        "strategy": row[1],
                        "factor": row[2],
                        "remark": row[3],
                    }

        # 3. 查已有的 strategy_trades（source=order）的 order_req_id 集合
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(StrategyTrade.order_req_id).where(
                    StrategyTrade.source == "order",
                    StrategyTrade.order_req_id.in_([o.req_id for o in orders]),
                )
            )
            recorded_req_ids: set[str] = {r[0] for r in result.all()}

        # 4. 为每个未记录的 qmt_order，在 Redis trades 中找匹配
        #    匹配条件：stock_code + order_type + traded_volume == order_volume + price 接近
        inserted = 0
        trades_to_insert: list[StrategyTrade] = []
        orders_to_update: dict[str, dict] = {}  # req_id -> {traded_price, traded_volume, order_id}
        used_trade_keys: set[str] = set()  # 避免同一条 trade 匹配多个 order

        for order in orders:
            if order.req_id in recorded_req_ids:
                continue

            # 优先通过 Redis trade 的 order_remark 中的 alpha: 前缀精确匹配
            matched_trade = None
            for t in trades_raw:
                t_remark = t.get("order_remark") or ""
                if t_remark.startswith("alpha:"):
                    short_id = t_remark.split(":", 1)[1] if ":" in t_remark else ""
                    if short_id and order.req_id.startswith(short_id):
                        trade_key = f"{t.get('traded_id', '')}_{t.get('order_id', '')}"
                        if trade_key not in used_trade_keys:
                            matched_trade = (t, trade_key)
                            break

            # 回退：stock_code + order_type + volume + price 近似匹配
            if not matched_trade:
                for t in trades_raw:
                    if t.get("stock_code") != order.stock_code:
                        continue
                    if (t.get("order_type") or "").lower() != order.order_type:
                        continue

                    trade_key = f"{t.get('traded_id', '')}_{t.get('order_id', '')}"
                    if trade_key in used_trade_keys:
                        continue

                    traded_volume = int(t.get("traded_volume", 0))
                    traded_price = float(t.get("traded_price", 0))

                    if traded_volume <= 0 or traded_price <= 0:
                        continue
                    if traded_volume != order.order_volume:
                        continue

                    order_price = float(order.price or 0)
                    if order_price > 0 and abs(traded_price - order_price) / order_price > 0.005:
                        continue

                    matched_trade = (t, trade_key)
                    break

            if not matched_trade:
                continue

            t, trade_key = matched_trade
            traded_volume = int(t.get("traded_volume", 0))
            traded_price = float(t.get("traded_price", 0))

            # 匹配成功 — 解析 strategy/factor
            strategy, factor, remark = _parse_remark(order.order_remark)
            if not strategy and order.strategy_name:
                strategy = order.strategy_name
            # 卖出订单无 order_remark 时，从 linked buy order 继承
            if not strategy and order.linked_req_id and order.linked_req_id in linked_buy_map:
                buy_order = linked_buy_map[order.linked_req_id]
                buy_strategy, buy_factor, buy_remark = _parse_remark(buy_order.order_remark)
                if not strategy:
                    strategy = buy_strategy or buy_order.strategy_name
                if not factor:
                    factor = buy_factor
                if not remark:
                    remark = buy_remark
            # 仍为空：从 strategy_trades 中查找 linked buy 的记录
            if (not strategy or not factor) and order.linked_req_id and order.linked_req_id in linked_buy_st_map:
                st = linked_buy_st_map[order.linked_req_id]
                if not strategy:
                    strategy = st.get("strategy")
                if not factor:
                    factor = st.get("factor")
                if not remark:
                    remark = st.get("remark")

            direction = "buy" if order.order_type == "buy" else "sell"
            trade_date_str = t.get("traded_time") or t.get("trade_time")
            trade_date = date.today()
            if trade_date_str:
                try:
                    trade_date = date.fromisoformat(str(trade_date_str)[:10])
                except (ValueError, TypeError):
                    pass

            trades_to_insert.append(StrategyTrade(
                account_id=order.account_id,
                stock_code=order.stock_code,
                stock_name=t.get("stock_name") or order.stock_name,
                direction=direction,
                volume=traded_volume,
                price=traded_price,
                amount=round(traded_volume * traded_price, 4),
                strategy=strategy,
                factor=factor,
                remark=remark,
                trade_date=trade_date,
                source="order",
                order_req_id=order.req_id,
                linked_req_id=order.linked_req_id,
            ))
            # 记录需要更新 qmt_orders 的成交信息
            update_vals: dict[str, Any] = {
                "traded_price": traded_price,
                "traded_volume": traded_volume,
                "status": "filled",
            }
            if t.get("order_id"):
                update_vals["order_id"] = t["order_id"]
            orders_to_update[order.req_id] = update_vals
            used_trade_keys.add(trade_key)
            recorded_req_ids.add(order.req_id)

        # 5. 批量插入 strategy_trades
        if trades_to_insert:
            async with self._db_session_factory() as session:
                session.add_all(trades_to_insert)
                await session.commit()
            inserted = len(trades_to_insert)
            logger.info("Reconciled {} strategy trades from account data", inserted)

        # 6. 回写 qmt_orders 的成交信息
        if orders_to_update:
            async with self._db_session_factory() as session:
                for req_id, vals in orders_to_update.items():
                    await session.execute(
                        update(QmtOrder)
                        .where(QmtOrder.req_id == req_id)
                        .values(**vals)
                    )
                await session.commit()
            logger.info("Updated {} qmt_orders with fill data", len(orders_to_update))

        return inserted

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
    # 策略持仓
    # ------------------------------------------------------------------

    async def get_strategy_positions(self) -> list[dict]:
        """从 strategy_trades 流水表聚合当前持仓，补充实时行情。"""
        from sqlalchemy import func, case
        from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return

        async with self._db_session_factory() as session:
            stmt = (
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    StrategyTrade.trade_date,
                    StrategyTrade.order_req_id,
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.volume),
                            else_=-StrategyTrade.volume,
                        )
                    ).label("holding_volume"),
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.amount),
                            else_=-StrategyTrade.amount,
                        )
                    ).label("total_cost"),
                )
                .where(StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID)
                .group_by(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    StrategyTrade.trade_date,
                    StrategyTrade.order_req_id,
                )
                .having(text("holding_volume > 0"))
            )
            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return []

        # 批量获取实时行情
        codes = list({r.stock_code for r in rows})
        snapshot = await self.get_snapshot(codes)

        # 批量获取复权因子
        adj_factors = await get_adjustment_factors(codes, self._config.REDIS_URL)

        results = []
        for r in rows:
            volume = int(r.holding_volume)
            total_cost = float(r.total_cost)
            avg_price = total_cost / volume if volume > 0 else 0
            tick = snapshot.get(r.stock_code, {})
            current_price = tick.get("last", avg_price)
            pct_change = tick.get("pct_change", 0)
            adj_ret = calc_adjusted_return(
                avg_price, current_price, r.trade_date,
                adj_factors.get(r.stock_code, []),
            )
            pnl = total_cost * adj_ret

            results.append({
                "stock_code": r.stock_code,
                "volume": volume,
                "trade_date": str(r.trade_date),
                "avg_price": avg_price,
                "other": r.remark or "",
                "cost": total_cost,
                "pct_change": pct_change,
                "current_price": current_price,
                "pnl": pnl,
                "order_req_id": r.order_req_id or "",
            })
        return results

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
