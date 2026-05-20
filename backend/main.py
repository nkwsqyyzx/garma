"""Alpha QMT Backend 入口。

FastAPI 应用，启动/关闭生命周期管理，WebSocket 端点。
"""

import asyncio
import json
from contextlib import asynccontextmanager

import redis
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from backend.config import get_settings
from backend.database import init_db, close_db
from backend.service.qmt_service import QmtService
from backend.worker.qmt_market_worker import QmtMarketWorker
from backend.worker.qmt_order_worker import QmtOrderWorker
from backend.worker.qmt_status_worker import QmtStatusWorker


# ---------------------------------------------------------------------------
# WebSocket 广播管理器
# ---------------------------------------------------------------------------

class WebSocketManager:
    """简单的频道广播管理器。"""

    def __init__(self):
        self._channels: dict[str, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        await websocket.accept()
        if channel not in self._channels:
            self._channels[channel] = []
        self._channels[channel].append(websocket)
        logger.info("WebSocket connected: channel={} total={}", channel, len(self._channels[channel]))

    def disconnect(self, websocket: WebSocket, channel: str) -> None:
        if channel in self._channels:
            self._channels[channel] = [ws for ws in self._channels[channel] if ws is not websocket]

    async def broadcast(self, channel: str, data) -> None:
        """广播数据到指定频道的所有 WebSocket 客户端。"""
        connections = self._channels.get(channel, [])
        if not connections:
            return
        message = json.dumps({"channel": channel, "data": data}, ensure_ascii=False, default=str)
        disconnected = []
        for ws in connections:
            try:
                await ws.send_text(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws, channel)


ws_manager = WebSocketManager()


# ---------------------------------------------------------------------------
# 应用生命周期
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / Shutdown 生命周期管理。"""
    settings = get_settings()

    # 1. 创建 Redis 连接（同步 redis.Redis，Workers 共享）
    redis_client = redis.Redis.from_url(
        settings.REDIS_URL,
        decode_responses=True,
        socket_timeout=3,
        socket_connect_timeout=5,
        health_check_interval=30,
    )
    await asyncio.to_thread(redis_client.ping)
    logger.info("Redis connected: {}", settings.REDIS_URL.split("@")[-1])

    # 2. 初始化数据库
    await init_db()
    logger.info("Database initialized")

    # 3. 创建 QmtService 实例
    from backend.database import async_session_factory
    qmt_service = QmtService(
        config=settings,
        redis_client=redis_client,
        db_session_factory=async_session_factory,
    )
    app.state.qmt_service = qmt_service

    # 4. 启动 Workers
    worker_tasks = []

    if settings.QMT_MARKET_ENABLED:
        market_worker = QmtMarketWorker(
            redis_client=redis_client,
            ws_broadcaster=ws_manager.broadcast,
        )
        worker_tasks.append(market_worker.start())

    order_worker = QmtOrderWorker(
        redis_client=redis_client,
        qmt_service=qmt_service,
    )
    worker_tasks.append(order_worker.start())

    status_worker = QmtStatusWorker(
        redis_client=redis_client,
        ws_broadcaster=ws_manager.broadcast,
    )
    worker_tasks.extend(status_worker.start())

    logger.info("All workers started ({} tasks)", len(worker_tasks))

    yield

    # --- Shutdown ---
    # 1. 停止 Workers
    for task in worker_tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*worker_tasks, return_exceptions=True)
    logger.info("All workers stopped")

    # 2. 关闭 QmtService
    await qmt_service.close()

    # 3. 关闭 Redis
    redis_client.close()
    logger.info("Redis closed")

    # 4. 关闭数据库
    await close_db()
    logger.info("Database closed")


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Alpha QMT Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
from backend.api.qmt import router as qmt_router
app.include_router(qmt_router, prefix="/api/v1")


# ---------------------------------------------------------------------------
# WebSocket 端点
# ---------------------------------------------------------------------------

@app.websocket("/ws/qmt-status")
async def ws_qmt_status(websocket: WebSocket):
    """QMT 状态 WebSocket 端点。"""
    await ws_manager.connect(websocket, "qmt_status")
    try:
        while True:
            # 保持连接，接收客户端心跳
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "qmt_status")


@app.websocket("/ws/qmt-tick")
async def ws_qmt_tick(websocket: WebSocket):
    """QMT 行情 Tick WebSocket 端点。"""
    await ws_manager.connect(websocket, "qmt_tick")
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, "qmt_tick")


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=settings.BACKEND_PORT,
        reload=False,
        log_level="info",
    )
