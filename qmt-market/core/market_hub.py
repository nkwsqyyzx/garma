"""
MarketHub：基于 xtdata 的行情查询服务。

职责：
- 初始化 xtdata 连接
- 提供同步行情查询方法（供 HTTP API 调用）

不再负责行情订阅和实时推送（已由旧实现接管，数据存储在 Redis db0）。
"""

import logging
import sys
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
    行情查询中枢：封装 xtdata 同步查询接口，供 HTTP API 调用。

    - 初始化时建立 xtdata 连接
    - 提供按需查询方法（tick / kline / detail / sector / history）
    - 支持重连
    """

    def __init__(self, redis_bridge, config: dict):
        """
        Args:
            redis_bridge: RedisBridge 实例（保留用于状态上报）
            config: 完整配置 dict
        """
        self._redis = redis_bridge
        self._config = config
        self._qmt_cfg = config.get("qmt", {})

        # xtdata 模块引用（延迟加载）
        self._xtdata = None
        self._connected = False

        # 运行状态
        self._running = False

    @property
    def connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动 MarketHub：初始化 xtdata 连接"""
        mini_path = self._qmt_cfg.get("mini_path", "")
        if not mini_path:
            logger.error("[ERROR] config.qmt.mini_path 未配置")
            return

        try:
            self._xtdata = _get_xtdata(mini_path)
            self._connected = True
            self._running = True
            logger.info("[OK] xtdata 初始化成功 mini_path=%s", mini_path)
        except Exception:
            logger.error("[ERROR] xtdata 初始化失败", exc_info=True)
            self._connected = False

    def stop(self) -> None:
        """停止 MarketHub"""
        self._running = False
        logger.info("[OK] MarketHub 已停止")

    # ------------------------------------------------------------------
    # 行情查询（HTTP API 调用）
    # ------------------------------------------------------------------

    def get_tick(self, code: str) -> Optional[dict]:
        """查询单只股票最新 Tick（通过 xtdata 实时查询）"""
        if not self._connected or not self._xtdata:
            return None
        try:
            raw = self._xtdata.get_full_tick([code])
            if raw and code in raw:
                return self._normalize_tick(code, raw[code])
        except Exception:
            logger.error("[ERROR] get_tick 失败 code=%s", code, exc_info=True)
        return None

    def get_ticks_batch(self, codes: list[str]) -> dict:
        """批量查询最新 Tick（通过 xtdata 实时查询）"""
        if not self._connected or not self._xtdata:
            return {}
        try:
            raw = self._xtdata.get_full_tick(codes)
            result = {}
            for code, tick in (raw or {}).items():
                normalized = self._normalize_tick(code, tick)
                if normalized:
                    result[code] = normalized
            return result
        except Exception:
            logger.error("[ERROR] get_ticks_batch 失败", exc_info=True)
            return {}

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
        """重连 xtdata"""
        if not self._xtdata:
            return False
        try:
            logger.info("[INFO] 正在重连 xtdata ...")
            mini_path = self._qmt_cfg.get("mini_path", "")
            self._xtdata = _get_xtdata(mini_path)
            self._connected = True
            logger.info("[OK] xtdata 重连成功")
            return True
        except Exception:
            logger.error("[ERROR] xtdata 重连失败", exc_info=True)
            self._connected = False
            return False

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

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
