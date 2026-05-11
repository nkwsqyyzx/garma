"""
MarketHub：管理 xtdata 行情订阅，将回调数据写入 Redis Stream 和快照 Hash。
进程隔离：本模块不 import 任何 xttrader / TradeHub / AccountHub 相关代码。
"""

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# xtdata 通过 xtquant SDK 提供，需要 mini_path 在 sys.path 中
_xtdata = None


def _get_xtdata(mini_path: str):
    """懒加载 xtdata 模块"""
    global _xtdata
    if _xtdata is not None:
        return _xtdata
    # 将 mini_path 的 XtQuantApi 目录加入 sys.path
    api_path = str(Path(mini_path) / "XtQuantApi")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    # 也加入 mini_path 根目录（部分安装方式）
    if mini_path not in sys.path:
        sys.path.insert(0, mini_path)
    from xtquant import xtdata
    _xtdata = xtdata
    return _xtdata


class MarketHub:
    """
    行情中枢：管理 xtdata 订阅，将 Tick/Kline 回调写入 Redis。

    - 订阅池持久化到 Redis Hash qmt:sub:pool
    - Tick 回调使用 Pipeline 批量写 Redis（4 写 / 1 RTT）
    - K 线回调写入对应的 Redis Stream
    - 支持重启后从 Redis 恢复订阅
    """

    def __init__(self, redis_bridge, config: dict):
        """
        Args:
            redis_bridge: RedisBridge 实例
            config: 完整配置 dict
        """
        self._redis = redis_bridge
        self._config = config
        self._market_cfg = config.get("market", {})
        self._qmt_cfg = config.get("qmt", {})

        # xtdata 模块引用（延迟加载）
        self._xtdata = None
        self._connected = False

        # 订阅管理
        self._subscribed_codes: set = set()
        self._sub_lock = threading.Lock()

        # 心跳节流
        self._last_heartbeat_ts: float = 0.0

        # 上限控制
        self._max_subscribe = self._market_cfg.get("max_subscribe_count", 500)
        self._tick_stream_maxlen = self._market_cfg.get("tick_stream_maxlen", 500)
        self._agg_stream_maxlen = self._market_cfg.get("agg_stream_maxlen", 50000)
        self._kline_stream_maxlen = self._market_cfg.get("kline_stream_maxlen", 1000)

        # 运行状态
        self._running = False
        self._last_tick_at: float = 0.0

        # 回调统计（供 StatusReporter 读取）
        self._tick_count: int = 0
        self._kline_count: int = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def subscribed_codes(self) -> set:
        with self._sub_lock:
            return set(self._subscribed_codes)

    @property
    def subscribed_count(self) -> int:
        with self._sub_lock:
            return len(self._subscribed_codes)

    @property
    def last_tick_at(self) -> float:
        return self._last_tick_at

    @property
    def tick_count(self) -> int:
        return self._tick_count

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 MarketHub：初始化 xtdata，恢复订阅池"""
        mini_path = self._qmt_cfg.get("mini_path", "")
        if not mini_path:
            logger.error("[ERROR] config.qmt.mini_path 未配置")
            return

        try:
            self._xtdata = _get_xtdata(mini_path)
            self._connected = True
            logger.info("[OK] xtdata 初始化成功 mini_path=%s", mini_path)
        except Exception:
            logger.error("[ERROR] xtdata 初始化失败", exc_info=True)
            self._connected = False
            return

        self._running = True

        # 从 Redis 恢复订阅池
        self._restore_subscriptions()

    def stop(self) -> None:
        """停止 MarketHub：取消全部订阅"""
        self._running = False
        if not self._xtdata:
            return

        try:
            with self._sub_lock:
                for code in self._subscribed_codes:
                    try:
                        self._xtdata.unsubscribe_quote(code)
                    except Exception:
                        pass
                self._subscribed_codes.clear()
            logger.info("[OK] MarketHub 已停止，全部订阅已取消")
        except Exception:
            logger.error("[ERROR] MarketHub 停止异常", exc_info=True)

    # ------------------------------------------------------------------
    # 订阅管理
    # ------------------------------------------------------------------

    def subscribe(self, codes: list[str], source: str = "api") -> dict:
        """
        添加订阅。
        Returns: {"added": [...], "skipped": [...], "error": str | None}
        """
        if not self._connected:
            return {"added": [], "skipped": codes, "error": "xtdata 未连接"}

        added = []
        skipped = []

        with self._sub_lock:
            current_count = len(self._subscribed_codes)
            for code in codes:
                if code in self._subscribed_codes:
                    skipped.append(code)
                    continue
                # 检查上限
                if current_count >= self._max_subscribe:
                    return {
                        "added": added,
                        "skipped": skipped + [c for c in codes if c not in self._subscribed_codes and c not in added],
                        "error": f"超出订阅上限 {self._max_subscribe}",
                    }
                try:
                    self._xtdata.subscribe_quote(code, callback=self._on_tick)
                    self._subscribed_codes.add(code)
                    self._redis.add_to_sub_pool(code, source)
                    added.append(code)
                    current_count += 1
                except Exception as e:
                    logger.error("[ERROR] 订阅 %s 失败: %s", code, e)
                    skipped.append(code)

        if added:
            logger.info("[OK] 添加订阅 %d 只: %s", len(added), added)
        return {"added": added, "skipped": skipped, "error": None}

    def unsubscribe(self, codes: list[str]) -> dict:
        """
        取消订阅。
        Returns: {"removed": [...], "not_found": [...]}
        """
        if not self._connected:
            return {"removed": [], "not_found": codes}

        removed = []
        not_found = []

        with self._sub_lock:
            for code in codes:
                if code not in self._subscribed_codes:
                    not_found.append(code)
                    continue
                try:
                    self._xtdata.unsubscribe_quote(code)
                    self._subscribed_codes.discard(code)
                    self._redis.remove_from_sub_pool(code)
                    removed.append(code)
                except Exception as e:
                    logger.error("[ERROR] 取消订阅 %s 失败: %s", code, e)
                    not_found.append(code)

        if removed:
            logger.info("[OK] 取消订阅 %d 只: %s", len(removed), removed)
        return {"removed": removed, "not_found": not_found}

    def unsubscribe_all(self) -> int:
        """清空全部订阅，返回取消数量"""
        with self._sub_lock:
            codes = list(self._subscribed_codes)
            self._subscribed_codes.clear()

        if self._connected and self._xtdata:
            for code in codes:
                try:
                    self._xtdata.unsubscribe_quote(code)
                except Exception:
                    pass

        self._redis.clear_sub_pool()
        logger.info("[OK] 已清空全部订阅，共 %d 只", len(codes))
        return len(codes)

    def get_subscription_list(self) -> list[dict]:
        """返回当前订阅列表"""
        with self._sub_lock:
            codes = list(self._subscribed_codes)
        pool = self._redis.get_sub_pool()
        result = []
        for code in codes:
            info_raw = pool.get(code, "{}")
            try:
                info = json.loads(info_raw) if isinstance(info_raw, str) else info_raw
            except json.JSONDecodeError:
                info = {}
            result.append({
                "code": code,
                "added_at": info.get("added_at"),
                "source": info.get("source", "unknown"),
            })
        return result

    # ------------------------------------------------------------------
    # xtdata 回调
    # ------------------------------------------------------------------

    def _on_tick(self, data: dict) -> None:
        """
        xtdata Tick 回调（在 xtdata 内部线程中执行）。

        xtdata 回调格式：data = {code: {field: value, ...}, ...}
        """
        if not self._running:
            return

        try:
            for code, tick in data.items():
                payload = self._normalize_tick(code, tick)
                if not payload:
                    continue

                # 心跳节流：仅距上次更新 > 1s 时包含在 Pipeline 中
                now = time.time()
                include_hb = (now - self._last_heartbeat_ts > 1.0)
                if include_hb:
                    self._last_heartbeat_ts = now

                # Pipeline 批量写 Redis（3-4 次写操作合并为 1 RTT）
                self._redis.publish_tick(
                    code, payload,
                    tick_maxlen=self._tick_stream_maxlen,
                    agg_maxlen=self._agg_stream_maxlen,
                    include_heartbeat=include_hb,
                )

                self._last_tick_at = now
                self._tick_count += 1

        except Exception:
            logger.error("[ERROR] _on_tick 处理异常", exc_info=True)

    def _on_kline(self, data: dict) -> None:
        """
        xtdata K 线回调。

        data 格式取决于 xtdata 版本，通常为 {code: {field: value}}
        """
        if not self._running:
            return

        try:
            for code, kline_data in data.items():
                # 从回调中推断周期（xtdata 可能不直接提供，需根据订阅上下文）
                period = kline_data.get("period", "1m")
                payload = self._normalize_kline(code, period, kline_data)
                if not payload:
                    continue

                self._redis.publish_kline(
                    code, period, payload,
                    maxlen=self._kline_stream_maxlen,
                )
                self._kline_count += 1

        except Exception:
            logger.error("[ERROR] _on_kline 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 行情查询（HTTP API 调用）
    # ------------------------------------------------------------------

    def get_tick(self, code: str) -> Optional[dict]:
        """查询单只股票最新 Tick（优先从 Redis 快照读取）"""
        # 优先从 Redis 快照读取
        tick = self._redis.get_tick_snapshot(code)
        if tick:
            return tick

        # 回退到 xtdata 实时查询
        if self._connected and self._xtdata:
            try:
                raw = self._xtdata.get_full_tick([code])
                if raw and code in raw:
                    return self._normalize_tick(code, raw[code])
            except Exception:
                logger.error("[ERROR] xtdata get_full_tick 失败", exc_info=True)
        return None

    def get_ticks_batch(self, codes: list[str]) -> dict:
        """批量查询最新 Tick（最多 50 只）"""
        # 优先从 Redis 批量读取
        result = self._redis.get_tick_snapshots_batch(codes)
        if len(result) == len(codes):
            return result

        # 补充缺失的
        missing = [c for c in codes if c not in result]
        if missing and self._connected and self._xtdata:
            try:
                raw = self._xtdata.get_full_tick(missing)
                for code, tick in (raw or {}).items():
                    normalized = self._normalize_tick(code, tick)
                    if normalized:
                        result[code] = normalized
            except Exception:
                logger.error("[ERROR] xtdata get_full_tick batch 失败", exc_info=True)
        return result

    def get_kline(self, code: str, period: str = "1d",
                  count: int = 100) -> list:
        """查询 K 线数据（从 xtdata 获取）"""
        if not self._connected or not self._xtdata:
            return []
        try:
            raw = self._xtdata.get_market_data_ex(
                field_list=[],
                stock_list=[code],
                period=period,
                count=count,
            )
            if not raw or code not in raw:
                return []
            df = raw[code]
            if df is None or df.empty:
                return []
            # 转换为列表
            result = []
            for idx, row in df.iterrows():
                result.append({
                    "code": code,
                    "period": period,
                    "datetime": str(idx),
                    "open": float(row.get("open", 0)),
                    "high": float(row.get("high", 0)),
                    "low": float(row.get("low", 0)),
                    "close": float(row.get("close", 0)),
                    "volume": int(row.get("volume", 0)),
                    "amount": float(row.get("amount", 0)),
                })
            return result
        except Exception:
            logger.error("[ERROR] get_kline 失败", exc_info=True)
            return []

    def get_instrument_detail(self, code: str) -> Optional[dict]:
        """查询股票基本信息（名称、涨停价等）"""
        if not self._connected or not self._xtdata:
            return None
        try:
            detail = self._xtdata.get_instrument_detail(code)
            if not detail:
                return None
            return {
                "code": code,
                "name": detail.get("InstrumentName", ""),
                "product_class": detail.get("ProductClass", ""),
                "open_date": detail.get("OpenDate", ""),
                "volume_multiple": detail.get("VolumeMultiple", 0),
                "price_tick": detail.get("PriceTick", 0),
                "limit_up": detail.get("UpStopPrice", 0),
                "limit_down": detail.get("DownStopPrice", 0),
            }
        except Exception:
            logger.error("[ERROR] get_instrument_detail 失败", exc_info=True)
            return None

    def get_sector_list(self, sector: str) -> list[str]:
        """查询板块成分股列表"""
        if not self._connected or not self._xtdata:
            return []
        try:
            return self._xtdata.get_stock_list_in_sector(sector) or []
        except Exception:
            logger.error("[ERROR] get_sector_list 失败", exc_info=True)
            return []

    def get_full_tick(self, code: str) -> Optional[dict]:
        """查询完整 Tick（含逐笔）"""
        if not self._connected or not self._xtdata:
            return None
        try:
            raw = self._xtdata.get_full_tick([code])
            if raw and code in raw:
                return self._normalize_tick(code, raw[code])
        except Exception:
            logger.error("[ERROR] get_full_tick 失败", exc_info=True)
        return None

    def download_history(self, code: str, period: str = "1d",
                         start_time: str = "", end_time: str = "",
                         incrementally: bool = True) -> dict:
        """触发历史行情下载"""
        if not self._connected or not self._xtdata:
            return {"error": "xtdata 未连接", "downloaded": False}
        try:
            self._xtdata.download_history_data(
                code, period, start_time, end_time, incrementally
            )
            return {"downloaded": True, "code": code, "period": period}
        except Exception as e:
            return {"error": str(e), "downloaded": False}

    # ------------------------------------------------------------------
    # 重连
    # ------------------------------------------------------------------

    def reconnect(self) -> bool:
        """重连 xtdata 并恢复订阅"""
        if not self._xtdata:
            return False
        try:
            logger.info("[INFO] 正在重连 xtdata ...")
            # xtdata 没有显式 reconnect 方法，重新初始化
            mini_path = self._qmt_cfg.get("mini_path", "")
            self._xtdata = _get_xtdata(mini_path)
            self._connected = True

            # 恢复订阅
            self._restore_subscriptions()
            logger.info("[OK] xtdata 重连成功")
            return True
        except Exception:
            logger.error("[ERROR] xtdata 重连失败", exc_info=True)
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _restore_subscriptions(self) -> None:
        """从 Redis 订阅池恢复订阅"""
        if not self._connected or not self._xtdata:
            return

        # 这里直接订阅全市场的tick信息.
        sid = self._xtdata.subscribe_whole_quote(['SH', 'SZ'], callback=self._on_tick)
        logger.info(f"[OK] 订阅全市场的tick推送信息, 订阅id: {sid}")

        pool = self._redis.get_sub_pool()
        if not pool:
            logger.info("[INFO] 订阅池为空，无需恢复")
            return

        codes = list(pool.keys())
        logger.info("[INFO] 从订阅池恢复 %d 只股票订阅", len(codes))

        with self._sub_lock:
            for code in codes:
                try:
                    self._subscribed_codes.add(code)
                except Exception as e:
                    logger.error("[ERROR] 恢复订阅 %s 失败: %s", code, e)

        logger.info("[OK] 订阅恢复完成，成功 %d / %d",
                    len(self._subscribed_codes), len(codes))

    def _normalize_tick(self, code: str, tick: dict) -> Optional[dict]:
        """
        标准化 xtdata Tick 数据为统一格式。
        xtdata Tick 字段名可能因版本不同而变化，此处做兼容处理。
        """
        if not tick:
            return None

        try:
            return {
                "code": code,
                "name": tick.get("instrument_name", tick.get("name", "")),
                "time": tick.get("time", ""),
                "datetime": tick.get("datetime", ""),
                "timestamp": tick.get("timestamp", time.time()),
                "open": float(tick.get("open", 0)),
                "high": float(tick.get("high", 0)),
                "low": float(tick.get("low", 0)),
                "last": float(tick.get("lastPrice", tick.get("last", 0))),
                "close": float(tick.get("lastClose", tick.get("close", 0))),
                "bid1": float(tick.get("bid1", 0)),
                "bid2": float(tick.get("bid2", 0)),
                "bid3": float(tick.get("bid3", 0)),
                "bid4": float(tick.get("bid4", 0)),
                "bid5": float(tick.get("bid5", 0)),
                "ask1": float(tick.get("ask1", 0)),
                "ask2": float(tick.get("ask2", 0)),
                "ask3": float(tick.get("ask3", 0)),
                "ask4": float(tick.get("ask4", 0)),
                "ask5": float(tick.get("ask5", 0)),
                "bid_vol1": int(tick.get("bidVol1", tick.get("bid_vol1", 0))),
                "bid_vol2": int(tick.get("bidVol2", tick.get("bid_vol2", 0))),
                "bid_vol3": int(tick.get("bidVol3", tick.get("bid_vol3", 0))),
                "bid_vol4": int(tick.get("bidVol4", tick.get("bid_vol4", 0))),
                "bid_vol5": int(tick.get("bidVol5", tick.get("bid_vol5", 0))),
                "ask_vol1": int(tick.get("askVol1", tick.get("ask_vol1", 0))),
                "ask_vol2": int(tick.get("askVol2", tick.get("ask_vol2", 0))),
                "ask_vol3": int(tick.get("askVol3", tick.get("ask_vol3", 0))),
                "ask_vol4": int(tick.get("askVol4", tick.get("ask_vol4", 0))),
                "ask_vol5": int(tick.get("askVol5", tick.get("ask_vol5", 0))),
                "volume": int(tick.get("volume", 0)),
                "amount": float(tick.get("amount", 0)),
                "avg_price": float(tick.get("avgPrice", tick.get("avg_price", 0))),
                "limit_up": float(tick.get("limitUp", tick.get("limit_up", 0))),
                "limit_down": float(tick.get("limitDown", tick.get("limit_down", 0))),
                "pct_change": float(tick.get("pctChange", tick.get("pct_change", 0))),
                "change": float(tick.get("change", 0)),
            }
        except Exception:
            logger.error("[ERROR] _normalize_tick 异常 code=%s", code, exc_info=True)
            return None

    def _normalize_kline(self, code: str, period: str,
                         kline: dict) -> Optional[dict]:
        """标准化 K 线数据"""
        if not kline:
            return None
        try:
            return {
                "code": code,
                "period": period,
                "datetime": kline.get("datetime", ""),
                "timestamp": kline.get("timestamp", time.time()),
                "open": float(kline.get("open", 0)),
                "high": float(kline.get("high", 0)),
                "low": float(kline.get("low", 0)),
                "close": float(kline.get("close", 0)),
                "volume": int(kline.get("volume", 0)),
                "amount": float(kline.get("amount", 0)),
            }
        except Exception:
            logger.error("[ERROR] _normalize_kline 异常 code=%s", code, exc_info=True)
            return None
