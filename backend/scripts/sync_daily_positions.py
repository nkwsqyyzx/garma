"""
盘后脚本：从 strategy_trades 聚合持仓快照写入 daily_positions。

推荐运行时间：开盘日的15:30

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

from backend.utils.wechat import send_text


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
        #    只用 stock_code + position_req_id 分组抵消买卖，
        #    买入的元数据（remark/strategy/factor/trade_date）从买入记录取
        async with db_mod.async_session_factory() as session:
            # 1a. 按 stock_code + position_req_id 聚合净持仓
            holding_stmt = (
                select(
                    StrategyTrade.stock_code,
                    func.coalesce(StrategyTrade.linked_req_id, StrategyTrade.order_req_id).label("position_req_id"),
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.volume),
                            else_=-StrategyTrade.volume,
                        )
                    ).label("holding_volume"),
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.volume),
                            else_=0,
                        )
                    ).label("buy_volume"),
                    func.sum(
                        case(
                            (StrategyTrade.direction == "buy", StrategyTrade.amount),
                            else_=0,
                        )
                    ).label("buy_cost"),
                )
                .where(StrategyTrade.account_id == account_id)
                .group_by(
                    StrategyTrade.stock_code,
                    text("position_req_id"),
                )
                .having(text("holding_volume > 0"))
            )
            holding_result = await session.execute(holding_stmt)
            holdings = holding_result.all()

        if not holdings:
            print(f"No open positions found for account {account_id}")
            return

        # 1b. 取每条持仓对应买入记录的元数据
        req_ids = [h.position_req_id for h in holdings]
        buy_meta: dict[str, tuple] = {}  # req_id -> (strategy, factor, remark, trade_date)
        async with db_mod.async_session_factory() as session:
            meta_result = await session.execute(
                select(
                    StrategyTrade.order_req_id,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    StrategyTrade.trade_date,
                )
                .where(
                    StrategyTrade.order_req_id.in_(req_ids),
                    StrategyTrade.direction == "buy",
                    StrategyTrade.account_id == account_id,
                )
            )
            for row in meta_result.all():
                buy_meta[row[0]] = (row[1], row[2], row[3], row[4])

        # 2. 获取最新股票名称（Redis 优先，remark 兜底）
        codes = list({h.stock_code for h in holdings})
        redis_names = await asyncio.to_thread(_load_stock_names_from_redis, settings.REDIS_URL)
        name_map: dict[str, str] = {}
        for h in holdings:
            if h.stock_code not in name_map:
                meta = buy_meta.get(h.position_req_id)
                remark = meta[2] if meta else None
                name_map[h.stock_code] = redis_names.get(h.stock_code) or _name_from_remark(remark) or h.stock_code

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
            for h in holdings:
                volume = int(h.holding_volume)
                # avg_price 取原始买入均价（不受卖出影响）
                buy_volume = int(h.buy_volume)
                buy_cost = float(h.buy_cost)
                avg_price = round(buy_cost / buy_volume, 4) if buy_volume > 0 else 0
                cost = round(avg_price * volume, 4)

                meta = buy_meta.get(h.position_req_id)
                strategy = meta[0] if meta else None
                factor = meta[1] if meta else None
                remark = meta[2] if meta else None
                trade_date = meta[3] if meta else date.today()

                pos = DailyPosition(
                    account_id=account_id,
                    snapshot_date=snapshot_date,
                    stock_code=h.stock_code,
                    stock_name=name_map.get(h.stock_code),
                    strategy=strategy,
                    factor=factor,
                    remark=remark,
                    order_req_id=h.position_req_id,
                    buy_date=trade_date,
                    volume=volume,
                    avg_price=avg_price,
                    cost=cost,
                )
                session.add(pos)
                inserted += 1

            await session.commit()

        print(f"Synced {inserted} positions to daily_positions for {snapshot_date}")

        # 5. 比对券商持仓
        import json
        raw = await asyncio.to_thread(lambda: __import__('redis').from_url(settings.REDIS_URL).get('qmt:account:positions'))
        if raw:
            broker_positions = json.loads(raw)
            broker_map: dict[str, int] = {p['stock_code']: int(p['volume']) for p in broker_positions if int(p.get('volume', 0)) > 0}

            async with db_mod.async_session_factory() as session:
                result = await session.execute(
                    select(DailyPosition.stock_code, func.sum(DailyPosition.volume))
                    .where(DailyPosition.snapshot_date == snapshot_date, DailyPosition.account_id == account_id)
                    .group_by(DailyPosition.stock_code)
                )
                daily_map: dict[str, int] = {row[0]: int(row[1]) for row in result.all()}

            all_codes = sorted(set(broker_map.keys()) | set(daily_map.keys()))
            content = ''
            has_diff = False
            for code in all_codes:
                bv = broker_map.get(code, 0)
                dv = daily_map.get(code, 0)
                diff = dv - bv
                if diff != 0:
                    if has_diff:
                        content += '\n'
                    content += f"  DIFF {code}: broker={bv} daily={dv} diff={diff:+d}"
                    has_diff = True

            if not has_diff:
                print(f"Reconciliation OK: all {len(all_codes)} stocks matched")
            else:
                print(f"Reconciliation: {len(all_codes)} stocks checked, differences found above")
                await send_text(content)
        else:
            print("Broker positions not available in Redis, skip reconciliation")

        await db_mod.engine.dispose()

    asyncio.run(_sync())


if __name__ == "__main__":
    main()
