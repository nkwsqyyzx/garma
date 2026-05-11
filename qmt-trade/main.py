"""
qmt-trade 交易服务启动入口。
FastAPI :8090 + TradeHub + AccountHub + CmdConsumer + CallbackHandler。
"""

import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# ---- sys.path 设置 ----
_BASE_DIR = Path(__file__).resolve().parent.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

_TRADE_DIR = Path(__file__).resolve().parent
if str(_TRADE_DIR) not in sys.path:
    sys.path.insert(0, str(_TRADE_DIR))

import uvicorn
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request

from shared.redis_bridge import RedisBridge
from core.trade_hub import TradeHub
from core.session_mgr import SessionMgr
from core.callback_handler import CallbackHandler
from core.account_hub import AccountHub
from core.cmd_consumer import CmdConsumer
from core.status_reporter import StatusReporter
from api.router import create_router

logger = logging.getLogger(__name__)


def load_config() -> dict:
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
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _create_control_router():
    from fastapi import APIRouter
    from shared.schemas.quote import ApiResponse
    from shared.const import ErrorCode

    router = APIRouter(prefix="/control", tags=["control"])

    @router.post("/reconnect")
    async def reconnect(request: Request):
        """重连 xttrader"""
        hub: TradeHub = request.app.state.trade_hub
        hub.disconnect()
        success = hub.connect()
        if success:
            # 重新注册回调
            callback = request.app.state.callback_handler
            hub.register_callback(callback)
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = app.state.config

    # ---- Startup ----
    logger.info("[STARTUP] qmt-trade 正在启动 ...")

    # 1. 初始化 Redis
    redis_bridge = RedisBridge(config)
    app.state.redis_bridge = redis_bridge
    logger.info("[OK] Redis 连接初始化完成")

    # 2. 冷启动恢复：检查备份队列是否有遗留命令
    recovered = redis_bridge.recover_backup_cmds()
    if recovered > 0:
        logger.info("[OK] 从备份队列恢复 %d 条命令", recovered)

    # 3. 初始化线程池
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="trade-exec")
    app.state.executor = executor

    # 4. 初始化 TradeHub
    trade_hub = TradeHub(redis_bridge, config)
    app.state.trade_hub = trade_hub

    # 5. 初始化 AccountHub
    account_hub = AccountHub(trade_hub, redis_bridge, config, executor=executor)
    app.state.account_hub = account_hub

    # 6. 先创建 SessionMgr（后续注入 callback_handler）
    session_mgr = SessionMgr(trade_hub)
    app.state.session_mgr = session_mgr

    # 7. 初始化 CallbackHandler
    callback_handler = CallbackHandler(
        account_hub, session_mgr, redis_bridge, executor
    )
    app.state.callback_handler = callback_handler

    # 7.1 注入 callback_handler 和 account_hub 到 SessionMgr（用于重连后恢复）
    session_mgr.set_post_reconnect_deps(callback_handler, account_hub)

    # 8. 连接 xttrader + 注册回调
    if trade_hub.connect():
        trade_hub.register_callback(callback_handler)
        logger.info("[OK] xttrader 连接成功")
    else:
        logger.warning("[WARN] xttrader 初始连接失败，将通过 SessionMgr 重连")
        # 仍然注册回调，等待重连后生效
        trade_hub.register_callback(callback_handler)

    # 9. 启动 AccountHub 轮询线程
    account_hub.start()

    # 10. 启动 CmdConsumer
    cmd_consumer = CmdConsumer(trade_hub, redis_bridge, config)
    app.state.cmd_consumer = cmd_consumer
    cmd_consumer.start()

    # 11. 初始化 StatusReporter
    reporter = StatusReporter(
        redis_bridge, trade_hub, account_hub, cmd_consumer, config
    )
    app.state.status_reporter = reporter
    reporter.start()

    trade_cfg = config.get("trade_server", {})
    port = trade_cfg.get("port", 8090)
    logger.info("[STARTUP] qmt-trade 启动完成，监听 :%d", port)

    yield  # ---- 应用运行中 ----

    # ---- Shutdown ----
    logger.info("[SHUTDOWN] qmt-trade 正在关机 ...")

    # 1. 停止接受新请求
    # 2. 设置 _running = False
    cmd_consumer: CmdConsumer = app.state.cmd_consumer
    if cmd_consumer:
        cmd_consumer.stop()

    account_hub: AccountHub = app.state.account_hub
    if account_hub:
        account_hub.stop()

    reporter: StatusReporter = app.state.status_reporter
    if reporter:
        reporter.stop()

    session_mgr: SessionMgr = app.state.session_mgr
    if session_mgr:
        session_mgr.stop()

    # 3. 等待 CmdConsumer 完成当前命令
    if cmd_consumer:
        cmd_consumer.join(timeout=15)

    # 4. 检查备份队列，未确认命令回主队列
    redis_bridge: RedisBridge = app.state.redis_bridge
    if redis_bridge:
        redis_bridge.recover_backup_cmds()

    # 5. 等待 AccountHub 后台线程
    # (account_hub._poll_thread 是 daemon，join 5s)
    if account_hub and account_hub._poll_thread and account_hub._poll_thread.is_alive():
        account_hub._poll_thread.join(timeout=5)

    # 6. 等待 StatusReporter
    if reporter and reporter._thread and reporter._thread.is_alive():
        reporter._thread.join(timeout=5)

    # 7. 写入 shutting_down 状态（TTL=10s）
    if redis_bridge:
        redis_bridge.raw.set(
            "qmt:trade:status",
            '{"source":"trade","overall_status":"shutting_down"}',
            ex=10,
        )

    # 8. 断开 xttrader
    trade_hub: TradeHub = app.state.trade_hub
    if trade_hub:
        trade_hub.disconnect()

    # 9. 关闭线程池
    executor: ThreadPoolExecutor = app.state.executor
    if executor:
        executor.shutdown(wait=False)

    # 10. 关闭 Redis
    if redis_bridge:
        redis_bridge.close()

    logger.info("[SHUTDOWN] qmt-trade 已停止")


def create_app() -> FastAPI:
    config = load_config()

    app = FastAPI(
        title="QMT Trade Service",
        description="A 股交易服务（Redis queue → xttrader）",
        version="1.0.0",
        lifespan=lifespan,
    )

    app.state.config = config
    app.state.redis_bridge = None
    app.state.trade_hub = None
    app.state.account_hub = None
    app.state.cmd_consumer = None
    app.state.callback_handler = None
    app.state.status_reporter = None
    app.state.executor = None

    app.include_router(create_router())
    app.include_router(_create_control_router())

    return app


app = create_app()


def main():
    config = load_config()
    trade_cfg = config.get("trade_server", {})
    server_cfg = config.get("server", {})

    log_level = server_cfg.get("log_level", "INFO")
    log_dir = _BASE_DIR / "logs" / "trade"
    log_dir.mkdir(parents=True, exist_ok=True)

    # 启用 faulthandler 以在 C 扩展崩溃（如 segfault）时输出 traceback
    import faulthandler
    crash_log = open(str(log_dir / "crash.log"), "w", encoding="utf-8")
    faulthandler.enable(file=crash_log, all_threads=True)

    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(str(log_dir / "app.log"), encoding="utf-8"),
        ],
    )

    host = server_cfg.get("host", "0.0.0.0")
    port = trade_cfg.get("port", 8090)

    logger.info("Starting qmt-trade on %s:%d", host, port)
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        log_level=log_level.lower(),
        workers=1,
    )


if __name__ == "__main__":
    main()
