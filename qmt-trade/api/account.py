"""
账户查询 API：资金 / 持仓 / 委托 / 成交。
"""

import logging

from fastapi import APIRouter, Request, Query

from shared.const import ErrorCode
from shared.schemas.quote import ApiResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/account", tags=["account"])


def _get_account_id(request: Request) -> str:
    return request.app.state.config.get("qmt", {}).get("account_id", "")


@router.get("/asset")
async def get_asset(request: Request):
    """查询资金"""
    account_id = _get_account_id(request)
    hub = request.app.state.account_hub
    asset = hub.get_asset(account_id)
    if not asset:
        # 回退到 Redis
        asset = request.app.state.redis_bridge.get_account_asset()
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=asset)


@router.get("/positions")
async def get_positions(request: Request):
    """查询持仓"""
    account_id = _get_account_id(request)
    hub = request.app.state.account_hub
    positions = hub.get_positions(account_id)
    if not positions:
        positions = request.app.state.redis_bridge.get_account_positions() or []
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=positions)


@router.get("/orders")
async def get_orders(
        request: Request,
        cancelable_only: bool = Query(False, description="仅返回可撤委托"),
):
    """查询当日委托（可选：仅可撤）"""
    account_id = _get_account_id(request)
    hub = request.app.state.account_hub
    orders = hub.get_orders(account_id, cancelable_only=cancelable_only)
    if not orders:
        orders = request.app.state.redis_bridge.get_account_orders() or []
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=orders)


@router.get("/trades")
async def get_trades(request: Request):
    """查询当日成交"""
    account_id = _get_account_id(request)
    hub = request.app.state.account_hub
    trades = hub.get_trades(account_id)
    if not trades:
        trades = request.app.state.redis_bridge.get_account_trades() or []
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=trades)
