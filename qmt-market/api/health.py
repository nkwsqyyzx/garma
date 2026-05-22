"""
健康检查 API：/health
读取 Redis 中的 qmt:market:status（由外部系统维护）。
"""

import json
import logging

from fastapi import APIRouter, Request

from shared.schemas.quote import ApiResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


@router.get("/health")
async def health_check(request: Request):
    """行情服务健康状态（从 Redis qmt:market:status 读取）"""
    redis_bridge = request.app.state.redis_bridge
    status = None
    try:
        raw = redis_bridge.raw.get("qmt:market:status")
        if raw:
            status = json.loads(raw)
    except Exception:
        logger.warning("[WARN] 读取 qmt:market:status 失败", exc_info=True)
    return ApiResponse(code=0, msg="ok", data=status)
