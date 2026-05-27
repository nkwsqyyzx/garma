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
from sqlalchemy import func, select, text, update
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

_CANCEL_STATUSES = frozenset({"CANCELING", "CANCELLED", "PARTIALLY_CANCELLED"})


def _resolve_display_status(order: dict) -> str:
    """根据原始状态 + 成交量计算规范化的 display_status。

    规则:
      - 完全成交 → filled
      - 撤单中/已撤 + 有部分成交 → partially_cancelled
      - 撤单中/已撤 + 无成交 → cancelled
      - UNKNOWN + 有部分成交 → partially_cancelled (手工撤单)
      - UNKNOWN + 无成交 → cancelled
      - 部分成交（未撤）→ partial
      - REJECTED → rejected
      - PENDING / SUBMITTED / CANCEL_FAILED → submitted
      - FILLED → filled
    """
    s = order.get("status", "")
    vol = order.get("order_volume", 0) or 0
    traded = order.get("traded_volume", 0) or 0

    # 完全成交优先
    if vol > 0 and traded >= vol:
        return "filled"

    # 撤单状态 + 有部分成交 → 部撤
    if s in _CANCEL_STATUSES and traded > 0:
        return "partially_cancelled"
    if s in _CANCEL_STATUSES:
        return "cancelled"

    # UNKNOWN: 券商侧已完结但 xtquant 未回调终态（如手工撤单）
    if s == "UNKNOWN":
        return "partially_cancelled" if traded > 0 else "cancelled"

    # 部分成交（未撤）
    if traded > 0 < vol and traded < vol:
        return "partial"

    # 其他原始状态映射
    _map = {
        "PENDING": "submitted",
        "SUBMITTED": "submitted",
        "FILLED": "filled",
        "REJECTED": "rejected",
        "CANCEL_FAILED": "submitted",
        "PARTIALLY_FILLED": "partial",
    }
    return _map.get(s, s.lower() if s else "submitted")


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
        """读取账户资金快照。Redis 无实时数据时从 daily_{date}_account 兜底。"""
        data = await self._redis_get_json(KEY_ACCOUNT_ASSET)
        if data is not None:
            return data
        return await self._get_asset_from_daily_account()

    async def _get_asset_from_daily_account(self) -> dict | None:
        """从 daily_{date}_account 列表读取最近一个交易日的资金数据兜底。

        数据格式 (pickle): [timestamp, market_value, cash, total_asset]
        参见 py_scripts/stock_data/每日总结.py 的 rpush 逻辑。
        """
        import asyncio
        from datetime import timedelta

        today = date.today()
        for i in range(7):
            d = today - timedelta(days=i)
            key = f"daily_{d.strftime('%Y%m%d')}_account"
            try:
                # 使用 _tick_redis（decode_responses=False）以正确读取 pickle 二进制数据
                raw_list = await asyncio.to_thread(self._tick_redis.lrange, key, -1, -1)
                if not raw_list:
                    continue
                values = pickle.loads(raw_list[-1])
                # values: [timestamp, market_value, cash, total_asset]
                return {
                    "total_asset": float(values[3]),
                    "cash": float(values[2]),
                    "market_value": float(values[1]),
                    "frozen_cash": 0,
                    "updated_at": float(values[0]),
                    "updated_by": f"daily_account_fallback_{d}",
                }
            except Exception:
                logger.debug(f"读取 {key} 失败，尝试前一日")
                continue
        return None

    async def get_positions(self) -> list[dict]:
        """读取持仓列表。Redis 无数据时从 daily_positions 表兜底。"""
        data = await self._redis_get_json(KEY_ACCOUNT_POSITIONS)
        if isinstance(data, list) and data:
            return data
        # fallback: 从 daily_positions 读取最近一个交易日的快照
        return await self._get_positions_from_db()

    async def get_orders(self, cancelable_only: bool = False) -> list[dict]:
        """读取当日委托列表，附带 display_status。"""
        data = await self._redis_get_json(KEY_ACCOUNT_ORDERS)
        if not isinstance(data, list):
            return []
        for o in data:
            o['display_status'] = _resolve_display_status(o)
        if cancelable_only:
            cancelable = {"submitted", "partial"}
            return [o for o in data if o.get("display_status", "") in cancelable]
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
        """下单：1) INSERT qmt_orders DRAFT  2) RPUSH 命令队列。

        卖出时使用 SELECT FOR UPDATE 行级锁 + 事务内校验，
        防止并发卖出超出可用持仓。
        """
        req_id = f"alpha_{uuid.uuid4().hex[:16]}_{int(time.time())}"

        # 卖出时，从 strategy_trades 继承策略信息
        # 优先用 linked_batch_id 查找（覆盖拆单批次），否则用 linked_req_id
        strategy_name = request.strategy_name
        order_remark = request.order_remark
        stock_name = None

        # batch_id: 直接传入，或卖出时从 linked_batch_id 继承
        effective_batch_id = request.batch_id
        if not effective_batch_id and request.order_type == "sell":
            effective_batch_id = request.linked_batch_id

        if request.order_type == "sell":
            # ── 卖出路径：单一事务内完成 策略继承 + 持仓校验 + 下单 ──
            async with self._db_session_factory() as session:
                # 1) FOR UPDATE 锁住该标的所有 strategy_trades 行，
                #    阻止并发卖出事务读取过期的持仓数据
                await session.execute(
                    select(StrategyTrade.id)
                    .where(
                        StrategyTrade.stock_code == request.stock_code,
                        StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID,
                    )
                    .with_for_update()
                )

                # 2) 继承策略元数据
                if (not strategy_name or not order_remark) and (request.linked_batch_id or request.linked_req_id):
                    if request.linked_batch_id:
                        meta_result = await session.execute(
                            select(
                                StrategyTrade.strategy,
                                StrategyTrade.factor,
                                StrategyTrade.remark,
                                StrategyTrade.stock_name,
                            )
                            .where(StrategyTrade.batch_id == request.linked_batch_id)
                            .limit(1)
                        )
                    else:
                        meta_result = await session.execute(
                            select(
                                StrategyTrade.strategy,
                                StrategyTrade.factor,
                                StrategyTrade.remark,
                                StrategyTrade.stock_name,
                            )
                            .where(StrategyTrade.order_req_id == request.linked_req_id)
                            .limit(1)
                        )
                    row = meta_result.first()
                    if row:
                        if not strategy_name and row[0]:
                            strategy_name = row[0]
                        if row[1] or row[2]:
                            factor = row[1] or "-"
                            remark_base = row[2] or ""
                            if not order_remark:
                                name = request.stock_code
                                if remark_base and ":" in remark_base:
                                    parts = remark_base.split(":")
                                    if len(parts) >= 4:
                                        name = parts[3]
                                order_remark = f"{strategy_name or '-'}:{factor}:0:{name}"
                        if row[3]:
                            stock_name = row[3]

                # 3) 计算净持仓 (strategy_trades 中买卖抵消)
                from sqlalchemy import func as _func, case as _case
                net_result = await session.execute(
                    select(
                        _func.sum(
                            _case(
                                (StrategyTrade.direction == "buy", StrategyTrade.volume),
                                else_=-StrategyTrade.volume,
                            )
                        )
                    )
                    .where(
                        StrategyTrade.stock_code == request.stock_code,
                        StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID,
                    )
                )
                net_position = int(net_result.scalar() or 0)

                # 4) 计算挂起的未成交卖出量 (qmt_orders 中非终态卖单)
                NON_TERMINAL = ("DRAFT", "PENDING", "SUBMITTED", "PARTIALLY_FILLED")
                pending_result = await session.execute(
                    select(
                        _func.sum(
                            QmtOrder.order_volume - _func.coalesce(QmtOrder.traded_volume, 0)
                        )
                    )
                    .where(
                        QmtOrder.stock_code == request.stock_code,
                        QmtOrder.order_type == "sell",
                        QmtOrder.account_id == self._config.QMT_ACCOUNT_ID,
                        QmtOrder.status.in_(NON_TERMINAL),
                    )
                )
                pending_sell = int(pending_result.scalar() or 0)

                available = net_position - pending_sell
                if request.order_volume > available:
                    raise ValueError(
                        f"卖出量 {request.order_volume} 超过可用持仓 "
                        f"(净持仓={net_position}, 挂单占用={pending_sell}, 可用={available})"
                    )

                # 5) stock_name 兜底
                if not stock_name:
                    names = await self.get_stock_names([request.stock_code])
                    stock_name = names.get(request.stock_code) or None

                # 6) 插入订单
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
                    batch_id=effective_batch_id,
                    status="DRAFT",
                )
                session.add(order)
                await session.commit()
        else:
            # ── 买入路径：无需持仓校验 ──
            if (not strategy_name or not order_remark) and (request.linked_batch_id or request.linked_req_id):
                async with self._db_session_factory() as session:
                    if request.linked_batch_id:
                        meta_result = await session.execute(
                            select(
                                StrategyTrade.strategy,
                                StrategyTrade.factor,
                                StrategyTrade.remark,
                                StrategyTrade.stock_name,
                            )
                            .where(StrategyTrade.batch_id == request.linked_batch_id)
                            .limit(1)
                        )
                    else:
                        meta_result = await session.execute(
                            select(
                                StrategyTrade.strategy,
                                StrategyTrade.factor,
                                StrategyTrade.remark,
                                StrategyTrade.stock_name,
                            )
                            .where(StrategyTrade.order_req_id == request.linked_req_id)
                            .limit(1)
                        )
                    row = meta_result.first()
                    if row:
                        if not strategy_name and row[0]:
                            strategy_name = row[0]
                        if row[1] or row[2]:
                            factor = row[1] or "-"
                            remark_base = row[2] or ""
                            if not order_remark:
                                name = request.stock_code
                                if remark_base and ":" in remark_base:
                                    parts = remark_base.split(":")
                                    if len(parts) >= 4:
                                        name = parts[3]
                                order_remark = f"{strategy_name or '-'}:{factor}:0:{name}"
                        if row[3]:
                            stock_name = row[3]

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
                    batch_id=effective_batch_id,
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
            "batch_id": request.batch_id or "",
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
        """根据订单事件更新 MySQL（纯 UPDATE，行不存在则跳过）。

        支持截断的 req_id 前缀匹配：QMT 券商端会截断 order_remark，
        导致回调中的 req_id 不完整（如 "alpha_b420e6c1f8d" 而非
        "alpha_b420e6c1f8dc4886_1779759166"）。此时通过前缀匹配定位完整 req_id。
        """
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

        # traded_volume 只增不减：防止乱序/延迟的分笔事件覆盖全量值
        tv_event = update_data.pop("traded_volume", None)
        tp_event = update_data.pop("traded_price", None)

        async with self._db_session_factory() as session:
            # 1. 精确匹配
            stmt = (
                update(QmtOrder)
                .where(QmtOrder.req_id == req_id)
                .values(**update_data)
            )
            if tv_event is not None:
                stmt = stmt.values(
                    traded_volume=func.greatest(QmtOrder.traded_volume, tv_event),
                )
                if tp_event is not None and tv_event > 0:
                    stmt = stmt.values(traded_price=tp_event)
            result = await session.execute(stmt)
            await session.commit()

            # 2. 精确匹配失败时，尝试前缀匹配（处理 QMT 截断 remark 的情况）
            if result.rowcount == 0 and req_id.startswith("alpha_"):
                stmt = (
                    update(QmtOrder)
                    .where(QmtOrder.req_id.startswith(req_id))
                    .values(**update_data)
                )
                if tv_event is not None:
                    stmt = stmt.values(
                        traded_volume=func.greatest(QmtOrder.traded_volume, tv_event),
                    )
                    if tp_event is not None and tv_event > 0:
                        stmt = stmt.values(traded_price=tp_event)
                result = await session.execute(stmt)
                await session.commit()
                if result.rowcount > 0:
                    logger.info("Order updated via prefix match: short_req_id={} status={}", req_id, status)

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
            # 重新查询 order 获取最新的 traded_volume/traded_price 作为真值
            # (event 的 traded_volume 可能是分笔值，直接使用会导致数据不准)
            fresh_result = await session.execute(
                select(QmtOrder).where(QmtOrder.req_id == req_id)
            )
            fresh_order = fresh_result.scalar_one_or_none()
            if fresh_order and int(fresh_order.traded_volume or 0) > 0:
                actual_vol = int(fresh_order.traded_volume)
                actual_price = float(fresh_order.traded_price or price)
            else:
                # DB 尚未更新，fallback 到 event 值
                actual_vol = volume
                actual_price = price

            existing = await session.execute(
                select(StrategyTrade).where(StrategyTrade.order_req_id == req_id)
            )
            existing_trade = existing.scalar_one_or_none()
            if existing_trade:
                # 更新已有记录 — 用 DB 的最新 traded_volume
                if actual_vol > 0 and actual_vol != existing_trade.volume:
                    existing_trade.volume = actual_vol
                    existing_trade.price = actual_price
                    existing_trade.amount = round(actual_vol * actual_price, 4)
                    await session.commit()
                    logger.info("Strategy trade updated: req_id={} {} {} vol={}",
                                 req_id, direction, order.stock_code, actual_vol)
                return

            trade = StrategyTrade(
                account_id=order.account_id,
                stock_code=order.stock_code,
                stock_name=order.stock_name,
                direction=direction,
                volume=actual_vol,
                price=actual_price,
                amount=round(actual_vol * actual_price, 4),
                strategy=strategy,
                factor=factor,
                remark=remark,
                trade_date=date.today(),
                source="order",
                order_req_id=req_id,
                linked_req_id=getattr(order, "linked_req_id", None),
                batch_id=getattr(order, "batch_id", None),
            )
            session.add(trade)
            await session.commit()

        logger.info("Strategy trade recorded: req_id={} {} {} vol={} price={}",
                     req_id, direction, order.stock_code, actual_vol, actual_price)

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

        # 4a. 修正已有 strategy_trades 的 volume 差异
        #     用 Redis qmt:account:orders 的 traded_volume 作为真值
        #     （MySQL qmt_orders 可能是过时的分笔值）
        redis_orders_raw = await self._redis_get_json(KEY_ACCOUNT_ORDERS)
        redis_orders_by_id: dict[str, dict] = {}
        if isinstance(redis_orders_raw, list):
            for ro in redis_orders_raw:
                oid = str(ro.get("order_id", ""))
                if oid and oid != "0":
                    redis_orders_by_id[oid] = ro

        if recorded_req_ids:
            async with self._db_session_factory() as session:
                result = await session.execute(
                    select(StrategyTrade).where(
                        StrategyTrade.source == "order",
                        StrategyTrade.order_req_id.in_(list(recorded_req_ids)),
                    )
                )
                existing_trades = {t.order_req_id: t for t in result.scalars().all()}

            orders_by_req = {o.req_id: o for o in orders}
            fixed = 0
            for req_id, st in existing_trades.items():
                o = orders_by_req.get(req_id)
                if not o:
                    continue
                # 优先使用 Redis order 级别数据（最准确）
                redis_order = redis_orders_by_id.get(str(o.order_id or ""))
                if redis_order:
                    order_vol = int(redis_order.get("traded_volume", 0))
                    order_price = float(redis_order.get("traded_price", 0))
                else:
                    # Redis 没有则用 MySQL 的值
                    order_vol = int(o.traded_volume or 0)
                    order_price = float(o.traded_price or st.price)
                if order_vol > 0 and st.volume != order_vol:
                    old_vol = st.volume
                    st.volume = order_vol
                    st.price = order_price or float(st.price)
                    st.amount = round(order_vol * st.price, 4)
                    async with self._db_session_factory() as session:
                        session.add(st)
                        await session.commit()
                    fixed += 1
                    logger.info("Reconcile fixed volume: req_id={} {} {} {}->{}",
                                req_id, st.direction, st.stock_code, old_vol, order_vol)
            if fixed:
                logger.info("Reconcile fixed {} strategy_trades volume mismatches", fixed)

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

            # alpha 前缀的订单只接受精确匹配，不回退到模糊匹配
            # 防止未成交的 alpha 订单错误消费其他订单的成交记录
            is_alpha_order = order.req_id.startswith("alpha_") if order.req_id else False
            if not matched_trade and not is_alpha_order:
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
        """从 strategy_trades 流水表聚合当前持仓，补充实时行情。

        聚合粒度: (stock_code, strategy, factor, position_key)
        - position_key = COALESCE(batch_id, linked_req_id, order_req_id)
          使得拆单批次(batch_id)合为一行，卖出通过 linked_req_id 与买入归入同一组
        """
        from sqlalchemy import func, case
        from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return

        # position_key: 卖出用 linked_req_id 归入买入所在的组
        position_key = func.coalesce(
            StrategyTrade.batch_id,
            case(
                (StrategyTrade.direction == "sell", StrategyTrade.linked_req_id),
                else_=StrategyTrade.order_req_id,
            ),
        ).label("position_key")

        async with self._db_session_factory() as session:
            stmt = (
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    position_key,
                    func.min(StrategyTrade.remark).label("remark"),
                    func.min(StrategyTrade.trade_date).label("trade_date"),
                    func.min(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.order_req_id),
                            else_=None,
                        )
                    ).label("order_req_id"),
                    func.min(StrategyTrade.batch_id).label("batch_id"),
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
                    text("position_key"),
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
                "batch_id": r.batch_id or "",
            })
        return results

    # ------------------------------------------------------------------
    # 策略成交
    # ------------------------------------------------------------------

    async def get_strategy_trades(self, trade_date: date | None = None, source: str | None = None) -> list[dict]:
        """从 strategy_trades 查询成交流水，补充实时行情（仅买入）。"""
        from sqlalchemy import func
        from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return

        async with self._db_session_factory() as session:
            # 确定查询日期：优先指定日期，否则取最大交易日
            if trade_date is None:
                trade_date = date.today()

            stmt = (
                select(StrategyTrade)
                .where(StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID)
                .where(StrategyTrade.trade_date == trade_date)
                .order_by(StrategyTrade.id)
            )
            if source:
                stmt = stmt.where(StrategyTrade.source == source)
            result = await session.execute(stmt)
            rows = result.scalars().all()

            # 无结果时回退到最近交易日（仅 source 不指定时回退）
            if not rows and not source:
                max_date_stmt = (
                    select(func.max(StrategyTrade.trade_date))
                    .where(StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID)
                )
                max_result = await session.execute(max_date_stmt)
                max_date = max_result.scalar()
                if max_date and max_date != trade_date:
                    stmt = (
                        select(StrategyTrade)
                        .where(StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID)
                        .where(StrategyTrade.trade_date == max_date)
                        .order_by(StrategyTrade.id)
                    )
                    result = await session.execute(stmt)
                    rows = result.scalars().all()

            # 构建买入价映射：用于计算卖出盈亏
            buy_price_map: dict[str, float] = {}
            for r in rows:
                if r.direction == "buy" and r.order_req_id:
                    buy_price_map[r.order_req_id] = float(r.price)

            # 补查不在当前结果中的 linked 买入价
            missing_req_ids = list({
                r.linked_req_id for r in rows
                if r.direction == "sell" and r.linked_req_id and r.linked_req_id not in buy_price_map
            })
            if missing_req_ids:
                supp_stmt = (
                    select(StrategyTrade.order_req_id, StrategyTrade.price)
                    .where(StrategyTrade.order_req_id.in_(missing_req_ids))
                    .where(StrategyTrade.direction == "buy")
                )
                supp_result = await session.execute(supp_stmt)
                for row in supp_result:
                    buy_price_map[row[0]] = float(row[1])

        if not rows:
            return []

        # 补齐 stock_name：对缺失名称的股票批量查询
        all_codes = list({r.stock_code for r in rows})
        missing_codes = [c for c in all_codes if not next(
            (r.stock_name for r in rows if r.stock_code == c), None
        )]
        name_map = await self.get_stock_names(missing_codes) if missing_codes else {}

        # 分离买入/卖出记录
        buy_codes = list({r.stock_code for r in rows if r.direction == "buy"})
        snapshot = await self.get_snapshot(buy_codes) if buy_codes else {}
        adj_factors = (
            await get_adjustment_factors(buy_codes, self._config.REDIS_URL)
            if buy_codes else {}
        )

        results = []
        for r in rows:
            entry = {
                "stock_code": r.stock_code,
                "stock_name": r.stock_name or name_map.get(r.stock_code, ""),
                "direction": r.direction,
                "price": float(r.price),
                "volume": int(r.volume),
                "amount": float(r.amount),
                "strategy": r.strategy or "",
                "factor": r.factor or "",
                "remark": r.remark or "",
                "trade_date": str(r.trade_date),
                "order_req_id": r.order_req_id or "",
                "source": r.source or "",
                "pct_change": 0,
                "pnl": 0,
            }

            if r.direction == "buy":
                tick = snapshot.get(r.stock_code, {})
                current_price = tick.get("last", float(r.price))
                pct_change = tick.get("pct_change", 0)
                adj_ret = calc_adjusted_return(
                    float(r.price), current_price, r.trade_date,
                    adj_factors.get(r.stock_code, []),
                )
                pnl = float(r.amount) * adj_ret
                entry["pct_change"] = pct_change
                entry["pnl"] = pnl
            elif r.direction == "sell" and r.linked_req_id:
                buy_price = buy_price_map.get(r.linked_req_id)
                if buy_price and buy_price > 0:
                    sell_price = float(r.price)
                    pnl = (sell_price - buy_price) * int(r.volume)
                    pct_change = ((sell_price - buy_price) / buy_price) * 100
                    entry["pct_change"] = pct_change
                    entry["pnl"] = pnl

            results.append(entry)
        return results

    # ------------------------------------------------------------------
    # 银证转账 & 资产快照
    # ------------------------------------------------------------------

    async def list_fund_transfers(self, start_date: date | None = None, end_date: date | None = None) -> list[dict]:
        """查询银证转账记录。"""
        from backend.models.fund_transfer import FundTransfer
        async with self._db_session_factory() as session:
            stmt = select(FundTransfer).order_by(FundTransfer.trade_date.desc(), FundTransfer.id.desc())
            if start_date:
                stmt = stmt.where(FundTransfer.trade_date >= start_date)
            if end_date:
                stmt = stmt.where(FundTransfer.trade_date <= end_date)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "account_id": r.account_id,
                    "trade_date": str(r.trade_date),
                    "direction": r.direction,
                    "amount": float(r.amount),
                    "note": r.note,
                    "created_at": str(r.created_at),
                    "updated_at": str(r.updated_at),
                }
                for r in rows
            ]

    async def create_fund_transfer(self, trade_date: str, direction: str, amount: float, note: str | None = None) -> dict:
        """创建银证转账记录。"""
        from backend.models.fund_transfer import FundTransfer
        async with self._db_session_factory() as session:
            transfer = FundTransfer(
                account_id=self._config.QMT_ACCOUNT_ID,
                trade_date=date.fromisoformat(trade_date),
                direction=direction,
                amount=amount,
                note=note,
            )
            session.add(transfer)
            await session.commit()
            await session.refresh(transfer)
            return {
                "id": transfer.id,
                "account_id": transfer.account_id,
                "trade_date": str(transfer.trade_date),
                "direction": transfer.direction,
                "amount": float(transfer.amount),
                "note": transfer.note,
                "created_at": str(transfer.created_at),
            }

    async def delete_fund_transfer(self, transfer_id: int) -> bool:
        """删除银证转账记录。"""
        from backend.models.fund_transfer import FundTransfer
        async with self._db_session_factory() as session:
            stmt = select(FundTransfer).where(FundTransfer.id == transfer_id)
            result = await session.execute(stmt)
            row = result.scalar_one_or_none()
            if row is None:
                return False
            await session.delete(row)
            await session.commit()
            return True

    async def list_asset_snapshots(self, start_date: date | None = None, end_date: date | None = None) -> list[dict]:
        """查询资产快照历史。"""
        from backend.models.daily_asset_snapshot import DailyAssetSnapshot
        async with self._db_session_factory() as session:
            stmt = select(DailyAssetSnapshot).order_by(DailyAssetSnapshot.trade_date.desc())
            if start_date:
                stmt = stmt.where(DailyAssetSnapshot.trade_date >= start_date)
            if end_date:
                stmt = stmt.where(DailyAssetSnapshot.trade_date <= end_date)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "account_id": r.account_id,
                    "trade_date": str(r.trade_date),
                    "snapshot_type": r.snapshot_type,
                    "total_asset": float(r.total_asset),
                    "cash": float(r.cash),
                    "frozen_cash": float(r.frozen_cash),
                    "market_value": float(r.market_value),
                    "created_at": str(r.created_at),
                }
                for r in rows
            ]

    async def get_daily_pnl_summary(self, start_date: date | None = None, end_date: date | None = None) -> list[dict]:
        """每日盈亏汇总：JOIN pre/post snapshot + transfers。"""
        account_id = self._config.QMT_ACCOUNT_ID
        async with self._db_session_factory() as session:
            sql = text("""
                SELECT
                    pre.trade_date AS trade_date,
                    pre.total_asset AS pre_asset,
                    post.total_asset AS post_asset,
                    COALESCE(t.net_transfer, 0) AS net_transfer
                FROM (
                    SELECT trade_date, total_asset
                    FROM daily_asset_snapshots
                    WHERE account_id = :account_id AND snapshot_type = 'pre_market'
                ) pre
                LEFT JOIN (
                    SELECT trade_date, total_asset
                    FROM daily_asset_snapshots
                    WHERE account_id = :account_id AND snapshot_type = 'post_market'
                ) post ON pre.trade_date = post.trade_date
                LEFT JOIN (
                    SELECT trade_date,
                        SUM(CASE WHEN direction = 'deposit' THEN amount ELSE -amount END) AS net_transfer
                    FROM fund_transfers
                    WHERE account_id = :account_id
                    GROUP BY trade_date
                ) t ON pre.trade_date = t.trade_date
                WHERE 1=1
            """)
            params = {"account_id": account_id}
            if start_date:
                sql = text(sql.text + " AND pre.trade_date >= :start_date")
                params["start_date"] = start_date
            if end_date:
                sql = text(sql.text + " AND pre.trade_date <= :end_date")
                params["end_date"] = end_date
            sql = text(sql.text + " ORDER BY pre.trade_date DESC")

            result = await session.execute(sql, params)
            rows = result.fetchall()
            summary = []
            for r in rows:
                pre_a = float(r.pre_asset) if r.pre_asset else None
                post_a = float(r.post_asset) if r.post_asset else None
                net_t = float(r.net_transfer) if r.net_transfer else 0
                daily_pnl = round(post_a - pre_a, 4) if (pre_a is not None and post_a is not None) else None
                adjusted_pnl = round(daily_pnl - net_t, 4) if daily_pnl is not None else None
                summary.append({
                    "trade_date": str(r.trade_date),
                    "pre_asset": pre_a,
                    "post_asset": post_a,
                    "daily_pnl": daily_pnl,
                    "net_transfer": net_t,
                    "adjusted_pnl": adjusted_pnl,
                })
            return summary

    async def save_asset_snapshot(self, snapshot_type: str) -> bool:
        """保存资产快照到 MySQL，用于 Worker 调用。"""
        from backend.models.daily_asset_snapshot import DailyAssetSnapshot
        asset = await self.get_asset()
        if asset is None:
            return False
        today = date.today()
        async with self._db_session_factory() as session:
            try:
                snapshot = DailyAssetSnapshot(
                    account_id=self._config.QMT_ACCOUNT_ID,
                    trade_date=today,
                    snapshot_type=snapshot_type,
                    total_asset=asset.get("total_asset", 0),
                    cash=asset.get("cash", 0),
                    frozen_cash=asset.get("frozen_cash", 0),
                    market_value=asset.get("market_value", 0),
                )
                session.add(snapshot)
                await session.commit()
                logger.info("Asset snapshot saved: type={} date={} total_asset={}", snapshot_type, today, asset.get("total_asset"))
                return True
            except Exception as e:
                await session.rollback()
                if "Duplicate entry" in str(e):
                    logger.debug("Asset snapshot already exists: type={} date={}", snapshot_type, today)
                    return True
                logger.warning("Failed to save asset snapshot: {}", e)
                return False

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
