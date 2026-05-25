"""每日资产快照 ORM 模型。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, DECIMAL, Date, DateTime, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class DailyAssetSnapshot(Base):
    __tablename__ = "daily_asset_snapshots"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="交易日期")
    snapshot_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="pre_market / post_market")
    total_asset: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="总资产")
    cash: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="可用资金")
    frozen_cash: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="冻结资金")
    market_value: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="证券市值")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")

    __table_args__ = (
        UniqueConstraint(
            "account_id", "trade_date", "snapshot_type",
            name="uk_account_date_type",
        ),
    )
