"""
路由总注册。
"""

from fastapi import APIRouter

from .health import router as health_router
from .quote import router as quote_router


def create_router() -> APIRouter:
    """创建并注册行情服务路由"""
    root = APIRouter()
    root.include_router(quote_router)
    root.include_router(health_router)
    return root
