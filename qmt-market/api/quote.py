"""
行情查询 API：最新 tick、历史 K 线、基本信息、板块成分股、完整 tick、历史下载。
"""

import logging

from fastapi import APIRouter, Request, Query

from shared.const import ErrorCode
from shared.schemas.quote import (
    QuoteHistoryRequest,
    ApiResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/quote", tags=["quote"])


@router.get("/tick")
async def batch_tick(
        request: Request,
        codes: str = Query(..., description="逗号分隔的股票代码，最多50只"),
):
    """批量查询最新 Tick（最多 50 只）"""
    code_list = [c.strip() for c in codes.split(",") if c.strip()]
    if not code_list:
        return ApiResponse(code=ErrorCode.PARAM_ERROR, msg="codes 不能为空")
    if len(code_list) > 50:
        return ApiResponse(code=ErrorCode.PARAM_ERROR, msg="最多查询 50 只")

    hub = request.app.state.market_hub
    data = hub.get_ticks_batch(code_list)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=data)


@router.get("/tick/{code}")
async def single_tick(code: str, request: Request):
    """查询单只股票最新 Tick"""
    hub = request.app.state.market_hub
    tick = hub.get_tick(code)
    if tick is None:
        return ApiResponse(code=ErrorCode.SUCCESS, msg="无数据", data=None)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=tick)


@router.get("/kline")
async def kline(
        request: Request,
        code: str = Query(..., description="股票代码"),
        period: str = Query("1d", description="K 线周期：1m / 5m / 1d"),
        count: int = Query(100, description="返回条数", ge=1, le=1000),
):
    """查询 K 线数据"""
    hub = request.app.state.market_hub
    data = hub.get_kline(code, period=period, count=count)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=data)


@router.get("/detail/{code}")
async def instrument_detail(code: str, request: Request):
    """查询股票基本信息（名称、涨停价等）"""
    hub = request.app.state.market_hub
    detail = hub.get_instrument_detail(code)
    if detail is None:
        return ApiResponse(code=ErrorCode.SUCCESS, msg="无数据", data=None)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=detail)


@router.get("/sector/{sector}")
async def sector_list(sector: str, request: Request):
    """查询板块成分股列表"""
    hub = request.app.state.market_hub
    codes = hub.get_sector_list(sector)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=codes)


@router.get("/full_tick")
async def full_tick(
        request: Request,
        code: str = Query(..., description="股票代码"),
):
    """查询完整 Tick（含逐笔）"""
    hub = request.app.state.market_hub
    tick = hub.get_full_tick(code)
    if tick is None:
        return ApiResponse(code=ErrorCode.SUCCESS, msg="无数据", data=None)
    return ApiResponse(code=ErrorCode.SUCCESS, msg="ok", data=tick)


@router.post("/history")
async def download_history(req: QuoteHistoryRequest, request: Request):
    """查询历史行情（触发下载）"""
    hub = request.app.state.market_hub
    result = hub.download_history(
        code=req.code,
        period=req.period,
        start_time=req.start_time,
        end_time=req.end_time,
        incrementally=req.incrementally,
    )
    code = ErrorCode.SUCCESS if result.get("downloaded") else ErrorCode.INTERNAL_ERROR
    return ApiResponse(code=code, msg=result.get("error", "ok"), data=result)
