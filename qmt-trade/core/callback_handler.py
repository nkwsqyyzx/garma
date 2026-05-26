"""
CallbackHandler：XtQuantTraderCallback 事件回调中枢。
所有方法均在 xtquant 内部线程中调用，禁止执行任何阻塞 I/O。
"""

import logging
import threading
import time
import uuid
from typing import Optional

from shared.const import ORDER_STATUS_MAP

logger = logging.getLogger(__name__)

# XtQuantTraderCallback 可能不在所有环境中可用
# 使用懒加载和可选导入
try:
    from xtquant.xttrader import XtQuantTraderCallback

    _HAS_XTQUANT = True
except ImportError:
    _HAS_XTQUANT = False


    # 定义一个空基类以便开发环境使用
    class XtQuantTraderCallback:
        pass


class CallbackHandler(XtQuantTraderCallback):
    """
    XtQuantTrader 事件回调中枢。
    所有方法均在 xtquant 内部线程中调用。
    AccountHub、SessionMgr 由外部注入，实现解耦。
    """

    def __init__(self, account_hub, session_mgr, redis_bridge, executor):
        """
        Args:
            account_hub: AccountHub 实例，负责内存状态管理
            session_mgr: SessionMgr 实例，负责重连调度
            redis_bridge: RedisBridge 实例，负责 Redis 写操作
            executor: ThreadPoolExecutor，用于投递耗时后续任务
        """
        self._hub = account_hub
        self._session = session_mgr
        self._redis = redis_bridge
        self._executor = executor
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # 1. 断线回调
    # ------------------------------------------------------------------

    def on_disconnected(self) -> None:
        """
        触发时机：xttrader 与 MiniQMT 连接断开。
        处理：标记 offline → 委托 SessionMgr 异步重连。
        """
        logger.error("[ERROR] XtQuantTrader 连接断开，触发重连流程")
        try:
            self._redis.raw.set("qmt:component:xttrader", "offline", ex=60)
        except Exception:
            pass
        # 投入线程池异步执行重连，避免阻塞回调线程
        self._executor.submit(self._session.handle_disconnected)

    # ------------------------------------------------------------------
    # 2. 账户状态变化
    # ------------------------------------------------------------------

    def on_account_status(self, status) -> None:
        """
        触发时机：账户登录/登出/状态变化。
        status 字段：.account_id, .account_type, .status（0=正常）
        """
        try:
            account_id = status.account_id
            account_type = getattr(status, "account_type", "")
            status_code = status.status

            logger.info(
                "[INFO] 账户状态变化 account=%s type=%s status=%d",
                account_id, account_type, status_code
            )

            is_online = (status_code == 0)
            self._redis.set_account_online_status(account_id, is_online)
            self._hub.apply_account_status(account_id, is_online)

            if is_online:
                # 账户上线：投入线程池全量拉取
                logger.info("[INFO] 账户 %s 上线，触发全量数据拉取", account_id)
                self._executor.submit(self._hub.full_sync)

        except Exception:
            logger.error("[ERROR] on_account_status 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 3. 委托回报
    # ------------------------------------------------------------------

    def on_stock_order(self, order) -> None:
        """
        触发时机：委托状态变化（SUBMITTED/PARTIALLY_FILLED/FILLED/CANCELLED 等）。
        同一笔委托的多次状态变化会多次回调。
        """
        try:
            normalized_status = self._map_order_status(order.order_status)
            req_id = self._parse_req_id(getattr(order, "order_remark", ""))

            event = {
                "event_type": "order_update",
                "req_id": req_id,
                "order_id": str(order.order_id),
                "order_sysid": getattr(order, "order_sysid", "") or "",
                "account_id": order.account_id,
                "stock_code": order.stock_code,
                "order_type": "buy" if order.order_type == 23 else "sell",
                "order_volume": order.order_volume,
                "traded_volume": order.traded_volume,
                "price": order.price,
                "traded_price": order.traded_price,
                "status": normalized_status,
                "status_msg": getattr(order, "status_msg", "") or "",
                "order_time": getattr(order, "order_time", "") or "",
                "strategy_name": getattr(order, "strategy_name", "") or "",
                "timestamp": time.time(),
            }

            # 1. 更新内存委托快照（加锁）
            self._hub.apply_order_event(event)

            # 2. 更新单笔委托状态 Redis Key
            self._redis.set_order_status(req_id or str(order.order_id), event)

            # 3. 发布到回报 Stream
            self._redis.publish_order_event(event)

            logger.info(
                "[INFO] 委托回报 order_id=%d stock=%s status=%s traded=%d/%d",
                order.order_id, order.stock_code, normalized_status,
                order.traded_volume, order.order_volume
            )

        except Exception:
            logger.error("[ERROR] on_stock_order 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 4. 成交回报
    # ------------------------------------------------------------------

    def on_stock_trade(self, trade) -> None:
        """
        触发时机：每发生一笔实际成交。
        与 on_stock_order 的区别：推送每笔实际成交明细。
        """
        try:
            req_id = self._parse_req_id(getattr(trade, "order_remark", ""))

            event = {
                "event_type": "trade",
                "req_id": req_id,
                "order_id": str(trade.order_id),
                "traded_id": trade.traded_id or "",
                "account_id": trade.account_id,
                "stock_code": trade.stock_code,
                "order_type": "buy" if trade.order_type == 23 else "sell",
                "traded_volume": trade.traded_volume,
                "traded_price": trade.traded_price,
                "traded_amount": trade.traded_amount,
                "traded_time": trade.traded_time or "",
                "strategy_name": getattr(trade, "strategy_name", "") or "",
                "timestamp": time.time(),
            }

            # 1. 更新内存成交列表（traded_id 去重）
            self._hub.apply_trade_event(event)

            # 2. 发布到回报 Stream
            self._redis.publish_order_event(event)

            # 3. 成交后延迟 200ms 拉取持仓和资金
            self._executor.submit(
                self._hub.refresh_asset_and_positions, 200
            )

            logger.info(
                "[INFO] 成交回报 order_id=%d traded_id=%s stock=%s vol=%d price=%.2f",
                trade.order_id, trade.traded_id, trade.stock_code,
                trade.traded_volume, trade.traded_price
            )

        except Exception:
            logger.error("[ERROR] on_stock_trade 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 5. 下单失败
    # ------------------------------------------------------------------

    def on_order_error(self, order_error) -> None:
        """触发时机：委托被交易所/柜台拒绝"""
        try:
            req_id = self._parse_req_id(getattr(order_error, "order_remark", ""))
            # 兜底1：从内存委托快照中通过 order_id 反查 req_id
            if not req_id:
                req_id = self._hub.get_req_id_by_order_id(
                    order_error.account_id, str(order_error.order_id)
                )
            # 兜底2：从 Redis 映射 local_order_id → req_id（cmd_consumer 写入）
            if not req_id:
                cached = self._redis.raw.get(f"qmt:order:req_by_local:{order_error.order_id}")
                if cached:
                    req_id = cached if isinstance(cached, str) else cached.decode()

            event = {
                "event_type": "order_error",
                "req_id": req_id,
                "order_id": str(order_error.order_id),
                "account_id": order_error.account_id,
                "stock_code": getattr(order_error, "stock_code", ""),
                "status": "REJECTED",
                "error_id": order_error.error_id,
                "error_msg": order_error.error_msg or "",
                "timestamp": time.time(),
            }

            self._hub.apply_order_error(event)
            self._redis.set_order_status(req_id or str(order_error.order_id), event)
            self._redis.publish_order_event(event)

            logger.error(
                "[ERROR] 下单失败 order_id=%d stock=%s error_id=%d msg=%s",
                order_error.order_id,
                getattr(order_error, "stock_code", ""),
                order_error.error_id, order_error.error_msg
            )

        except Exception:
            logger.error("[ERROR] on_order_error 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 6. 撤单失败
    # ------------------------------------------------------------------

    def on_cancel_error(self, cancel_error) -> None:
        """触发时机：发起撤单但被拒绝"""
        try:
            order_id = str(cancel_error.order_id)
            req_id = self._hub.get_req_id_by_order_id(
                cancel_error.account_id, order_id
            )

            event = {
                "event_type": "cancel_error",
                "req_id": req_id,
                "order_id": order_id,
                "account_id": cancel_error.account_id,
                "status": "CANCEL_FAILED",
                "error_id": cancel_error.error_id,
                "error_msg": cancel_error.error_msg or "",
                "timestamp": time.time(),
            }

            self._hub.apply_cancel_error(event)
            self._redis.publish_order_event(event)

            logger.error(
                "[ERROR] 撤单失败 order_id=%d error_id=%d msg=%s",
                cancel_error.order_id, cancel_error.error_id, cancel_error.error_msg
            )

        except Exception:
            logger.error("[ERROR] on_cancel_error 处理异常", exc_info=True)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    @staticmethod
    def _map_order_status(xt_status: int) -> str:
        """将 xtquant 状态码映射到系统规范状态字符串"""
        return ORDER_STATUS_MAP.get(xt_status, f"UNKNOWN_{xt_status}")

    @staticmethod
    def _parse_req_id(order_remark: Optional[str]) -> Optional[str]:
        """
        从 order_remark 中提取 req_id。
        约定：下单时 order_remark 格式为 "alpha:{req_id}"
        - req_id 可能为 UUID 格式 (36 字符含 4 个连字符)
        - 也可能为 alpha_xxx_yyy 格式 (非 UUID)
        - 注意：QMT 券商端会截断 order_remark，导致 req_id 不完整，
          此时返回截断后的值，由下游通过前缀匹配定位完整 req_id
        """
        if not order_remark:
            return None
        candidate = None
        if order_remark.startswith("alpha:"):
            candidate = order_remark[6:]
        elif len(order_remark) == 36 and order_remark.count("-") == 4:
            candidate = order_remark  # 兼容旧格式 (纯 UUID)
        if candidate:
            # UUID 格式：完整匹配
            try:
                uuid.UUID(candidate, version=4)
                return candidate
            except ValueError:
                pass
            # alpha_xxx 格式：可能是完整的也可能是被 QMT 截断的
            if candidate.startswith("alpha_"):
                return candidate
        return None
