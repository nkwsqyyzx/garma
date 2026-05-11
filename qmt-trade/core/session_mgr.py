"""
SessionMgr：xttrader 会话管理，重连 + 指数退避策略。
触发时机：on_disconnected 回调 → executor.submit(handle_disconnected)
"""

import logging
import threading
import time

logger = logging.getLogger(__name__)

# 退避参数
_INITIAL_BACKOFF = 1.0  # 首次重连等待 1s
_MAX_BACKOFF = 60.0  # 最大等待 60s
_BACKOFF_MULTIPLIER = 2.0  # 每次翻倍


class SessionMgr:
    """
    xttrader 会话管理器：
    - 指数退避重连（1s → 2s → 4s → ... → 60s 上限）
    - 重连后自动注册回调 + 触发全量数据拉取
    """

    def __init__(self, trade_hub, callback_handler=None, account_hub=None):
        """
        Args:
            trade_hub: TradeHub 实例
            callback_handler: CallbackHandler 实例（重连后重新注册）
            account_hub: AccountHub 实例（重连后触发 full_sync）
        """
        self._hub = trade_hub
        self._callback_handler = callback_handler
        self._account_hub = account_hub
        self._running = True
        self._reconnecting = False
        self._reconnect_count = 0
        self._lock = threading.Lock()

    @property
    def reconnecting(self) -> bool:
        return self._reconnecting

    @property
    def reconnect_count(self) -> int:
        return self._reconnect_count

    def stop(self) -> None:
        self._running = False

    def set_post_reconnect_deps(self, callback_handler, account_hub) -> None:
        """在所有组件初始化完成后调用，注入重连后需要的依赖"""
        self._callback_handler = callback_handler
        self._account_hub = account_hub

    def handle_disconnected(self) -> None:
        """
        处理断线重连。由 CallbackHandler.on_disconnected 通过线程池调用。
        使用指数退避策略，避免无限快速重连。
        """
        with self._lock:
            if self._reconnecting:
                logger.debug("[DEBUG] 重连已在进行中，跳过")
                return
            self._reconnecting = True

        backoff = _INITIAL_BACKOFF

        try:
            while self._running:
                logger.info(
                    "[INFO] 尝试重连 xttrader（第 %d 次，等待 %.1fs）...",
                    self._reconnect_count + 1, backoff
                )

                time.sleep(backoff)

                if not self._running:
                    break

                # 断开旧连接
                self._hub.disconnect()

                # 尝试重连
                if self._hub.connect():
                    self._reconnect_count += 1

                    # 重连成功后重新注册回调
                    if self._callback_handler:
                        self._hub.register_callback(self._callback_handler)

                    # 重连成功后触发全量数据拉取
                    if self._account_hub:
                        try:
                            self._account_hub.full_sync()
                        except Exception:
                            logger.error("[ERROR] 重连后 full_sync 失败", exc_info=True)

                    logger.info(
                        "[OK] xttrader 重连成功（累计重连 %d 次）",
                        self._reconnect_count
                    )
                    return

                # 重连失败，增加退避
                backoff = min(backoff * _BACKOFF_MULTIPLIER, _MAX_BACKOFF)
                self._reconnect_count += 1

        finally:
            with self._lock:
                self._reconnecting = False

        logger.error("[ERROR] xttrader 重连循环退出（服务已停止）")
