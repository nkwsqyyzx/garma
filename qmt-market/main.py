"""
qmt-market 行情服务启动入口。
FastAPI :8091 + MarketHub 后台线程 + StatusReporter 定时上报。

启动方式: python qmt-market/main.py
"""

import json
import logging
import sys
from pathlib import Path

# ---- sys.path 设置 ----
# 1. 项目根目录（qmt-server/）→ 使 shared 模块可被 import
_BASE_DIR = Path(__file__).resolve().parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# 2. 本服务目录（qmt-market/）→ 使 core / api 模块可被 import
_MARKET_DIR = Path(__file__).resolve().parent
if str(_MARKET_DIR) not in sys.path:
    sys.path.insert(0, str(_MARKET_DIR))

import uvicorn
from fastapi import FastAPI, Request

from shared.redis_bridge import RedisBridge
from core.market_hub import MarketHub
from core.status_reporter import StatusReporter
from api.router import create_router

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# 配置加载
# ------------------------------------------------------------------

def load_config() -> dict:
    """加载配置文件：config.json + config.secret.json（合并）"""
    config_path = _BASE_DIR / "config.json"
    secret_path = _BASE_DIR / "config.secret.json"

    config = {}
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)

    if secret_path.exists():
        with open(secret_path, "r", encoding="utf-8") as f:
            secret = json.load(f)
        _deep_merge(config, secret)

    return config


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并 override 到 base"""
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


# ------------------------------------------------------------------
# WebSocket 推送（行情服务 :8091）
# ------------------------------------------------------------------

async def _ws_quote_handler(websocket):
    """
    WebSocket 行情推送：
    客户端发送 {"action": "subscribe", "codes": ["600519.SH"]}
    服务端推送 {"type": "tick", "code": "600519.SH", "data": {...}, "ts": ...}
    """
    import asyncio
    import time

    await websocket.accept()
    subscribed_codes = set()
    hub: MarketHub = websocket.app.state.market_hub

    try:
        while True:
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                msg = json.loads(raw)
                action = msg.get("action")

                if action == "subscribe":
                    codes = msg.get("codes", [])
                    subscribed_codes.update(codes)
                    await websocket.send_json({
                        "type": "subscribed",
                        "codes": list(subscribed_codes),
                    })
                elif action == "unsubscribe":
                    codes = msg.get("codes", [])
                    subscribed_codes -= set(codes)
                    await websocket.send_json({
                        "type": "unsubscribed",
                        "codes": list(subscribed_codes),
                    })
            except asyncio.TimeoutError:
                # 发心跳
                await websocket.send_json({"type": "ping", "ts": time.time()})
                continue

            # 推送订阅的行情
            if subscribed_codes:
                ticks = hub.get_ticks_batch(list(subscribed_codes))
                now = time.time()
                for code, tick in ticks.items():
                    await websocket.send_json({
                        "type": "tick",
                        "code": code,
                        "data": tick,
                        "ts": now,
                    })
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ------------------------------------------------------------------
# 控制接口
# ------------------------------------------------------------------

def _create_control_router():
    from fastapi import APIRouter
    from shared.schemas.quote import ApiResponse
    from shared.const import ErrorCode

    router = APIRouter(prefix="/control", tags=["control"])

    @router.post("/reconnect")
    async def reconnect(request: Request):
        """重连 xtdata"""
        hub: MarketHub = request.app.state.market_hub
        success = hub.reconnect()
        if success:
            return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data={"reconnected": True})
        return ApiResponse(code=ErrorCode.INTERNAL_ERROR, msg="重连失败", data={"reconnected": False})

    @router.get("/config")
    async def get_config(request: Request):
        """查看当前配置（脱敏）"""
        config = request.app.state.config
        safe = json.loads(json.dumps(config))
        if "server" in safe:
            safe["server"].pop("api_key", None)
        if "redis" in safe:
            safe["redis"].pop("password", None)
        return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=safe)

    return router


# ------------------------------------------------------------------
# 应用工厂
# ------------------------------------------------------------------

def create_app() -> FastAPI:
    config = load_config()

    app = FastAPI(
        title="QMT Market Service",
        description="A 股行情服务（xtdata → Redis Stream）",
        version="1.0.0",
    )

    # 存储到 app.state
    app.state.config = config
    app.state.redis_bridge = None
    app.state.market_hub = None
    app.state.status_reporter = None

    # 注册路由
    app.include_router(create_router())
    app.include_router(_create_control_router())

    # WebSocket 路由
    from fastapi import WebSocket

    @app.websocket("/ws/quote")
    async def ws_quote(websocket: WebSocket):
        await _ws_quote_handler(websocket)

    # ---- Startup ----
    @app.on_event("startup")
    async def startup():
        logger.info("[STARTUP] qmt-market 正在启动 ...")

        # 1. 初始化 Redis
        redis_bridge = RedisBridge(config)
        app.state.redis_bridge = redis_bridge
        logger.info("[OK] Redis 连接初始化完成")

        # 2. 初始化 MarketHub
        market_hub = MarketHub(redis_bridge, config)
        app.state.market_hub = market_hub
        market_hub.start()
        logger.info("[OK] MarketHub 启动完成")

        # 3. 初始化 StatusReporter
        reporter = StatusReporter(redis_bridge, market_hub, config)
        app.state.status_reporter = reporter
        reporter.start()
        logger.info("[OK] StatusReporter 启动完成")

        market_cfg = config.get("market_server", {})
        port = market_cfg.get("port", 8091)
        logger.info("[STARTUP] qmt-market 启动完成，监听 :%d", port)

    # ---- Shutdown（优雅关机）----
    @app.on_event("shutdown")
    async def shutdown():
        logger.info("[SHUTDOWN] qmt-market 正在关机 ...")

        # 1. 停止接受新请求（FastAPI shutdown 事件自动处理）
        # 2. 设置 _running = False，通知后台线程退出
        reporter: StatusReporter = app.state.status_reporter
        if reporter:
            reporter.stop()

        hub: MarketHub = app.state.market_hub
        if hub:
            hub.stop()

        # 3. 等待后台线程退出
        if reporter and reporter._thread and reporter._thread.is_alive():
            reporter._thread.join(timeout=5)

        # 4. 写入 shutting_down 状态（TTL=10s）
        redis_bridge: RedisBridge = app.state.redis_bridge
        if redis_bridge:
            redis_bridge.raw.set(
                "qmt:market:status",
                '{"source":"market","overall_status":"shutting_down"}',
                ex=10,
            )

        # 5. 断开 xtdata 连接（hub.stop 已处理）

        # 6. 关闭 Redis 连接池
        if redis_bridge:
            redis_bridge.close()

        logger.info("[SHUTDOWN] qmt-market 已停止")

    return app


# ------------------------------------------------------------------
# 入口
# ------------------------------------------------------------------

app = create_app()


def main():
    config = load_config()
    market_cfg = config.get("market_server", {})
    server_cfg = config.get("server", {})

    # 配置日志
    log_level = server_cfg.get("log_level", "INFO")
    log_dir = _BASE_DIR / "logs" / "market"
    log_dir.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_dir / "app.log"), encoding="utf-8"),
        ],
    )

    host = server_cfg.get("host", "0.0.0.0")
    port = market_cfg.get("port", 8091)

    logger.info("Starting qmt-market on %s:%d", host, port)
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level=log_level.lower(),
        workers=1,
    )


if __name__ == "__main__":
    main()
