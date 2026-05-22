"""盘后脚本：从 strategy_trades 聚合持仓快照写入 daily_positions。

用法:
    cd garma/
    python -m backend.scripts.sync_daily_positions              # 默认今天
    python -m backend.scripts.sync_daily_positions 2026-05-20   # 指定日期
"""

import gzip
import pickle
import sys
from datetime import date
from pathlib import Path

# 确保 garma/ 在 sys.path
_GARMA_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_GARMA_ROOT) not in sys.path:
    sys.path.insert(0, str(_GARMA_ROOT))


def _parse_date(val: str) -> date:
    """解析日期字符串。"""
    return date.fromisoformat(val.strip().replace("/", "-"))


def _name_from_remark(remark: str | None) -> str | None:
    """从 remark '策略:因子:序号:股票名' 中提取股票名。"""
    if not remark or ":" not in remark:
        return None
    parts = remark.split(":")
    return parts[-1] if parts[-1] else None


def _load_stock_names_from_redis(redis_url: str) -> dict[str, str]:
    """从 Redis 读取最新股票名称缓存。"""
    import redis as _redis
    r = _redis.from_url(redis_url)
    raw = r.get("股票基础信息")
    if not raw:
        return {}
    try:
        data = pickle.loads(gzip.decompress(raw))
        if isinstance(data, dict):
            names = data.get("股票名称")
            if isinstance(names, dict):
                return names
    except Exception:
        pass
    return {}


def main():
    import asyncio
    import backend.database as db_mod
    from backend.database import init_db
    from backend.models.strategy_trade import StrategyTrade
    from backend.models.daily_position import DailyPosition
    from backend.config import get_settings
    from sqlalchemy import text, func, case, delete, select

    snapshot_date = _parse_date(sys.argv[1]) if len(sys.argv) > 1 else date.today()
    settings = get_settings()
    account_id = settings.QMT_ACCOUNT_ID

    async def _sync():
        await init_db()
        async with db_mod.engine.begin() as conn:
            await conn.run_sync(db_mod.Base.metadata.create_all)

        # 1. 聚合 strategy_trades → 当前持仓
        #    卖出记录通过 linked_req_id 关联回买入的 order_req_id，
        #    用 CASE 将卖出映射到买入分组键进行抵消
        async with db_mod.async_session_factory() as session:
            stmt = (
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    func.coalesce(StrategyTrade.linked_req_id, StrategyTrade.order_req_id).label("position_req_id"),
                    StrategyTrade.trade_date,
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.volume),
                            else_=-StrategyTrade.volume,
                        )
                    ).label("holding_volume"),
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.amount),
                            else_=-StrategyTrade.amount,
                        )
                    ).label("total_cost"),
                )
                .where(StrategyTrade.account_id == account_id)
                .group_by(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    text("position_req_id"),
                    StrategyTrade.trade_date,
                )
                .having(text("holding_volume > 0"))
            )
            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            print(f"No open positions found for account {account_id}")
            return

        # 2. 获取最新股票名称（Redis 优先，remark 兜底）
        codes = list({r.stock_code for r in rows})
        redis_names = await asyncio.to_thread(_load_stock_names_from_redis, settings.REDIS_URL)
        name_map: dict[str, str] = {}
        for r in rows:
            if r.stock_code not in name_map:
                name_map[r.stock_code] = redis_names.get(r.stock_code) or _name_from_remark(r.remark) or r.stock_code

        # 3. 删除该日期的旧快照
        async with db_mod.async_session_factory() as session:
            await session.execute(
                delete(DailyPosition).where(
                    DailyPosition.snapshot_date == snapshot_date,
                    DailyPosition.account_id == account_id,
                )
            )
            await session.commit()

        # 4. 写入新快照
        inserted = 0
        async with db_mod.async_session_factory() as session:
            for r in rows:
                volume = int(r.holding_volume)
                total_cost = float(r.total_cost)
                avg_price = round(total_cost / volume, 4) if volume > 0 else 0

                pos = DailyPosition(
                    account_id=account_id,
                    snapshot_date=snapshot_date,
                    stock_code=r.stock_code,
                    stock_name=name_map.get(r.stock_code),
                    strategy=r.strategy,
                    factor=r.factor,
                    remark=r.remark,
                    order_req_id=r.position_req_id,
                    buy_date=r.trade_date,
                    volume=volume,
                    avg_price=avg_price,
                    cost=round(total_cost, 4),
                )
                session.add(pos)
                inserted += 1

            await session.commit()

        print(f"Synced {inserted} positions to daily_positions for {snapshot_date}")

    asyncio.run(_sync())


if __name__ == "__main__":
    main()
