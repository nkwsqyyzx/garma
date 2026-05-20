"""
Redis 桥接层：统一封装所有 Redis 写操作。
线程安全的同步 redis.Redis 连接，供两个服务共享。
"""

import json
import logging
import time
from typing import Optional

import redis

from .const import (
    KEY_CMD_QUEUE,
    KEY_CMD_QUEUE_BACKUP,
    KEY_CMD_DLQ,
    KEY_CMD_DELAY_QUEUE,
    KEY_ORDER_STATUS,
    KEY_EVENT_ORDER_UPDATE,
    KEY_ACCOUNT_ASSET,
    KEY_ACCOUNT_POSITIONS,
    KEY_ACCOUNT_ORDERS,
    KEY_ACCOUNT_TRADES,
    KEY_ACCOUNT_ONLINE,
    KEY_MARKET_STATUS,
    KEY_TRADE_STATUS,
    KEY_STATUS_NOTIFY,
    KEY_STATUS_ALERTS,
    STATUS_TTL_SECONDS,
    ALERT_HISTORY_MAX,
)

logger = logging.getLogger(__name__)


class RedisBridge:
    """
    所有对 Redis 的写操作集中于此，便于测试 mock 和监控。
    使用同步连接（线程安全），适合在 xtquant 回调线程中使用。
    """

    def __init__(self, config: dict):
        redis_cfg = config.get("redis", {})
        self._redis = redis.Redis(
            host=redis_cfg.get("host", "192.168.3.80"),
            port=redis_cfg.get("port", 6379),
            db=redis_cfg.get("db", 0),
            password=redis_cfg.get("password") or None,
            socket_timeout=redis_cfg.get("socket_timeout", 3),
            socket_connect_timeout=redis_cfg.get("socket_connect_timeout", 3),
            retry_on_timeout=redis_cfg.get("retry_on_timeout", True),
            health_check_interval=redis_cfg.get("health_check_interval", 30),
            decode_responses=True,
        )
        self._config = config

    @property
    def raw(self) -> redis.Redis:
        """暴露底层 Redis 连接，供特殊操作使用"""
        return self._redis

    # ------------------------------------------------------------------
    # 基础工具
    # ------------------------------------------------------------------

    def ping_latency_ms(self) -> Optional[float]:
        """执行 PING 并返回延迟毫秒数；连接失败返回 None"""
        try:
            t0 = time.monotonic()
            self._redis.ping()
            return (time.monotonic() - t0) * 1000
        except redis.RedisError:
            return None

    def is_connected(self) -> bool:
        return self.ping_latency_ms() is not None

    def get_json(self, key: str) -> Optional[dict]:
        try:
            raw = self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except (redis.RedisError, json.JSONDecodeError):
            return None

    def llen(self, key: str) -> int:
        try:
            return self._redis.llen(key)
        except redis.RedisError:
            return -1

    # ------------------------------------------------------------------
    # 交易命令队列
    # ------------------------------------------------------------------

    def push_cmd(self, cmd: dict) -> None:
        """LPUSH 写入交易命令队列"""
        try:
            self._redis.lpush(KEY_CMD_QUEUE, json.dumps(cmd, ensure_ascii=False))
        except redis.RedisError:
            logger.error("[ERROR] push_cmd 失败", exc_info=True)

    def consume_cmd(self, timeout: int = 5) -> Optional[tuple]:
        """
        BRPOPLPUSH 弹出命令到备份队列。
        Returns: (cmd_dict, cmd_raw_string) or None
        """
        try:
            raw = self._redis.brpoplpush(
                KEY_CMD_QUEUE, KEY_CMD_QUEUE_BACKUP, timeout=timeout
            )
            if raw is None:
                return None
            cmd = json.loads(raw)
            return (cmd, raw)
        except (redis.RedisError, json.JSONDecodeError):
            return None

    def ack_cmd(self, cmd_raw: str) -> None:
        """命令处理成功，从备份队列删除"""
        try:
            self._redis.lrem(KEY_CMD_QUEUE_BACKUP, 1, cmd_raw)
        except redis.RedisError:
            logger.error("[ERROR] ack_cmd 失败", exc_info=True)

    def nack_cmd(self, cmd: dict, cmd_raw: str, reason: str) -> None:
        """命令处理失败，重入队列或转死信。cmd_raw 为 BRPOPLPUSH 返回的原始字符串。"""
        try:
            retry_count = cmd.get("retry_count", 0) + 1
            cmd["retry_count"] = retry_count
            cmd["last_error"] = reason
            cmd["last_error_at"] = time.time()
            cmd_json = json.dumps(cmd, ensure_ascii=False)

            # 先从备份队列删除（使用原始字符串匹配）
            self._redis.lrem(KEY_CMD_QUEUE_BACKUP, 1, cmd_raw)

            if retry_count >= 3:
                # 转死信队列
                self._redis.lpush(KEY_CMD_DLQ, cmd_json)
                logger.error(
                    "[ERROR] 命令转死信 req_id=%s retries=%d reason=%s",
                    cmd.get("req_id"), retry_count, reason
                )
            else:
                # 写入延迟队列，指数退避
                delay = retry_count * 2  # 2s → 4s → 6s
                score = time.time() + delay
                self._redis.zadd(KEY_CMD_DELAY_QUEUE, {cmd_json: score})
                logger.warning(
                    "[WARN] 命令重入延迟队列 req_id=%s retry=%d delay=%ds",
                    cmd.get("req_id"), retry_count, delay
                )
        except redis.RedisError:
            logger.error("[ERROR] nack_cmd 失败", exc_info=True)

    def recover_delayed_cmds(self) -> int:
        """轮询延迟队列，到期命令重新入主队列，返回恢复数量"""
        try:
            now = time.time()
            items = self._redis.zrangebyscore(KEY_CMD_DELAY_QUEUE, 0, now)
            if not items:
                return 0
            for item in items:
                self._redis.lpush(KEY_CMD_QUEUE, item)
                self._redis.zrem(KEY_CMD_DELAY_QUEUE, item)
            return len(items)
        except redis.RedisError:
            return 0

    def recover_backup_cmds(self) -> int:
        """冷启动时从备份队列恢复未确认命令。
        带恢复计数：超过 1 次恢复的命令转入 DLQ，防止崩溃死循环。
        """
        try:
            items = self._redis.lrange(KEY_CMD_QUEUE_BACKUP, 0, -1)
            if not items:
                return 0
            recovered = 0
            for item in items:
                try:
                    cmd = json.loads(item)
                    recovery_count = cmd.get("_recovery_count", 0) + 1
                    if recovery_count > 1:
                        cmd["_recovery_count"] = recovery_count
                        cmd["_dlq_reason"] = "多次恢复失败（可能触发进程崩溃）"
                        self._redis.lpush(KEY_CMD_DLQ,
                                          json.dumps(cmd, ensure_ascii=False))
                        logger.error(
                            "[ERROR] 命令多次恢复失败转入死信 req_id=%s recoveries=%d",
                            cmd.get("req_id"), recovery_count)
                        continue
                    cmd["_recovery_count"] = recovery_count
                    self._redis.lpush(KEY_CMD_QUEUE,
                                      json.dumps(cmd, ensure_ascii=False))
                    recovered += 1
                except (json.JSONDecodeError, Exception):
                    self._redis.lpush(KEY_CMD_DLQ, item)
            self._redis.delete(KEY_CMD_QUEUE_BACKUP)
            return recovered
        except redis.RedisError:
            return 0

    # ------------------------------------------------------------------
    # 订单回报
    # ------------------------------------------------------------------

    def set_order_status(self, req_id: str, event: dict,
                         ttl: int = 86400) -> None:
        """更新单笔委托状态 Redis Key"""
        try:
            key = KEY_ORDER_STATUS.format(req_id=req_id)
            self._redis.set(key, json.dumps(event, ensure_ascii=False), ex=ttl)
        except redis.RedisError:
            logger.error("[ERROR] set_order_status 失败", exc_info=True)

    def get_order_status(self, req_id: str) -> Optional[dict]:
        try:
            key = KEY_ORDER_STATUS.format(req_id=req_id)
            raw = self._redis.get(key)
            return json.loads(raw) if raw else None
        except (redis.RedisError, json.JSONDecodeError):
            return None

    def publish_order_event(self, event: dict) -> None:
        """发布到订单回报 Stream"""
        try:
            fields = {k: str(v) for k, v in event.items()}
            self._redis.xadd(KEY_EVENT_ORDER_UPDATE, fields,
                             maxlen=10000, approximate=True)
        except redis.RedisError:
            logger.error("[ERROR] publish_order_event 失败", exc_info=True)

    # ------------------------------------------------------------------
    # 账户快照
    # ------------------------------------------------------------------

    def flush_account_snapshot(self, account_id: str, asset: dict,
                               positions: list) -> None:
        """原子更新账户快照（资金 + 持仓）"""
        try:
            pipe = self._redis.pipeline()
            pipe.set(KEY_ACCOUNT_ASSET,
                     json.dumps(asset, ensure_ascii=False), ex=60)
            pipe.set(KEY_ACCOUNT_POSITIONS,
                     json.dumps(positions, ensure_ascii=False), ex=60)
            pipe.execute()
        except redis.RedisError:
            logger.error("[ERROR] flush_account_snapshot 失败", exc_info=True)

    def flush_orders(self, account_id: str, orders_snapshot: dict) -> None:
        """刷新 Redis 委托列表"""
        try:
            self._redis.set(
                KEY_ACCOUNT_ORDERS,
                json.dumps(list(orders_snapshot.values()), ensure_ascii=False),
                ex=60,
            )
        except redis.RedisError:
            logger.error("[ERROR] flush_orders 失败", exc_info=True)

    def flush_trades(self, account_id: str, trades_snapshot: dict) -> None:
        """刷新 Redis 成交列表"""
        try:
            self._redis.set(
                KEY_ACCOUNT_TRADES,
                json.dumps(list(trades_snapshot.values()), ensure_ascii=False),
                ex=60,
            )
        except redis.RedisError:
            logger.error("[ERROR] flush_trades 失败", exc_info=True)

    def flush_full_account(self, account_id: str, asset: dict,
                           positions: list, orders: list,
                           trades: list) -> None:
        """全量原子写 Redis（Pipeline）"""
        try:
            pipe = self._redis.pipeline()
            pipe.set(KEY_ACCOUNT_ASSET,
                     json.dumps(asset, ensure_ascii=False), ex=60)
            pipe.set(KEY_ACCOUNT_POSITIONS,
                     json.dumps(positions, ensure_ascii=False), ex=60)
            pipe.set(KEY_ACCOUNT_ORDERS,
                     json.dumps(orders, ensure_ascii=False), ex=60)
            pipe.set(KEY_ACCOUNT_TRADES,
                     json.dumps(trades, ensure_ascii=False), ex=60)
            pipe.execute()
        except redis.RedisError:
            logger.error("[ERROR] flush_full_account 失败", exc_info=True)

    def set_account_online_status(self, account_id: str,
                                  is_online: bool) -> None:
        try:
            key = KEY_ACCOUNT_ONLINE.format(account_id=account_id)
            value = "online" if is_online else "offline"
            self._redis.set(key, value, ex=60)
        except redis.RedisError:
            logger.error("[ERROR] set_account_online_status 失败", exc_info=True)

    # ------------------------------------------------------------------
    # 状态上报
    # ------------------------------------------------------------------

    def publish_status(self, status: dict, service: str) -> None:
        """
        主动状态上报：
        1. SET qmt:{service}:status {json} EX 35
        2. PUBLISH qmt:status:notify {json}
        """
        payload = json.dumps(status, ensure_ascii=False)
        try:
            pipe = self._redis.pipeline()
            key = KEY_MARKET_STATUS if service == "market" else KEY_TRADE_STATUS
            pipe.set(key, payload, ex=STATUS_TTL_SECONDS)
            pipe.publish(KEY_STATUS_NOTIFY, payload)
            pipe.execute()
        except redis.RedisError:
            logger.error("[ERROR] publish_status 失败", exc_info=True)

    def push_alert_history(self, alert: dict) -> None:
        """写入告警历史"""
        try:
            pipe = self._redis.pipeline()
            pipe.lpush(KEY_STATUS_ALERTS, json.dumps(alert, ensure_ascii=False))
            pipe.ltrim(KEY_STATUS_ALERTS, 0, ALERT_HISTORY_MAX - 1)
            pipe.execute()
        except redis.RedisError:
            logger.error("[ERROR] push_alert_history 失败", exc_info=True)

    # ------------------------------------------------------------------
    # 读取账户快照（HTTP API 使用）
    # ------------------------------------------------------------------

    def get_account_asset(self) -> Optional[dict]:
        return self.get_json(KEY_ACCOUNT_ASSET)

    def get_account_positions(self) -> Optional[list]:
        raw = self.get_json(KEY_ACCOUNT_POSITIONS)
        return raw if isinstance(raw, list) else None

    def get_account_orders(self) -> Optional[list]:
        raw = self._redis.get(KEY_ACCOUNT_ORDERS)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (redis.RedisError, json.JSONDecodeError):
            return None

    def get_account_trades(self) -> Optional[list]:
        raw = self.get_json(KEY_ACCOUNT_TRADES)
        return raw if isinstance(raw, list) else None

    # ------------------------------------------------------------------
    # 关闭连接
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._redis.close()
        except Exception:
            pass
