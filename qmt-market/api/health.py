"""
健康检查 API：/health（xtdata 连接 + Redis 连通性）
"""

import logging

from fastapi import APIRouter, Request

from shared.schemas.quote import ApiResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request):
    """行情服务健康状态（xtdata 连接 + Redis 连通性）"""
    reporter = request.app.state.status_reporter
    status = reporter.latest_status
    return ApiResponse(code=0, msg="ok", data=status)
