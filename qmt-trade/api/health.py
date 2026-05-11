"""
健康检查 API：/health（xttrader 连接 + 账户状态 + CmdConsumer）
"""

import logging

from fastapi import APIRouter, Request

from shared.schemas.quote import ApiResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request):
    """交易服务健康状态"""
    reporter = request.app.state.status_reporter
    status = reporter.latest_status
    return ApiResponse(code=0, msg="ok", data=status)
