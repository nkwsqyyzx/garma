"""
交易操作 API：下单 / 撤单。
HTTP 接口同样通过 Redis 命令队列投递，保持可靠链路。
"""

import logging
import time
import uuid

from fastapi import APIRouter, Request

from shared.const import ErrorCode
from shared.schemas.quote import ApiResponse
from shared.schemas.trade import PlaceOrderRequest, CancelOrderRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/trade", tags=["trade"])


@router.post("/order")
async def place_order(req: PlaceOrderRequest, request: Request):
    """下单（限价/市价），写入命令队列"""
    redis_bridge = request.app.state.redis_bridge
    config = request.app.state.config
    account_id = config.get("qmt", {}).get("account_id", "")

    req_id = req.req_id or str(uuid.uuid4())

    cmd = {
        "req_id": req_id,
        "cmd": "place_order",
        "account_id": account_id,
        "stock_code": req.stock_code,
        "order_type": req.order_type,
        "order_volume": req.order_volume,
        "price_type": req.price_type,
        "price": req.price,
        "strategy_name": req.strategy_name,
        "order_remark": req.order_remark,
        "retry_count": 0,
        "created_at": time.time(),
    }

    try:
        redis_bridge.push_cmd(cmd)
    except Exception as e:
        return ApiResponse(code=ErrorCode.INTERNAL_ERROR, msg=str(e), data=None)

    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data={
        "req_id": req_id,
        "status": "SUBMITTED",
        "stock_code": req.stock_code,
        "order_volume": req.order_volume,
        "price": req.price,
        "submitted_at": time.time(),
    })


@router.post("/cancel")
async def cancel_order(req: CancelOrderRequest, request: Request):
    """撤单，写入命令队列"""
    redis_bridge = request.app.state.redis_bridge
    config = request.app.state.config
    account_id = config.get("qmt", {}).get("account_id", "")

    req_id = req.req_id or str(uuid.uuid4())

    cmd = {
        "req_id": req_id,
        "cmd": "cancel_order",
        "account_id": account_id,
        "order_id": req.order_id,
        "retry_count": 0,
        "created_at": time.time(),
    }

    try:
        redis_bridge.push_cmd(cmd)
    except Exception as e:
        return ApiResponse(code=ErrorCode.INTERNAL_ERROR, msg=str(e), data=None)

    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data={
        "req_id": req_id,
        "order_id": req.order_id,
    })


@router.post("/cancel_all")
async def cancel_all(request: Request):
    """撤销全部可撤委托"""
    redis_bridge = request.app.state.redis_bridge
    config = request.app.state.config
    account_id = config.get("qmt", {}).get("account_id", "")

    req_id = str(uuid.uuid4())

    cmd = {
        "req_id": req_id,
        "cmd": "cancel_all",
        "account_id": account_id,
        "retry_count": 0,
        "created_at": time.time(),
    }

    try:
        redis_bridge.push_cmd(cmd)
    except Exception as e:
        return ApiResponse(code=ErrorCode.INTERNAL_ERROR, msg=str(e), data=None)

    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data={"req_id": req_id})


@router.get("/order/{req_id}")
async def query_order_status(req_id: str, request: Request):
    """查询委托状态（通过 req_id）"""
    redis_bridge = request.app.state.redis_bridge
    status = redis_bridge.get_order_status(req_id)
    if status is None:
        return ApiResponse(code=ErrorCode.SUCCESS, msg="无记录", data=None)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=status)
