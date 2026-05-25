"""银证转账记录 ORM 模型。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, DECIMAL, Date, DateTime, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class FundTransfer(Base):
    __tablename__ = "fund_transfers"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    direction: Mapped[str] = mapped_column(String(10), nullable=False, comment="deposit / withdraw")
    amount: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="金额")
    note: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP"), comment="更新时间")
