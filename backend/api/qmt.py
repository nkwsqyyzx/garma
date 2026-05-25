"""QMT API 路由。"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from backend.config import get_settings, Settings
from backend.schemas.qmt import (
    QmtOrderRequest,
    QmtOrderResponse,
    QmtCancelRequest,
    QmtProxyRequest,
    QmtHealthResponse,
    QmtConfigResponse,
    QmtConfigUpdateRequest,
    KillSwitchResponse,
)
from backend.service.qmt_service import QmtService

router = APIRouter(prefix="/qmt", tags=["QMT"])


# ---------------------------------------------------------------------------
# 依赖注入
# ---------------------------------------------------------------------------

def get_qmt_service() -> QmtService:
    """从 app.state 获取 QmtService 单例。"""
    from backend.main import app
    svc = getattr(app.state, "qmt_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="QMT service not initialized")
    return svc


def _api_ok(data=None, msg="success") -> dict:
    return {"code": 0, "msg": msg, "data": data}


def _api_error(code: int, msg: str) -> dict:
    return {"code": code, "msg": msg, "data": None}


# ===========================================================================
# 行情（3 个）
# ===========================================================================

@router.get("/quote/snapshot")
async def quote_snapshot(
    codes: str = Query(..., description="逗号分隔的股票代码，如 600519.SH,000001.SZ"),
    svc: QmtService = Depends(get_qmt_service),
):
    """批量查询最新 Tick。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    data = await svc.get_snapshot(code_list)
    return _api_ok(data)


@router.get("/quote/tick/{code}")
async def quote_tick(
    code: str,
    svc: QmtService = Depends(get_qmt_service),
):
    """单只股票最新 Tick。"""
    data = await svc.get_tick(code)
    if data is None:
        return _api_error(1001, f"No tick data for {code}")
    return _api_ok(data)


@router.get("/quote/kline")
async def quote_kline(
    code: str = Query(..., description="股票代码"),
    period: str = Query(default="1d", description="K 线周期"),
    count: int = Query(default=100, ge=1, le=1000, description="返回条数"),
    svc: QmtService = Depends(get_qmt_service),
):
    """查询 K 线。"""
    data = await svc.get_kline(code, period, count)
    return _api_ok(data)


