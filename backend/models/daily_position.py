"""每日持仓快照 ORM 模型（盘后定时任务生成，本期仅建表）。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, Integer, DECIMAL, Date, DateTime, UniqueConstraint, text,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class DailyPosition(Base):
    __tablename__ = "daily_positions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, comment="快照日期")
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="股票名称")
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="策略名")
    factor: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="因子")
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注")
    buy_date: Mapped[date] = mapped_column(Date, nullable=False, comment="买入日期")
    volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="持仓量")
    avg_price: Mapped[float] = mapped_column(DECIMAL(12, 4), nullable=False, comment="加权均价")
    cost: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="持仓成本")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=text("CURRENT_TIMESTAMP"), comment="创建时间")

    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "account_id", "stock_code", "strategy", "factor", "buy_date",
            name="uk_snapshot_account_stock_strategy_buy",
        ),
    )
