"""
AccountHub：账户状态内存快照管理。
事件驱动优先（回调即更新）+ 10s 轮询兜底。
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from shared.const import CANCELABLE_STATUSES, ORDER_STATUS_MAP

logger = logging.getLogger(__name__)


def _normalize_asset(raw) -> dict:
    """标准化 xtquant 资金数据（XtAsset 只有 cash/frozen_cash/market_value/total_asset）"""
    if raw is None:
        return {}
    try:
        return {
            "account_id": getattr(raw, "account_id", ""),
            "total_asset": float(getattr(raw, "total_asset", 0)),
            "cash": float(getattr(raw, "cash", 0)),
            "frozen_cash": float(getattr(raw, "frozen_cash", 0)),
            "market_value": float(getattr(raw, "market_value", 0)),
            "updated_at": time.time(),
            "updated_by": "full_sync",
        }
    except Exception:
        return {}


def _normalize_position(raw) -> dict:
    """标准化 xtquant 持仓数据（profit_loss/profit_loss_ratio 由 open_price*volume 和 market_value 计算）"""
    try:
        volume = int(getattr(raw, "volume", 0))
        open_price = float(getattr(raw, "open_price", 0))
        market_value = float(getattr(raw, "market_value", 0))
        cost = open_price * volume
        profit_loss = round(market_value - cost, 4)
        profit_loss_ratio = round(profit_loss / cost * 100, 4) if cost > 0 else 0.0
        return {
            "account_id": getattr(raw, "account_id", ""),
            "stock_code": getattr(raw, "stock_code", ""),
            "volume": volume,
            "can_use_volume": int(getattr(raw, "can_use_volume", 0)),
            "frozen_volume": int(getattr(raw, "frozen_volume", 0)),
            "on_road_volume": int(getattr(raw, "on_road_volume", 0)),
            "yesterday_volume": int(getattr(raw, "yesterday_volume", 0)),
            "avg_price": float(getattr(raw, "avg_price", 0)),
            "open_price": open_price,
            "market_value": market_value,
            "profit_loss": profit_loss,
            "profit_loss_ratio": profit_loss_ratio,
            "updated_at": time.time(),
        }
    except Exception:
        return {}


def _normalize_order(raw) -> dict:
    """标准化 xtquant 委托数据（XtOrder 无 stock_name）"""
    try:
        return {
            "order_id": str(getattr(raw, "order_id", "")),
            "order_sysid": getattr(raw, "order_sysid", "") or "",
            "account_id": getattr(raw, "account_id", ""),
            "stock_code": getattr(raw, "stock_code", ""),
            "order_type": "buy" if getattr(raw, "order_type", 0) == 23 else "sell",
            "order_volume": int(getattr(raw, "order_volume", 0)),
            "traded_volume": int(getattr(raw, "traded_volume", 0)),
            "price_type": int(getattr(raw, "price_type", 0)),
            "price": float(getattr(raw, "price", 0)),
            "traded_price": float(getattr(raw, "traded_price", 0)),
            "status": ORDER_STATUS_MAP.get(getattr(raw, "order_status", -1), "UNKNOWN"),
            "status_msg": getattr(raw, "status_msg", "") or "",
            "order_time": getattr(raw, "order_time", "") or "",
            "strategy_name": getattr(raw, "strategy_name", "") or "",
            "order_remark": getattr(raw, "order_remark", "") or "",
            "offset_flag": int(getattr(raw, "offset_flag", 0)),
            "updated_at": time.time(),
        }
    except Exception:
        return {}


def _normalize_trade(raw) -> dict:
    """标准化 xtquant 成交数据（XtTrade 无 stock_name，有 commission）"""
    try:
        return {
            "traded_id": getattr(raw, "traded_id", "") or "",
            "order_id": str(getattr(raw, "order_id", "")),
            "order_sysid": getattr(raw, "order_sysid", "") or "",
            "account_id": getattr(raw, "account_id", ""),
            "stock_code": getattr(raw, "stock_code", ""),
            "order_type": "buy" if getattr(raw, "order_type", 0) == 23 else "sell",
            "traded_volume": int(getattr(raw, "traded_volume", 0)),
            "traded_price": float(getattr(raw, "traded_price", 0)),
            "traded_amount": float(getattr(raw, "traded_amount", 0)),
            "traded_time": getattr(raw, "traded_time", "") or "",
            "strategy_name": getattr(raw, "strategy_name", "") or "",
            "order_remark": getattr(raw, "order_remark", "") or "",
            "commission": float(getattr(raw, "commission", 0)),
        }
    except Exception:
        return {}


class AccountHub:
    """
    账户状态管理：事件驱动 + 轮询兜底。
    所有内存写操作通过 threading.Lock 保护。
    """

    def __init__(self, trade_hub, redis_bridge, config: dict, executor=None):
        self._trader = trade_hub
        self._redis = redis_bridge
        self._config = config
        self._lock = threading.Lock()
        self._executor = executor  # 可选：主线程池，避免每次 full_sync 创建新池

        # 账户维度快照
        self._asset: dict = {}  # account_id → asset dict
        self._positions: dict = {}  # account_id → {stock_code: position dict}
        self._orders: dict = {}  # account_id → {order_id: order dict}
        self._trades: dict = {}  # account_id → {traded_id: trade dict}
        self._account_status: dict = {}  # account_id → "online"/"offline"

        # 元信息
        self._last_full_sync_at: float = 0.0
        self._last_sync_log_at: dict = {}  # account_id → timestamp，日志频控
        self._last_asset_sync_at: float = 0.0
        self._refreshing = False

        # 运行控制
        self._running = False
        self._poll_thread = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动轮询线程"""
        self._running = True
        self._poll_thread = threading.Thread(
            target=self._poll_loop, name="AccountHub-Poll", daemon=True
        )
        self._poll_thread.start()
        logger.info("[OK] AccountHub 轮询线程已启动")

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # 事件入口（由 CallbackHandler 调用，xtquant 线程）
    # ------------------------------------------------------------------

    def apply_order_event(self, event: dict) -> None:
        """更新内存委托快照，同时刷新 Redis"""
        with self._lock:
            account_id = event["account_id"]
            order_id = event["order_id"]
            if account_id not in self._orders:
                self._orders[account_id] = {}
            self._orders[account_id][order_id] = event
            snapshot = dict(self._orders[account_id])
        self._redis.flush_orders(account_id, snapshot)

    def apply_trade_event(self, event: dict) -> None:
        """追加成交记录（traded_id 去重），同时刷新 Redis"""
        with self._lock:
            account_id = event["account_id"]
            traded_id = event["traded_id"]
            if account_id not in self._trades:
                self._trades[account_id] = {}
            if traded_id not in self._trades[account_id]:
                self._trades[account_id][traded_id] = event
            snapshot = dict(self._trades[account_id])
        self._redis.flush_trades(account_id, snapshot)

    def apply_order_error(self, event: dict) -> None:
        """委托失败：同 apply_order_event"""
        self.apply_order_event(event)

    def apply_cancel_error(self, event: dict) -> None:
        """撤单失败：同 apply_order_event"""
        self.apply_order_event(event)

    def apply_account_status(self, account_id: str, is_online: bool) -> None:
        with self._lock:
            self._account_status[account_id] = "online" if is_online else "offline"

    # ------------------------------------------------------------------
    # 主动拉取（由线程池调用，非 xtquant 线程）
    # ------------------------------------------------------------------

    def refresh_asset_and_positions(self, delay_ms: int = 0) -> None:
        """成交后触发：延迟拉取资金和持仓"""
        now = time.time()
        with self._lock:
            if self._refreshing:
                return
            if now - self._last_asset_sync_at < 0.5:
                return
            self._refreshing = True

        try:
            if delay_ms > 0:
                time.sleep(delay_ms / 1000.0)

            asset = self._trader.query_asset()
            positions = self._trader.query_positions()
            account_id = self._trader.account.account_id if self._trader.account else ""

            with self._lock:
                self._asset[account_id] = _normalize_asset(asset)
                self._positions[account_id] = {
                    _normalize_position(p).get("stock_code", ""): _normalize_position(p)
                    for p in (positions or [])
                    if _normalize_position(p).get("stock_code")
                }
                self._last_asset_sync_at = time.time()

            self._redis.flush_account_snapshot(
                account_id,
                self._asset.get(account_id, {}),
                list(self._positions.get(account_id, {}).values()),
            )
        except Exception:
            logger.error("[ERROR] refresh_asset_and_positions 失败", exc_info=True)
        finally:
            with self._lock:
                self._refreshing = False

    def full_sync(self) -> None:
        """全量拉取：资金 + 持仓 + 委托 + 成交"""
        account_id = self._trader.account.account_id if self._trader.account else ""
        if not account_id:
            logger.warning("[WARN] AccountHub full_sync: 无有效 account_id")
            return

        try:
            pool = self._executor
            own_pool = False
            if pool is None:
                pool = ThreadPoolExecutor(max_workers=4)
                own_pool = True

            try:
                future_asset = pool.submit(self._trader.query_asset)
                future_positions = pool.submit(self._trader.query_positions)
                future_orders = pool.submit(self._trader.query_orders)
                future_trades = pool.submit(self._trader.query_trades)

                asset = future_asset.result(timeout=10)
                positions = future_positions.result(timeout=10)
                orders = future_orders.result(timeout=10)
                trades = future_trades.result(timeout=10)
            finally:
                if own_pool:
                    pool.shutdown(wait=False)

            with self._lock:
                self._asset[account_id] = _normalize_asset(asset)
                self._positions[account_id] = {
                    _normalize_position(p).get("stock_code", ""): _normalize_position(p)
                    for p in (positions or [])
                    if _normalize_position(p).get("stock_code")
                }
                self._orders[account_id] = {
                    _normalize_order(o).get("order_id", ""): _normalize_order(o)
                    for o in (orders or [])
                    if _normalize_order(o).get("order_id")
                }
                self._trades[account_id] = {
                    _normalize_trade(t).get("traded_id", ""): _normalize_trade(t)
                    for t in (trades or [])
                    if _normalize_trade(t).get("traded_id")
                }
                self._last_full_sync_at = time.time()

            self._redis.flush_full_account(
                account_id,
                self._asset[account_id],
                list(self._positions[account_id].values()),
                list(self._orders[account_id].values()),
                list(self._trades[account_id].values()),
            )
            now = time.time()
            last = self._last_sync_log_at.get(account_id, 0)
            if now - last >= 300:
                logger.info("[OK] AccountHub 全量同步完成 account=%s", account_id)
                self._last_sync_log_at[account_id] = now
        except Exception:
            logger.error("[ERROR] AccountHub 全量同步失败", exc_info=True)

    # ------------------------------------------------------------------
    # 只读查询（HTTP 线程调用，加锁读内存）
    # ------------------------------------------------------------------

    def get_asset(self, account_id: str) -> dict:
        with self._lock:
            return dict(self._asset.get(account_id, {}))

    def get_positions(self, account_id: str) -> list:
        with self._lock:
            return list(self._positions.get(account_id, {}).values())

    def get_orders(self, account_id: str, cancelable_only: bool = False) -> list:
        with self._lock:
            orders = [dict(o) for o in self._orders.get(account_id, {}).values()]
            if cancelable_only:
                orders = [o for o in orders if o.get("status") in CANCELABLE_STATUSES]
            return orders

    def get_trades(self, account_id: str) -> list:
        with self._lock:
            return list(self._trades.get(account_id, {}).values())

    def get_account_status(self, account_id: str) -> str:
        with self._lock:
            return self._account_status.get(account_id, "unknown")

    def get_req_id_by_order_id(self, account_id: str, order_id: str) -> Optional[str]:
        """从内存委托快照中查找原始 order_remark 以提取 req_id"""
        with self._lock:
            order = self._orders.get(account_id, {}).get(order_id, {})
            remark = order.get("order_remark", "")
            if remark and remark.startswith("alpha:"):
                return remark[6:]
            return order.get("req_id")

    # ------------------------------------------------------------------
    # 轮询兜底
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        """每 10s 全量拉取一次，修正回调漏失的状态"""
        while self._running:
            time.sleep(10)
            if not self._running:
                break
            try:
                self.full_sync()
            except Exception:
                logger.error("[ERROR] AccountHub 轮询同步失败", exc_info=True)
