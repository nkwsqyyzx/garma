"""
路由总注册。
"""

from fastapi import APIRouter

from .account import router as account_router
from .health import router as health_router
from .trade import router as trade_router


def create_router() -> APIRouter:
    root = APIRouter()
    root.include_router(trade_router)
    root.include_router(account_router)
    root.include_router(health_router)
    return root
