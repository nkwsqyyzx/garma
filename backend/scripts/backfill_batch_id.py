"""一次性回填脚本：为卖出 strategy_trades 和 qmt_orders 回填缺失的 batch_id。

逻辑：
1. 查找所有 direction='sell' AND batch_id IS NULL AND linked_req_id IS NOT NULL 的 strategy_trades
2. 从关联买入的 strategy_trades 获取 batch_id 并回填
3. 同步回填 qmt_orders 中缺失 batch_id 的卖出订单

用法:
    cd garma/
    python -m backend.scripts.backfill_batch_id              # 执行回填
    python -m backend.scripts.backfill_batch_id --dry-run    # 仅预览，不写入
"""

import sys
from pathlib import Path

# 确保 garma/ 在 sys.path
_GARMA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_GARMA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GARMA_ROOT))


def main():
    import asyncio
    import backend.database as db_mod
    from backend.database import init_db
    from backend.models.strategy_trade import StrategyTrade
    from backend.models.qmt_order import QmtOrder
    from sqlalchemy import select, update, text

    dry_run = "--dry-run" in sys.argv

    async def _backfill():
        await init_db()
        # 确保表/列存在
        async with db_mod.engine.begin() as conn:
            await conn.run_sync(db_mod.Base.metadata.create_all)

        # ---- Step 1: 回填 strategy_trades ----
        # 查找所有卖出且缺少 batch_id 但有 linked_req_id 的记录
        async with db_mod.async_session_factory() as session:
            result = await session.execute(
                select(
                    StrategyTrade.id,
                    StrategyTrade.linked_req_id,
                ).where(
                    StrategyTrade.direction == "sell",
                    StrategyTrade.batch_id.is_(None),
                    StrategyTrade.linked_req_id.isnot(None),
                    StrategyTrade.linked_req_id != "",
                )
            )
            sell_rows = result.all()

        if not sell_rows:
            print("No strategy_trades need batch_id backfill.")
        else:
            print(f"Found {len(sell_rows)} strategy_trades (sell) missing batch_id.")

            # 收集所有 linked_req_id，批量查询买入的 batch_id
            linked_ids = list({row[1] for row in sell_rows})
            async with db_mod.async_session_factory() as session:
                result = await session.execute(
                    select(
                        StrategyTrade.order_req_id,
                        StrategyTrade.batch_id,
                    ).where(
                        StrategyTrade.order_req_id.in_(linked_ids),
                        StrategyTrade.batch_id.isnot(None),
                    )
                )
                buy_batch_map: dict[str, str] = {row[0]: row[1] for row in result.all()}

            # 也查 qmt_orders 作为备选
            missing_in_st = [lid for lid in linked_ids if lid not in buy_batch_map]
            if missing_in_st:
                async with db_mod.async_session_factory() as session:
                    result = await session.execute(
                        select(
                            QmtOrder.req_id,
                            QmtOrder.batch_id,
                        ).where(
                            QmtOrder.req_id.in_(missing_in_st),
                            QmtOrder.batch_id.isnot(None),
                        )
                    )
                    for row in result.all():
                        buy_batch_map[row[0]] = row[1]

            st_fixed = 0
            async with db_mod.async_session_factory() as session:
                for st_id, linked_req_id in sell_rows:
                    batch_id = buy_batch_map.get(linked_req_id)
                    if batch_id:
                        if dry_run:
                            print(f"  [DRY-RUN] strategy_trades id={st_id} linked_req_id={linked_req_id} -> batch_id={batch_id}")
                        else:
                            await session.execute(
                                update(StrategyTrade)
                                .where(StrategyTrade.id == st_id)
                                .values(batch_id=batch_id)
                            )
                            print(f"  Fixed strategy_trades id={st_id}: batch_id={batch_id}")
                        st_fixed += 1

                if not dry_run and st_fixed > 0:
                    await session.commit()

            print(f"strategy_trades: {st_fixed} records {'would be ' if dry_run else ''}fixed.")

        # ---- Step 2: 回填 qmt_orders ----
        async with db_mod.async_session_factory() as session:
            result = await session.execute(
                select(
                    QmtOrder.req_id,
                    QmtOrder.linked_req_id,
                ).where(
                    QmtOrder.order_type == "sell",
                    QmtOrder.batch_id.is_(None),
                    QmtOrder.linked_req_id.isnot(None),
                    QmtOrder.linked_req_id != "",
                )
            )
            sell_orders = result.all()

        if not sell_orders:
            print("No qmt_orders need batch_id backfill.")
        else:
            print(f"Found {len(sell_orders)} qmt_orders (sell) missing batch_id.")

            # 从 strategy_trades 查买入的 batch_id
            linked_ids = list({row[1] for row in sell_orders})
            async with db_mod.async_session_factory() as session:
                result = await session.execute(
                    select(
                        StrategyTrade.order_req_id,
                        StrategyTrade.batch_id,
                    ).where(
                        StrategyTrade.order_req_id.in_(linked_ids),
                        StrategyTrade.batch_id.isnot(None),
                    )
                )
                batch_map: dict[str, str] = {row[0]: row[1] for row in result.all()}

            # 备选：从 qmt_orders 的买入记录查
            missing = [lid for lid in linked_ids if lid not in batch_map]
            if missing:
                async with db_mod.async_session_factory() as session:
                    result = await session.execute(
                        select(
                            QmtOrder.req_id,
                            QmtOrder.batch_id,
                        ).where(
                            QmtOrder.req_id.in_(missing),
                            QmtOrder.batch_id.isnot(None),
                        )
                    )
                    for row in result.all():
                        batch_map[row[0]] = row[1]

            order_fixed = 0
            async with db_mod.async_session_factory() as session:
                for req_id, linked_req_id in sell_orders:
                    batch_id = batch_map.get(linked_req_id)
                    if batch_id:
                        if dry_run:
                            print(f"  [DRY-RUN] qmt_orders req_id={req_id} linked_req_id={linked_req_id} -> batch_id={batch_id}")
                        else:
                            await session.execute(
                                update(QmtOrder)
                                .where(QmtOrder.req_id == req_id)
                                .values(batch_id=batch_id)
                            )
                            print(f"  Fixed qmt_orders req_id={req_id}: batch_id={batch_id}")
                        order_fixed += 1

                if not dry_run and order_fixed > 0:
                    await session.commit()

            print(f"qmt_orders: {order_fixed} records {'would be ' if dry_run else ''}fixed.")

        await db_mod.engine.dispose()

    asyncio.run(_backfill())


if __name__ == "__main__":
    main()
