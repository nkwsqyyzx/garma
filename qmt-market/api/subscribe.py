"""
订阅管理 API：添加/取消/列表/重置。
"""

import logging

from fastapi import APIRouter, Request

from shared.const import ErrorCode
from shared.schemas.quote import (
    SubscribeAddRequest,
    SubscribeRemoveRequest,
    ApiResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/subscribe", tags=["subscribe"])


@router.get("/list")
async def list_subscriptions(request: Request):
    """当前订阅列表"""
    hub = request.app.state.market_hub
    subs = hub.get_subscription_list()
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=subs)


@router.post("/add")
async def add_subscriptions(req: SubscribeAddRequest, request: Request):
    """添加订阅（批量）"""
    hub = request.app.state.market_hub
    result = hub.subscribe(req.codes, source="api")

    error = result.get("error")
    code = ErrorCode.SUBSCRIBE_LIMIT_EXCEEDED if error and "上限" in error else ErrorCode.SUCCESS
    return ApiResponse(code=code, msg=error or "ok", data=result)


@router.post("/remove")
async def remove_subscriptions(req: SubscribeRemoveRequest, request: Request):
    """取消订阅（批量）"""
    hub = request.app.state.market_hub
    result = hub.unsubscribe(req.codes)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=result)


@router.delete("/all")
async def clear_all_subscriptions(request: Request):
    """清空全部订阅"""
    hub = request.app.state.market_hub
    count = hub.unsubscribe_all()
    return ApiResponse(
        code=ErrorCode.SUCCESS,
        msg="ok",
        data={"cleared": count},
    )
