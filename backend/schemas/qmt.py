"""Alpha 端 QMT Pydantic 请求/响应模型。"""

from datetime import datetime
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# 下单
# ---------------------------------------------------------------------------

class QmtOrderRequest(BaseModel):
    stock_code: str = Field(..., description="股票代码，如 600519.SH")
    order_type: str = Field(..., description="buy / sell")
    order_volume: int = Field(..., gt=0, description="委托数量")
    price_type: str = Field(default="limit", description="limit / market / best5")
    price: float = Field(default=0.0, description="委托价格")
    strategy_name: str | None = Field(default=None, description="策略名称")
    order_remark: str | None = Field(default=None, description="委托备注")
    linked_req_id: str | None = Field(default=None, description="关联买入的order_req_id(卖出时使用)")
    linked_batch_id: str | None = Field(default=None, description="关联买入的batch_id(卖出时, 优先于linked_req_id)")
    batch_id: str | None = Field(default=None, description="拆单批次ID, 同一批拆单共享")


class QmtOrderResponse(BaseModel):
    req_id: str = Field(..., description="系统生成的唯一请求 ID")


# ---------------------------------------------------------------------------
# 撤单
# ---------------------------------------------------------------------------

class QmtCancelRequest(BaseModel):
    order_id: str = Field(..., description="QMT 委托编号")


# ---------------------------------------------------------------------------
# HTTP 透传
# ---------------------------------------------------------------------------

class QmtProxyRequest(BaseModel):
    method: str = Field(..., description="HTTP 方法: GET / POST / PUT / DELETE")
    path: str = Field(..., description="请求路径，如 /api/v1/subscribe")
    params: dict | None = Field(default=None, description="查询参数")
    body: dict | None = Field(default=None, description="请求体")


# ---------------------------------------------------------------------------
# 状态 & 配置
# ---------------------------------------------------------------------------

class QmtServiceStatus(BaseModel):
    source: str = Field(..., description="market / trade")
    status: str = Field(default="unknown")
    level: str = Field(default="offline")
    last_heartbeat: datetime | None = Field(default=None)
    tick_delay: float | None = Field(default=None, description="行情延迟秒数")


class QmtHealthResponse(BaseModel):
    market: QmtServiceStatus | None = Field(default=None)
    trade: QmtServiceStatus | None = Field(default=None)
    online: bool = Field(default=False, description="至少一个服务在线")


class QmtConfigResponse(BaseModel):
    qmt_server_url: str
    qmt_market_enabled: bool
    qmt_trade_enabled: bool
    qmt_account_id: str
    qmt_server_timeout: int


class QmtConfigUpdateRequest(BaseModel):
    qmt_server_url: str | None = None
    qmt_server_api_key: str | None = None
    qmt_server_timeout: int | None = None
    qmt_market_enabled: bool | None = None
    qmt_trade_enabled: bool | None = None
    qmt_account_id: str | None = None


# ---------------------------------------------------------------------------
# 熔断
# ---------------------------------------------------------------------------

class KillSwitchResponse(BaseModel):
    active: bool = Field(..., description="熔断是否激活")
