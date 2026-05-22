"""一次性脚本：将 CSV 策略持仓数据导入 strategy_trades 表。

用法:
    cd garma/
    python -m backend.scripts.import_csv_trades /tmp/1/b.csv
"""

import csv
import sys
from datetime import date
from pathlib import Path

# 确保 garma/ 在 sys.path
_GARMA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_GARMA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GARMA_ROOT))


def _parse_date(val: str) -> date | None:
    """解析日期字符串，兼容多种格式。"""
    if not val:
        return None
    val = val.strip().split("T")[0]  # 处理 ISO 格式 2026-05-21T00:00:00
    try:
        return date.fromisoformat(val.replace("/", "-"))
    except (ValueError, TypeError):
        return None


def main():
    import asyncio
    import backend.database as db_mod
    from backend.database import init_db
    from backend.models.strategy_trade import StrategyTrade
    from backend.config import get_settings

    csv_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/1/b.csv"
    settings = get_settings()
    account_id = settings.QMT_ACCOUNT_ID

    print(f"Reading CSV: {csv_path}")
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Found {len(rows)} rows")

    async def _import():
        await init_db()
        # 确保表存在
        async with db_mod.engine.begin() as conn:
            await conn.run_sync(db_mod.Base.metadata.create_all)
        # 清理旧导入数据
        from sqlalchemy import delete
        async with db_mod.async_session_factory() as session:
            await session.execute(delete(StrategyTrade).where(StrategyTrade.source == "import"))
            await session.commit()
            print("Cleaned up previous import data")
        inserted = 0
        skipped = 0

        async with db_mod.async_session_factory() as session:
            for i, row in enumerate(rows):
                stock_code = row.get("证券代码", "").strip()
                if not stock_code:
                    skipped += 1
                    continue

                volume = int(float(row.get("持仓量", 0)))
                price = float(row.get("成交均价", 0))
                amount = float(row.get("持仓成本", volume * price))
                trade_date = _parse_date(row.get("交易日期", ""))
                strategy = row.get("策略", "").strip() or None
                factor = row.get("因子", "").strip() or None
                remark = row.get("其他", "").strip() or None
                order_req_id = row.get("订单标记", "").strip() or None

                if not trade_date:
                    trade_date = date.today()

                trade = StrategyTrade(
                    account_id=account_id,
                    stock_code=stock_code,
                    stock_name=None,
                    direction="buy",
                    volume=volume,
                    price=price,
                    amount=amount,
                    strategy=strategy,
                    factor=factor,
                    remark=remark,
                    trade_date=trade_date,
                    source="import",
                    order_req_id=order_req_id,
                )
                session.add(trade)
                inserted += 1

            await session.commit()

        print(f"Import complete: {inserted} inserted, {skipped} skipped")

        await db_mod.engine.dispose()

    asyncio.run(_import())


if __name__ == "__main__":
    main()
