"""策略成交流水 ORM 模型。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, Integer, DECIMAL, Date, DateTime, Index, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class StrategyTrade(Base):
    __tablename__ = "strategy_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="股票名称")
    direction: Mapped[str] = mapped_column(String(4), nullable=False, comment="buy/sell")
    volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="成交数量")
    price: Mapped[float] = mapped_column(DECIMAL(12, 4), nullable=False, comment="成交均价")
    amount: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="成交金额")
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="策略名")
    factor: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="因子")
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注(原始其他字段)")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="成交日期")
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="order", comment="来源: order/import/manual")
    order_req_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联 qmt_orders.req_id")
    linked_req_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="卖出时关联的买入order_req_id")
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="拆单批次ID, 同一批拆单共享")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")

    __table_args__ = (
        Index("idx_account_date", "account_id", "trade_date"),
        Index("idx_stock_date", "stock_code", "trade_date"),
        Index("idx_order_req_id", "order_req_id"),
        Index("idx_batch_id", "batch_id"),
    )