@router.get("/quote/stock_names")
async def quote_stock_names(
    codes: str = Query(..., description="逗号分隔的股票代码"),
    svc: QmtService = Depends(get_qmt_service),
):
    """批量查询股票名称。"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    data = await svc.get_stock_names(code_list)
    return _api_ok(data)


# ===========================================================================
# 账户（4 个）
# ===========================================================================

@router.get("/account/asset")
async def account_asset(
    svc: QmtService = Depends(get_qmt_service),
):
    """账户资金。"""
    data = await svc.get_asset()
    return _api_ok(data)


@router.get("/account/positions")
async def account_positions(
    svc: QmtService = Depends(get_qmt_service),
):
    """持仓列表。"""
    data = await svc.get_positions()
    return _api_ok(data)


@router.get("/account/orders")
async def account_orders(
    cancelable_only: bool = Query(default=False, description="仅返回可撤委托"),
    svc: QmtService = Depends(get_qmt_service),
):
    """当日委托列表。"""
    data = await svc.get_orders(cancelable_only)
    return _api_ok(data)


@router.get("/account/trades")
async def account_trades(
    svc: QmtService = Depends(get_qmt_service),
):
    """当日成交列表。"""
    data = await svc.get_trades()
    return _api_ok(data)


# ===========================================================================
# 交易（4 个）
# ===========================================================================

@router.post("/trade/order")
async def trade_order(
    request: QmtOrderRequest,
    svc: QmtService = Depends(get_qmt_service),
):
    """下单：MySQL 写入 + Redis 队列。"""
    # 检查熔断
    if await svc.get_kill_switch():
        raise HTTPException(status_code=403, detail="Kill switch is active, trading disabled")
    try:
        req_id = await svc.place_order(request)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _api_ok(QmtOrderResponse(req_id=req_id).model_dump())


@router.post("/trade/cancel")
async def trade_cancel(
    request: QmtCancelRequest,
    svc: QmtService = Depends(get_qmt_service),
):
    """撤单。"""
    req_id = await svc.cancel_order(request.order_id)
    return _api_ok({"req_id": req_id})


@router.post("/trade/cancel_all")
async def trade_cancel_all(
    svc: QmtService = Depends(get_qmt_service),
):
    """全撤。"""
    req_id = await svc.cancel_all()
    return _api_ok({"req_id": req_id})


@router.get("/trade/order/{req_id}")
async def trade_order_status(
    req_id: str,
    svc: QmtService = Depends(get_qmt_service),
):
    """查询订单状态。"""
    data = await svc.get_order_status(req_id)
    if data is None:
        return _api_error(1001, f"Order not found: {req_id}")
    return _api_ok(data)


# ===========================================================================
# 策略持仓（1 个）
# ===========================================================================

@router.get("/strategy/positions")
async def strategy_positions(
    svc: QmtService = Depends(get_qmt_service),
):
    """策略持仓：从 qmt-market RPC 读取当天成交缓存。"""
    data = await svc.get_strategy_positions()
    return _api_ok(data)


@router.get("/strategy/trades")
async def strategy_trades(
    trade_date: date | None = Query(None, description="交易日期，如 2026-05-22"),
    svc: QmtService = Depends(get_qmt_service),
):
    """策略成交：从 strategy_trades 表查询成交流水。"""
    data = await svc.get_strategy_trades(trade_date=trade_date)
    return _api_ok(data)


# ===========================================================================
# 健康与调试（2 个）
# ===========================================================================

@router.get("/health")
async def health(
    svc: QmtService = Depends(get_qmt_service),
):
    """QMT-Server 连接状态。"""
    result = await svc.health()
    return _api_ok(result.model_dump())


@router.post("/proxy")
async def proxy(
    request: QmtProxyRequest,
    svc: QmtService = Depends(get_qmt_service),
):
    """代理请求到 QMT-Server（白名单校验）。"""
    data = await svc.proxy_request(
        method=request.method,
        path=request.path,
        params=request.params,
        body=request.body,
    )
    return data


# ===========================================================================
# 熔断（3 个）
# ===========================================================================

@router.get("/kill-switch")
async def get_kill_switch(
    svc: QmtService = Depends(get_qmt_service),
):
    """查询熔断状态。"""
    active = await svc.get_kill_switch()
    return _api_ok(KillSwitchResponse(active=active).model_dump())


@router.post("/kill-switch")
async def activate_kill_switch(
    svc: QmtService = Depends(get_qmt_service),
):
    """激活熔断。"""
    await svc.set_kill_switch(True)
    return _api_ok(KillSwitchResponse(active=True).model_dump())


@router.delete("/kill-switch")
async def deactivate_kill_switch(
    svc: QmtService = Depends(get_qmt_service),
):
    """关闭熔断。"""
    await svc.set_kill_switch(False)
    return _api_ok(KillSwitchResponse(active=False).model_dump())


# ===========================================================================
# 设置（2 个）
# ===========================================================================

@router.get("/config")
async def get_config():
    """查看 QMT 配置（脱敏）。"""
    settings = get_settings()
    return _api_ok(QmtConfigResponse(
        qmt_server_url=settings.QMT_SERVER_URL,
        qmt_market_enabled=settings.QMT_TRADE_ENABLED,  # 保留字段兼容前端，用 TRADE 值
        qmt_trade_enabled=settings.QMT_TRADE_ENABLED,
        qmt_account_id=settings.QMT_ACCOUNT_ID,
        qmt_server_timeout=settings.QMT_SERVER_TIMEOUT,
    ).model_dump())


@router.put("/config")
async def update_config(
    request: QmtConfigUpdateRequest,
):
    """更新 QMT 配置。"""
    # 配置通过环境变量 / .env 管理，运行时不支持热更新核心配置
    # 此接口用于前端展示，实际修改需要更新 .env 文件并重启服务
    return _api_error(1001, "Config update requires .env file modification and service restart")
