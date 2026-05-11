"""
CmdConsumer：BRPOPLPUSH 可靠队列消费（Redis → xttrader）。
独立线程运行，消费 qmt:cmd:queue 中的交易命令。
"""

import logging
import threading
import time
from datetime import date
from typing import Optional

from shared.const import CANCELABLE_STATUSES, KEY_KILL_SWITCH

logger = logging.getLogger(__name__)


class CmdConsumer:
    """
    可靠命令队列消费器：
    - BRPOPLPUSH qmt:cmd:queue → qmt:cmd:queue:backup
    - 成功：LREM 备份
    - 失败：指数退避重试（延迟队列），3 次后转死信
    - 独立线程轮询延迟队列，到期命令重回主队列
    - kill_switch 检查：熔断激活时不处理新命令
    """

    def __init__(self, trade_hub, redis_bridge, config: dict):
        self._trader = trade_hub
        self._redis = redis_bridge
        self._config = config
        self._trade_cfg = config.get("trade", {})

        self._running = False
        self._thread = None
        self._delay_thread = None

        # 消费统计
        self._last_consumed_at: float = 0.0
        self._processed_today: int = 0
        self._today_str: str = ""

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def last_consumed_timestamp(self) -> Optional[float]:
        return self._last_consumed_at if self._last_consumed_at > 0 else None

    @property
    def processed_count_today(self) -> int:
        self._reset_daily_count_if_needed()
        return self._processed_today

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def start(self) -> None:
        """启动消费线程 + 延迟队列轮询线程"""
        self._running = True

        self._thread = threading.Thread(
            target=self._consume_loop, name="CmdConsumer", daemon=True
        )
        self._thread.start()

        self._delay_thread = threading.Thread(
            target=self._delay_loop, name="CmdConsumer-Delay", daemon=True
        )
        self._delay_thread.start()

        logger.info("[OK] CmdConsumer 已启动")

    def stop(self) -> None:
        """停止消费"""
        self._running = False

    def join(self, timeout: float = 15.0) -> None:
        """等待消费线程结束"""
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        if self._delay_thread and self._delay_thread.is_alive():
            self._delay_thread.join(timeout=5)

    # ------------------------------------------------------------------
    # 主消费循环
    # ------------------------------------------------------------------

    def _consume_loop(self) -> None:
        """BRPOPLPUSH 阻塞等待 + 执行命令"""
        timeout = self._trade_cfg.get("cmd_queue_timeout", 5)

        while self._running:
            try:
                result = self._redis.consume_cmd(timeout=timeout)
                if result is None:
                    continue

                cmd, cmd_raw = result
                self._process_command(cmd, cmd_raw)

            except Exception:
                logger.error("[ERROR] CmdConsumer 消费异常", exc_info=True)
                time.sleep(1)

    def _process_command(self, cmd: dict, cmd_raw: str) -> None:
        """处理单条命令"""
        req_id = cmd.get("req_id", "unknown")
        cmd_type = cmd.get("cmd", "")

        logger.info(
            "[INFO] 消费命令 cmd=%s req_id=%s retry=%d",
            cmd_type, req_id, cmd.get("retry_count", 0)
        )

        # kill_switch 检查
        try:
            kill = self._redis.raw.get(KEY_KILL_SWITCH)
            if kill and kill == "1":
                logger.warning("[WARN] kill_switch 已激活，拒绝命令 req_id=%s", req_id)
                self._redis.nack_cmd(cmd, cmd_raw, "kill_switch 激活，命令被拒绝")
                return
        except Exception:
            pass

        try:
            if cmd_type == "place_order":
                order_id = self._trader.place_order(cmd)
                if order_id is not None:
                    # 下单成功
                    self._redis.ack_cmd(cmd_raw)
                    # 写入初始状态到 Redis
                    event = {
                        "event_type": "order_submitted",
                        "req_id": req_id,
                        "order_id": str(order_id),
                        "account_id": cmd.get("account_id", ""),
                        "stock_code": cmd.get("stock_code", ""),
                        "order_type": cmd.get("order_type", ""),
                        "order_volume": cmd.get("order_volume", 0),
                        "price": cmd.get("price", 0),
                        "status": "SUBMITTED",
                        "timestamp": time.time(),
                    }
                    self._redis.set_order_status(req_id, event)
                    self._redis.publish_order_event(event)
                else:
                    # 下单失败
                    self._redis.nack_cmd(cmd, cmd_raw, "xttrader.order_stock_async 返回失败")

            elif cmd_type == "cancel_order":
                order_id_str = cmd.get("order_id", "")
                try:
                    order_id_int = int(order_id_str)
                except (ValueError, TypeError):
                    self._redis.nack_cmd(cmd, cmd_raw, f"无效的 order_id: {order_id_str}")
                    return

                success = self._trader.cancel_order(order_id_int)
                if success:
                    self._redis.ack_cmd(cmd_raw)
                else:
                    self._redis.nack_cmd(cmd, cmd_raw, "xttrader.cancel_order_stock_async 返回失败")

            elif cmd_type == "cancel_all":
                # 撤销全部可撤委托
                self._cancel_all_orders(cmd)
                self._redis.ack_cmd(cmd_raw)

            else:
                logger.error("[ERROR] 未知命令类型: %s", cmd_type)
                self._redis.nack_cmd(cmd, cmd_raw, f"未知命令类型: {cmd_type}")

            # 更新统计
            self._last_consumed_at = time.time()
            self._reset_daily_count_if_needed()
            self._processed_today += 1

        except Exception as e:
            logger.error("[ERROR] 命令处理异常 req_id=%s", req_id, exc_info=True)
            self._redis.nack_cmd(cmd, cmd_raw, str(e))

    def _cancel_all_orders(self, cmd: dict) -> None:
        """撤销全部可撤委托"""
        account_id = cmd.get("account_id", "")
        if not account_id:
            return

        # 从 Redis 读取当前委托列表
        orders = self._redis.get_account_orders()
        if not orders:
            return

        cancelable = CANCELABLE_STATUSES
        cancelled_count = 0
        for order in orders:
            if order.get("status") in cancelable:
                order_id_str = order.get("order_id", "")
                try:
                    order_id_int = int(order_id_str)
                    if self._trader.cancel_order(order_id_int):
                        cancelled_count += 1
                except (ValueError, TypeError):
                    pass

        logger.info("[OK] 批量撤单完成 cancelled=%d", cancelled_count)

    # ------------------------------------------------------------------
    # 延迟队列轮询
    # ------------------------------------------------------------------

    def _delay_loop(self) -> None:
        """每秒轮询延迟队列，到期命令重回主队列"""
        while self._running:
            try:
                self._redis.recover_delayed_cmds()
            except Exception:
                logger.error("[ERROR] 延迟队列轮询异常", exc_info=True)
            time.sleep(1)

    # ------------------------------------------------------------------
    # 统计辅助
    # ------------------------------------------------------------------

    def _reset_daily_count_if_needed(self) -> None:
        """每日零点重置计数"""
        today = str(date.today())
        if today != self._today_str:
            self._today_str = today
            self._processed_today = 0
