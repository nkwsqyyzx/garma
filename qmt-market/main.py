"""
qmt-market 行情查询服务启动入口。
FastAPI + MarketHub（xtdata 同步查询）+ StatusReporter 定时上报。

行情订阅已由旧实现（qmt_tick.py）接管，本服务仅提供按需查询 API。

启动方式: python qmt-market/main.py
"""

import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

_SHANGHAI_TZ = timezone(timedelta(hours=8))


class _ShanghaiFormatter(logging.Formatter):
    """强制使用 Asia/Shanghai (UTC+8) 时区的日志 Formatter，跨平台通用。"""

    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created, tz=_SHANGHAI_TZ)
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S") + f",{int(record.msecs):03d}"

# ---- sys.path 设置 ----
# 1. 项目根目录（garma/）→ 使 shared 模块可被 import
_BASE_DIR = Path(__file__).resolve().parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

# 2. 本服务目录（qmt-market/）→ 使 core / api 模块可被 import
_MARKET_DIR = Path(__file__).resolve().parent
if str(_MARKET_DIR) not in sys.path:
    sys.path.insert(0, str(_MARKET_DIR))

import uvicorn
from contextlib import asynccontextmanager
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config

    # ---- Startup ----
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
    port = market_cfg.get("port", 3301)
    logger.info("[STARTUP] qmt-market 启动完成，监听 :%d", port)

    yield  # ---- 应用运行中 ----

    # ---- Shutdown（优雅关机）----
    logger.info("[SHUTDOWN] qmt-market 正在关机 ...")

    # 1. 停止 StatusReporter
    reporter: StatusReporter = app.state.status_reporter
    if reporter:
        reporter.stop()

    # 2. 停止 MarketHub
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

    # 5. 关闭 Redis 连接池
    if redis_bridge:
        redis_bridge.close()

    logger.info("[SHUTDOWN] qmt-market 已停止")


def create_app() -> FastAPI:
    config = load_config()

    app = FastAPI(
        title="QMT Market Service",
        description="A 股行情查询服务（xtdata 按需查询）",
        version="2.0.0",
        lifespan=lifespan,
    )

    # 存储到 app.state
    app.state.config = config
    app.state.redis_bridge = None
    app.state.market_hub = None
    app.state.status_reporter = None

    # 注册路由
    app.include_router(create_router())
    app.include_router(_create_control_router())

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
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_dir / "app.log"), encoding="utf-8"),
        ],
    )
    for h in logging.root.handlers:
        h.setFormatter(_ShanghaiFormatter(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))

    host = server_cfg.get("host", "0.0.0.0")
    port = market_cfg.get("port", 3301)

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
