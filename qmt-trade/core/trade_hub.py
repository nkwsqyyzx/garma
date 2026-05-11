"""
TradeHub：封装 xttrader 的下单/撤单/查询操作。
进程隔离：本模块不 import 任何 xtdata / MarketHub 相关代码。
"""

import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# xttrader 通过 xtquant SDK 提供
_xttrader = None
_xtconstant = None
_xttype = None


def _init_xtquant(mini_path: str):
    """初始化 xtquant 模块"""
    global _xttrader, _xtconstant, _xttype
    if _xttrader is not None:
        return

    api_path = str(Path(mini_path) / "XtQuantApi")
    if api_path not in sys.path:
        sys.path.insert(0, api_path)
    if mini_path not in sys.path:
        sys.path.insert(0, mini_path)

    from xtquant import xttrader, xtconstant
    from xtquant.xttype import StockAccount
    _xttrader = xttrader
    _xtconstant = xtconstant
    _xttype = StockAccount


class TradeHub:
    """
    交易中枢：封装 xttrader 实例管理，提供下单/撤单/查询操作。
    """

    # 价格类型映射：Alpha 传入 → xtquant price_type 常量值
    PRICE_TYPE_MAP = {
        "limit": 11,  # FIX_PRICE
        "market": 5,  # MARKET_PEER_PRICE_FIRST
        "best5": 6,  # MARKET_BEST_PRICE
        "cancel_remain": 10,  # MARKET_CANCEL_REMAIN
    }

    # 买卖方向映射
    ORDER_TYPE_MAP = {
        "buy": 23,  # STOCK_BUY
        "sell": 24,  # STOCK_SELL
    }

    def __init__(self, redis_bridge, config: dict):
        self._redis = redis_bridge
        self._config = config
        self._qmt_cfg = config.get("qmt", {})
        self._trade_cfg = config.get("trade", {})

        self._trader = None  # XtQuantTrader 实例
        self._account = None  # StockAccount 实例
        self._connected = False
        self._lock = threading.Lock()

        # 最后一次下单时间戳
        self._last_order_at: float = 0.0

    @property
    def connected(self) -> bool:
        if self._trader is None:
            return False
        try:
            return self._trader.connected
        except Exception:
            return False

    @property
    def last_order_at(self) -> float:
        return self._last_order_at

    @property
    def account(self):
        return self._account

    @property
    def trader(self):
        return self._trader

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """连接 xttrader 并注册回调"""
        mini_path = self._qmt_cfg.get("mini_path", "")
        account_id = self._qmt_cfg.get("account_id", "")
        account_type = self._qmt_cfg.get("account_type", "STOCK")
        session_id = self._qmt_cfg.get("session_id", 1001)
        timeout = self._qmt_cfg.get("connect_timeout", 10)

        if not mini_path or not account_id:
            logger.error("[ERROR] qmt.mini_path 或 qmt.account_id 未配置")
            return False

        try:
            _init_xtquant(mini_path)

            self._account = _xttype(account_id, account_type)
            self._trader = _xttrader.XtQuantTrader(mini_path, session_id)

            # 注册回调（由 SessionMgr 设置）
            # self._trader.register_callback(callback_handler)

            # 连接
            self._trader.start()
            connect_result = self._trader.connect()

            if connect_result == 0:
                self._connected = True
                logger.info("[OK] xttrader 连接成功 account=%s", account_id)
                return True
            else:
                logger.error("[ERROR] xttrader 连接失败 result=%d", connect_result)
                self._connected = False
                return False

        except Exception:
            logger.error("[ERROR] xttrader 连接异常", exc_info=True)
            self._connected = False
            return False

    def disconnect(self) -> None:
        """断开 xttrader 连接"""
        if self._trader:
            try:
                self._trader.stop()
            except Exception:
                pass
            self._connected = False
            logger.info("[OK] xttrader 已断开")

    def register_callback(self, callback_handler) -> None:
        """注册回调处理器"""
        if self._trader and callback_handler:
            self._trader.register_callback(callback_handler)

    # ------------------------------------------------------------------
    # 下单 / 撤单（CmdConsumer 调用）
    # ------------------------------------------------------------------

    def place_order(self, cmd: dict) -> Optional[int]:
        """
        异步下单。返回 xtquant 委托编号（order_id），失败返回 None。

        cmd 字段：stock_code, order_type, order_volume, price_type, price,
                  req_id, strategy_name, order_remark
        """
        if not self._connected or not self._trader:
            logger.error("[ERROR] xttrader 未连接，无法下单")
            return None

        try:
            stock_code = cmd["stock_code"]
            order_type_str = cmd.get("order_type", "buy")
            order_volume = cmd["order_volume"]
            price_type_str = cmd.get("price_type", "limit")
            price = cmd.get("price", 0)

            xt_order_type = self.ORDER_TYPE_MAP.get(order_type_str)
            if xt_order_type is None:
                logger.error("[ERROR] 未知 order_type: %s", order_type_str)
                return None

            xt_price_type = self.PRICE_TYPE_MAP.get(price_type_str)
            if xt_price_type is None:
                logger.error("[ERROR] 未知 price_type: %s", price_type_str)
                return None

            # 构建 order_remark: "alpha:{req_id}"
            req_id = cmd.get("req_id", "")
            order_remark = f"alpha:{req_id}" if req_id else cmd.get("order_remark", "")

            strategy_name = cmd.get("strategy_name", "")

            order_id = self._trader.order_stock_async(
                account=self._account,
                stock_code=stock_code,
                order_type=xt_order_type,
                order_volume=order_volume,
                price_type=xt_price_type,
                price=price,
                strategy_name=strategy_name,
                order_remark=order_remark,
            )

            if order_id >= 0:
                self._last_order_at = time.time()
                logger.info(
                    "[OK] 下单成功 order_id=%d stock=%s %s %d@%.2f req_id=%s",
                    order_id, stock_code, order_type_str, order_volume, price, req_id
                )
                return order_id
            else:
                logger.error("[ERROR] 下单失败 return=%d stock=%s", order_id, stock_code)
                return None

        except Exception:
            logger.error("[ERROR] place_order 异常", exc_info=True)
            return None

    def cancel_order(self, order_id: int) -> bool:
        """
        异步撤单。返回是否成功提交撤单请求。
        """
        if not self._connected or not self._trader:
            logger.error("[ERROR] xttrader 未连接，无法撤单")
            return False

        try:
            result = self._trader.cancel_order_stock_async(
                account=self._account,
                order_id=order_id,
            )
            if result >= 0:
                logger.info("[OK] 撤单请求已提交 order_id=%d", order_id)
                return True
            else:
                logger.error("[ERROR] 撤单请求失败 order_id=%d result=%d", order_id, result)
                return False
        except Exception:
            logger.error("[ERROR] cancel_order 异常", exc_info=True)
            return False

    # ------------------------------------------------------------------
    # 查询操作（AccountHub 调用）
    # ------------------------------------------------------------------

    def query_asset(self) -> Optional[object]:
        """查询资金（同步，在后台线程中调用）"""
        if not self._connected or not self._trader:
            return None
        try:
            return self._trader.query_stock_asset(self._account)
        except Exception:
            logger.error("[ERROR] query_asset 异常", exc_info=True)
            return None

    def query_positions(self) -> Optional[list]:
        """查询持仓（同步，在后台线程中调用）"""
        if not self._connected or not self._trader:
            return None
        try:
            return self._trader.query_stock_positions(self._account)
        except Exception:
            logger.error("[ERROR] query_positions 异常", exc_info=True)
            return None

    def query_orders(self) -> Optional[list]:
        """查询当日委托（同步，在后台线程中调用）"""
        if not self._connected or not self._trader:
            return None
        try:
            return self._trader.query_stock_orders(self._account)
        except Exception:
            logger.error("[ERROR] query_orders 异常", exc_info=True)
            return None

    def query_trades(self) -> Optional[list]:
        """查询当日成交（同步，在后台线程中调用）"""
        if not self._connected or not self._trader:
            return None
        try:
            return self._trader.query_stock_trades(self._account)
        except Exception:
            logger.error("[ERROR] query_trades 异常", exc_info=True)
            return None
