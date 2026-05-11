"""
账户 Pydantic 模型（资金 / 持仓 / 委托 / 成交）。
供 qmt-trade 和 Alpha 后端共享。
"""

from typing import Optional

from pydantic import BaseModel, Field


class AssetData(BaseModel):
    """资金快照"""
    account_id: str = Field("", description="证券账号")
    total_asset: float = Field(0.0, description="总资产")
    cash: float = Field(0.0, description="可用资金")
    frozen_cash: float = Field(0.0, description="冻结资金")
    market_value: float = Field(0.0, description="持仓市值")
    profit_loss: float = Field(0.0, description="浮动盈亏")
    profit_loss_ratio: float = Field(0.0, description="盈亏比例")
    updated_at: float = Field(0.0, description="更新时间戳")
    updated_by: str = Field("", description="更新来源: trade_callback / full_sync")


class PositionData(BaseModel):
    """持仓数据"""
    account_id: str = Field("", description="证券账号")
    stock_code: str = Field("", description="股票代码")
    stock_name: str = Field("", description="股票名称")
    volume: int = Field(0, description="持仓数量")
    can_use_volume: int = Field(0, description="可用数量（T+1 限制）")
    avg_price: float = Field(0.0, description="持仓均价")
    market_value: float = Field(0.0, description="当前市值")
    profit_loss: float = Field(0.0, description="浮动盈亏")
    profit_loss_ratio: float = Field(0.0, description="盈亏比例")
    open_price: float = Field(0.0, description="今日开盘价")
    updated_at: float = Field(0.0, description="更新时间戳")


class OrderData(BaseModel):
    """委托数据"""
    order_id: str = Field("", description="委托编号")
    order_sysid: str = Field("", description="交易所委托编号")
    req_id: Optional[str] = Field(None, description="关联请求 ID")
    account_id: str = Field("", description="证券账号")
    stock_code: str = Field("", description="股票代码")
    stock_name: str = Field("", description="股票名称")
    order_type: str = Field("", description="buy / sell")
    order_volume: int = Field(0, description="委托数量")
    traded_volume: int = Field(0, description="累计成交数量")
    price: float = Field(0.0, description="委托价格")
    traded_price: float = Field(0.0, description="成交均价")
    status: str = Field("", description="委托状态")
    status_msg: str = Field("", description="状态说明")
    order_time: str = Field("", description="委托时间")
    strategy_name: Optional[str] = Field(None, description="策略名称")
    updated_at: float = Field(0.0, description="更新时间戳")


class TradeRecord(BaseModel):
    """成交明细"""
    traded_id: str = Field("", description="成交编号")
    order_id: str = Field("", description="关联委托编号")
    req_id: Optional[str] = Field(None, description="关联请求 ID")
    account_id: str = Field("", description="证券账号")
    stock_code: str = Field("", description="股票代码")
    stock_name: str = Field("", description="股票名称")
    order_type: str = Field("", description="buy / sell")
    traded_volume: int = Field(0, description="本笔成交数量")
    traded_price: float = Field(0.0, description="本笔成交价格")
    traded_amount: float = Field(0.0, description="本笔成交金额")
    traded_time: str = Field("", description="成交时间")
    strategy_name: Optional[str] = Field(None, description="策略名称")
