"""
StatusReporter：行情服务健康上报（每 10s 写 qmt:market:status）。
独立线程运行，采集 xtdata 连接状态 + Redis 连通性 + 订阅数量。
"""

import logging
import threading
import time
from datetime import datetime
from typing import Optional

import pytz

from shared.const import (
    TICK_STALE_SECONDS,
    TICK_OFFLINE_SECONDS,
    REDIS_LATENCY_WARN_MS,
)

logger = logging.getLogger(__name__)

_BEIJING_TZ = pytz.timezone("Asia/Shanghai")


class StatusReporter:
    """
    每 REPORT_INTERVAL 秒采集一次本进程的系统状态：
    - 写入 Redis qmt:market:status（TTL 自动过期，离线感知）
    - PUBLISH qmt:status:notify（两个服务共享频道）
    - 新告警写入 qmt:status:alerts（持久化历史）
    """

    REPORT_INTERVAL = 10  # 秒

    def __init__(self, redis_bridge, market_hub, config: dict,
                 notifier=None):
        """
        Args:
            redis_bridge: RedisBridge 实例
            market_hub: MarketHub 实例
            config: 完整配置 dict
            notifier: 通知接口（企业微信等），可选
        """
        self._redis = redis_bridge
        self._hub = market_hub
        self._config = config
        self._notifier = notifier
        self._start_time = time.time()
        self._running = False
        self._thread = None

        # 告警去重：记录上一次活跃告警集合
        self._active_alert_keys: set = set()

        # 最近一次状态缓存（供 /health 直接读取）
        self._latest_status: Optional[dict] = None

    @property
    def latest_status(self) -> dict:
        return self._latest_status or {"overall_status": "unknown"}

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(
            target=self._loop, name="StatusReporter", daemon=True
        )
        self._thread.start()
        logger.info("[OK] StatusReporter 已启动，上报周期 %ds", self.REPORT_INTERVAL)

    def stop(self) -> None:
        self._running = False

    def _loop(self) -> None:
        while self._running:
            try:
                self._report_once()
            except Exception:
                logger.error("[ERROR] StatusReporter 上报失败", exc_info=True)
            time.sleep(self.REPORT_INTERVAL)

    def _report_once(self) -> None:
        """采集状态 → 写 Redis → Pub/Sub 广播 → 处理告警"""
        status = self._collect()
        self._latest_status = status

        # 1. 写状态快照（TTL 自动续期）
        self._redis.publish_status(status, service="market")

        # 2. 处理本次告警
        self._handle_alerts(status.get("alerts", []))

        logger.debug(
            "[INFO] 状态上报完成 overall=%s alerts=%d",
            status["overall_status"], len(status["alerts"])
        )

    def _collect(self) -> dict:
        """采集全量状态"""
        now = time.time()
        alerts = []

        # ---- xtdata 状态 ----
        xtdata_connected = self._hub.connected
        last_tick_at = self._hub.last_tick_at
        subscribed_count = self._hub.subscribed_count
        tick_count = self._hub.tick_count

        if not xtdata_connected:
            xtdata_status = "offline"
            alerts.append(self._make_alert("xtdata_offline", "error",
                                           "行情连接断开（xtdata 未连接）"))
        elif last_tick_at > 0:
            tick_delay = now - last_tick_at
            if tick_delay > TICK_OFFLINE_SECONDS:
                xtdata_status = "offline"
                alerts.append(self._make_alert("xtdata_stale_offline", "error",
                                               f"行情超过 {TICK_OFFLINE_SECONDS}s 无推送"))
            elif tick_delay > TICK_STALE_SECONDS:
                xtdata_status = "degraded"
                alerts.append(self._make_alert("xtdata_stale", "warning",
                                               f"行情 {tick_delay:.0f}s 无推送，可能延迟"))
            else:
                xtdata_status = "healthy"
        else:
            # 启动后尚未收到任何 tick，给予 30s 宽限
            uptime = now - self._start_time
            if uptime > 60:
                xtdata_status = "degraded"
                alerts.append(self._make_alert("xtdata_no_tick", "warning",
                                               "启动后尚未收到任何行情数据"))
            else:
                xtdata_status = "healthy"

        xtdata_info = {
            "connected": xtdata_connected,
            "last_tick_at": last_tick_at if last_tick_at > 0 else None,
            "last_tick_delay_s": round(now - last_tick_at, 1) if last_tick_at > 0 else None,
            "subscribed_count": subscribed_count,
            "tick_count": tick_count,
            "status": xtdata_status,
        }

        # ---- Redis 状态 ----
        redis_latency_ms = self._redis.ping_latency_ms()
        if redis_latency_ms is None:
            redis_status = "offline"
            alerts.append(self._make_alert("redis_offline", "error", "Redis 连接失败"))
        elif redis_latency_ms > REDIS_LATENCY_WARN_MS:
            redis_status = "degraded"
            alerts.append(self._make_alert("redis_slow", "warning",
                                           f"Redis 延迟过高：{redis_latency_ms:.1f}ms"))
        else:
            redis_status = "healthy"

        redis_info = {
            "connected": redis_latency_ms is not None,
            "latency_ms": round(redis_latency_ms, 2) if redis_latency_ms is not None else None,
            "status": redis_status,
        }

        # ---- 整体状态（取最差等级）----
        component_statuses = [xtdata_status, redis_status]
        if "offline" in component_statuses:
            overall = "offline"
        elif "degraded" in component_statuses:
            overall = "degraded"
        else:
            overall = "healthy"

        return {
            "source": "market",
            "version": "1.0.0",
            "overall_status": overall,
            "server_time": datetime.now(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M:%S"),
            "timestamp": now,
            "uptime": int(now - self._start_time),
            "components": {
                "xtdata": xtdata_info,
                "redis": redis_info,
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
        """
        与上次活跃告警对比：
        - 新出现的告警：写历史 + 发通知
        - 已消除的告警：发"已恢复"通知
        """
        current_keys = {a["key"] for a in alerts}

        # 新增告警
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

        # 告警消除
        recovered = self._active_alert_keys - current_keys
        for key in recovered:
            if self._notifier:
                try:
                    self._notifier.send_qmt_alert(f"[QMT 恢复] {key} 已恢复正常")
                except Exception:
                    pass
            logger.info("[OK] 告警已消除: %s", key)

        self._active_alert_keys = current_keys
