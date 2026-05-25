"""资产快照 Worker：盘前/盘后自动采集资产快照到 MySQL。"""

import asyncio
from datetime import time

from loguru import logger


# 盘前采集时间窗口: 09:10 ~ 09:15（集合竞价前，取当日开盘前资产）
_PRE_MARKET_START = time(9, 10)
_PRE_MARKET_END = time(9, 14, 59)

# 盘后采集时间: 15:05 ~ 15:15
_POST_MARKET_START = time(15, 5)
_POST_MARKET_END = time(15, 15)


class AssetSnapshotWorker:
    """盘前/盘后自动采集资产快照。"""

    def __init__(self, qmt_service):
        self._svc = qmt_service
        self._running = False
        self._pre_captured = False
        self._post_captured = False
        self._last_date = None

    def start(self) -> asyncio.Task:
        self._running = True
        task = asyncio.create_task(self._run(), name="asset-snapshot")
        logger.info("AssetSnapshotWorker started")
        return task

    async def _run(self) -> None:
        """每 30 秒检查一次是否需要采集快照。"""
        from datetime import datetime

        while self._running:
            try:
                now = datetime.now()
                today = now.date()
                now_time = now.time()

                # 日期变化时重置采集标志
                if today != self._last_date:
                    self._pre_captured = False
                    self._post_captured = False
                    self._last_date = today

                # 盘前快照: 集合竞价前采集（09:10 ~ 09:15）
                if not self._pre_captured and _PRE_MARKET_START <= now_time <= _PRE_MARKET_END:
                    ok = await self._svc.save_asset_snapshot("pre_market")
                    if ok:
                        self._pre_captured = True

                # 盘后快照: 15:05 后写入
                if not self._post_captured and _POST_MARKET_START <= now_time <= _POST_MARKET_END:
                    ok = await self._svc.save_asset_snapshot("post_market")
                    if ok:
                        self._post_captured = True

            except Exception:
                logger.exception("AssetSnapshotWorker error")

            await asyncio.sleep(30)
