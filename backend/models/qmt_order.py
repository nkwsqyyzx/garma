"""QMT 订单 ORM 模型，映射设计文档 Section 8.3。"""

from datetime import datetime
from sqlalchemy import BigInteger, String, Integer, SmallInteger, DECIMAL, DateTime, Index
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class QmtOrder(Base):
    __tablename__ = "qmt_orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    req_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, comment="系统生成唯一请求ID")
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="QMT返回委托编号")
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="股票名称")
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, comment="buy/sell")
    order_volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="委托数量")
    traded_volume: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0", comment="成交数量")
    price_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="limit/market/best5")
    price: Mapped[float] = mapped_column(DECIMAL(10, 4), nullable=False, server_default="0", comment="委托价格")
    traded_price: Mapped[float | None] = mapped_column(DECIMAL(10, 4), nullable=True, comment="成交均价")
    status: Mapped[str] = mapped_column(String(30), nullable=False, server_default="DRAFT", comment="订单状态")
    status_msg: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="状态说明")
    strategy_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="策略名称")
    order_remark: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="委托备注")
    linked_req_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联买入的order_req_id(卖出时使用)")
    retry_count: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0", comment="重试次数")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default="CURRENT_TIMESTAMP", comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False,
        server_default="CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        comment="更新时间",
    )

    __table_args__ = (
        Index("idx_account_status", "account_id", "status"),
        Index("idx_stock_created", "stock_code", "created_at"),
        Index("idx_order_id", "order_id"),
    )
