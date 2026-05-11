"""
交易 Pydantic 模型（下单请求 / 撤单请求 / 命令格式）。
供 qmt-trade 和 Alpha 后端共享。
"""

from typing import Optional

from pydantic import BaseModel, Field


class PlaceOrderRequest(BaseModel):
    """下单请求"""
    req_id: Optional[str] = Field(None, description="请求唯一 ID（未提供则自动生成）")
    stock_code: str = Field(..., description="股票代码，如 600519.SH")
    order_type: str = Field(..., description="buy / sell")
    order_volume: int = Field(..., description="委托数量（股，必须是100的整数倍）", gt=0)
    price_type: str = Field("limit", description="limit / market / best5 / cancel_remain")
    price: float = Field(0.0, description="委托价格（限价单必填；市价单填 0）", ge=0)
    strategy_name: Optional[str] = Field(None, description="策略名称")
    order_remark: Optional[str] = Field(None, description="委托备注")


class CancelOrderRequest(BaseModel):
    """撤单请求"""
    req_id: Optional[str] = Field(None, description="请求唯一 ID（未提供则自动生成）")
    order_id: str = Field(..., description="委托编号")


class CancelAllRequest(BaseModel):
    """全部撤单请求（无参数，保留扩展）"""
    pass


class TradeCommand(BaseModel):
    """
    Redis 命令队列中的命令格式。
    Alpha 后端或 HTTP 接口写入 qmt:cmd:queue 的 JSON 结构。
    """
    req_id: str = Field(..., description="请求唯一 ID（UUID4）")
    cmd: str = Field(..., description="命令类型：place_order / cancel_order / cancel_all")
    account_id: str = Field("", description="证券账号")
    stock_code: Optional[str] = Field(None, description="股票代码")
    order_type: Optional[str] = Field(None, description="buy / sell")
    order_volume: Optional[int] = Field(None, description="委托数量")
    price_type: Optional[str] = Field(None, description="价格类型")
    price: Optional[float] = Field(None, description="委托价格")
    order_id: Optional[str] = Field(None, description="撤单时的委托编号")
    strategy_name: Optional[str] = Field(None, description="策略名称")
    order_remark: Optional[str] = Field(None, description="委托备注")
    retry_count: int = Field(0, description="重试次数")
    created_at: float = Field(0.0, description="创建时间戳")
