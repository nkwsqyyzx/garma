"""
行情 Pydantic 模型（Tick / Kline）。
供 qmt-market 和 Alpha 后端共享。
"""

from typing import Optional

from pydantic import BaseModel, Field


# ============================================================
# Tick 数据模型
# ============================================================

class TickData(BaseModel):
    """标准化 Tick 数据"""
    code: str = Field(..., description="股票代码，如 600519.SH")
    name: str = Field("", description="股票名称")
    time: str = Field("", description="时间 HH:MM:SS")
    datetime: str = Field("", description="日期时间 YYYY-MM-DD HH:MM:SS")
    timestamp: float = Field(0.0, description="Unix 时间戳")

    # 价格
    open: float = Field(0.0, description="开盘价")
    high: float = Field(0.0, description="最高价")
    low: float = Field(0.0, description="最低价")
    last: float = Field(0.0, description="最新价")
    close: float = Field(0.0, description="昨收价")

    # 五档买价
    bid1: float = Field(0.0)
    bid2: float = Field(0.0)
    bid3: float = Field(0.0)
    bid4: float = Field(0.0)
    bid5: float = Field(0.0)

    # 五档卖价
    ask1: float = Field(0.0)
    ask2: float = Field(0.0)
    ask3: float = Field(0.0)
    ask4: float = Field(0.0)
    ask5: float = Field(0.0)

    # 五档买量
    bid_vol1: int = Field(0)
    bid_vol2: int = Field(0)
    bid_vol3: int = Field(0)
    bid_vol4: int = Field(0)
    bid_vol5: int = Field(0)

    # 五档卖量
    ask_vol1: int = Field(0)
    ask_vol2: int = Field(0)
    ask_vol3: int = Field(0)
    ask_vol4: int = Field(0)
    ask_vol5: int = Field(0)

    # 成交统计
    volume: int = Field(0, description="成交量（股）")
    amount: float = Field(0.0, description="成交额（元）")
    avg_price: float = Field(0.0, description="均价")

    # 涨跌停
    limit_up: float = Field(0.0, description="涨停价")
    limit_down: float = Field(0.0, description="跌停价")
    pct_change: float = Field(0.0, description="涨跌幅（%）")
    change: float = Field(0.0, description="涨跌额")


# ============================================================
# K 线数据模型
# ============================================================

class KlineData(BaseModel):
    """标准化 K 线数据"""
    code: str = Field(..., description="股票代码")
    period: str = Field(..., description="K 线周期：1m / 5m / 1d 等")
    datetime: str = Field("", description="日期时间")
    timestamp: float = Field(0.0, description="Unix 时间戳")
    open: float = Field(0.0, description="开盘价")
    high: float = Field(0.0, description="最高价")
    low: float = Field(0.0, description="最低价")
    close: float = Field(0.0, description="收盘价")
    volume: int = Field(0, description="成交量（股）")
    amount: float = Field(0.0, description="成交额（元）")


# ============================================================
# HTTP 请求/响应模型
# ============================================================

class QuoteHistoryRequest(BaseModel):
    """历史行情下载请求"""
    code: str = Field(..., description="股票代码")
    period: str = Field("1d", description="K 线周期")
    start_time: str = Field("", description="开始时间 YYYYMMDD 或 YYYYMMDDHHmmSS")
    end_time: str = Field("", description="结束时间")
    incrementally: bool = Field(True, description="是否增量下载")


class ApiResponse(BaseModel):
    """统一响应格式"""
    code: int = Field(0, description="0=成功，非0=错误")
    msg: str = Field("ok", description="错误时为错误描述")
    data: Optional[dict | list] = Field(None, description="业务数据")
