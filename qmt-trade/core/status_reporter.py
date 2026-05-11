"""
StatusReporter：交易服务健康上报（每 10s 写 qmt:trade:status）。
采集 xttrader 连接状态 + 账户状态 + CmdConsumer 状态 + Redis 连通性。
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import pytz

from shared.const import (
    REDIS_LATENCY_WARN_MS,
)

logger = logging.getLogger(__name__)

_BEIJING_TZ = pytz.timezone("Asia/Shanghai")


class StatusReporter:
    """
    每 REPORT_INTERVAL 秒采集一次交易进程状态：
    - 写入 Redis qmt:trade:status（TTL 自动过期）
    - PUBLISH qmt:status:notify
    - 新告警写入 qmt:status:alerts
    """

    REPORT_INTERVAL = 10

    def __init__(self, redis_bridge, trade_hub, account_hub,
                 cmd_consumer, config: dict, notifier=None):
        self._redis = redis_bridge
        self._hub = trade_hub
        self._account = account_hub
        self._consumer = cmd_consumer
        self._config = config
        self._notifier = notifier
        self._start_time = time.time()
        self._running = False
        self._thread = None

        self._active_alert_keys: set = set()
        self._latest_status: Optional[dict] = None

    @property
    def latest_status(self) -> dict:
        return self._latest_status or {"overall_status": "unknown"}

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="TradeStatusReporter", daemon=True
        )
        self._thread.start()
        logger.info("[OK] Trade StatusReporter 已启动，上报周期 %ds", self.REPORT_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self._report_once()
            except Exception:
                logger.error("[ERROR] Trade StatusReporter 上报失败", exc_info=True)
            time.sleep(self.REPORT_INTERVAL)

    def _report_once(self) -> None:
        status = self._collect()
        self._latest_status = status
        self._redis.publish_status(status, service="trade")
        self._handle_alerts(status.get("alerts", []))

    def _collect(self) -> dict:
        now = time.time()
        alerts = []

        # ---- xttrader 状态 ----
        trader_connected = self._hub.connected
        account_id = self._config.get("qmt", {}).get("account_id", "")
        account_online = self._account.get_account_status(account_id)
        last_order_at = self._hub.last_order_at

        if not trader_connected:
            xttrader_status = "offline"
            alerts.append(self._make_alert("xttrader_offline", "error",
                                           "交易连接断开（xttrader 未连接）"))
        elif account_online != "online":
            xttrader_status = "degraded"
            alerts.append(self._make_alert("account_offline", "warning",
                                           f"账户状态异常：{account_online}"))
        else:
            xttrader_status = "healthy"

        xttrader_info = {
            "connected": trader_connected,
            "account_id": account_id,
            "account_status": account_online,
            "last_order_at": last_order_at if last_order_at > 0 else None,
            "status": xttrader_status,
        }

        # ---- Redis 状态 ----
        redis_latency_ms = self._redis.ping_latency_ms()
        cmd_queue_depth = self._redis.llen("qmt:cmd:queue")
        dlq_depth = self._redis.llen("qmt:cmd:dlq")

        if redis_latency_ms is None:
            redis_status = "offline"
            alerts.append(self._make_alert("redis_offline", "error", "Redis 连接失败"))
        elif redis_latency_ms > REDIS_LATENCY_WARN_MS:
            redis_status = "degraded"
            alerts.append(self._make_alert("redis_slow", "warning",
                                           f"Redis 延迟过高：{redis_latency_ms:.1f}ms"))
        else:
            redis_status = "healthy"

        if dlq_depth > 0:
            alerts.append(self._make_alert("dlq_has_items", "error",
                                           f"死信队列积压 {dlq_depth} 条，需人工处理"))

        redis_info = {
            "connected": redis_latency_ms is not None,
            "latency_ms": round(redis_latency_ms, 2) if redis_latency_ms is not None else None,
            "cmd_queue_depth": cmd_queue_depth,
            "dlq_depth": dlq_depth,
            "status": redis_status,
        }

        # ---- CmdConsumer 状态 ----
        consumer_alive = self._consumer.is_alive
        last_consumed_at = self._consumer.last_consumed_timestamp
        processed_today = self._consumer.processed_count_today

        if not consumer_alive:
            consumer_status = "offline"
            alerts.append(self._make_alert("consumer_dead", "error",
                                           "CmdConsumer 线程已停止"))
        else:
            consumer_status = "healthy"

        consumer_info = {
            "running": consumer_alive,
            "last_consumed_at": last_consumed_at,
            "processed_today": processed_today,
            "status": consumer_status,
        }

        # ---- 整体状态 ----
        component_statuses = [xttrader_status, redis_status, consumer_status]
        if "offline" in component_statuses:
            overall = "offline"
        elif "degraded" in component_statuses:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "source": "trade",
            "version": "1.0.0",
            "overall_status": overall,
            "server_time": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now,
            "uptime": int(now - self._start_time),
            "components": {
                "xttrader": xttrader_info,
                "redis": redis_info,
                "cmd_consumer": consumer_info,
            },
            "alerts": alerts,
        }

    def _make_alert(self, key: str, level: str, msg: str) -> dict:
        return {
            "key": key,
            "level": level,
            "msg": msg,
            "timestamp": time.time(),
            "server_time": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        }

    def _handle_alerts(self, alerts: list) -> None:
        current_keys = {a["key"] for a in alerts}

        for alert in alerts:
            if alert["key"] not in self._active_alert_keys:
                self._redis.push_alert_history(alert)
                if self._notifier and alert["level"] == "error":
                    try:
                        self._notifier.send_qmt_alert(
                            f"[QMT 告警] {alert['msg']}\n时间：{alert['server_time']}"
                        )
                    except Exception:
                        pass
                logger.warning("[ALERT] %s: %s", alert["level"].upper(), alert["msg"])

        recovered = self._active_alert_keys - current_keys
        for key in recovered:
            if self._notifier:
                try:
                    self._notifier.send_qmt_alert(f"[QMT 恢复] {key} 已恢复正常")
                except Exception:
                    pass
            logger.info("[OK] 告警已消除: %s", key)

        self._active_alert_keys = current_keys
