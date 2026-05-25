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
        #    按 (stock_code, strategy, factor) 分组抵消买卖，
        #    避免按 position_req_id 分组导致超额卖出被丢弃
        async with db_mod.async_session_factory() as session:
            # 1a. 按 stock_code + strategy + factor 聚合净持仓
            holding_stmt = (
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
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
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                )
                .having(text("holding_volume > 0"))
            )
            holding_result = await session.execute(holding_stmt)
            holdings = holding_result.all()

        if not holdings:
            print(f"No open positions found for account {account_id}")
            return

        # 1b. 取每条持仓对应买入记录的元数据（取最早的买入记录）
        buy_meta: dict[tuple, tuple] = {}  # (stock_code, strategy, factor) -> (remark, trade_date)
        async with db_mod.async_session_factory() as session:
            meta_result = await session.execute(
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    StrategyTrade.trade_date,
                )
                .where(
                    StrategyTrade.direction == "buy",
                    StrategyTrade.account_id == account_id,
                )
                .order_by(StrategyTrade.trade_date)
            )
            for row in meta_result.all():
                key = (row[0], row[1], row[2])
                if key not in buy_meta:
                    buy_meta[key] = (row[3], row[4])

        # 2. 获取最新股票名称（Redis 优先，remark 兜底）
        codes = list({h.stock_code for h in holdings})
        redis_names = await asyncio.to_thread(_load_stock_names_from_redis, settings.REDIS_URL)
        name_map: dict[str, str] = {}
        for h in holdings:
            if h.stock_code not in name_map:
                meta = buy_meta.get((h.stock_code, h.strategy, h.factor))
                remark = meta[0] if meta else None
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

                meta = buy_meta.get((h.stock_code, h.strategy, h.factor))
                remark = meta[0] if meta else None
                trade_date = meta[1] if meta else date.today()

                pos = DailyPosition(
                    account_id=account_id,
                    snapshot_date=snapshot_date,
                    stock_code=h.stock_code,
                    stock_name=name_map.get(h.stock_code),
                    strategy=h.strategy,
                    factor=h.factor,
                    remark=remark,
                    order_req_id=f"{h.strategy or ''}|{h.factor or ''}",
                    buy_date=trade_date,
                    volume=volume,
                    avg_price=avg_price,
                    cost=cost,
                )
                session.add(pos)
                inserted += 1

            await session.commit()

        print(f"Synced {inserted} positions to daily_positions for {snapshot_date}")

        # 4b. 计算复权盈亏概览
        from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return
        adj_factors = await get_adjustment_factors(codes, settings.REDIS_URL)

        # 从 Redis 获取最新收盘价
        import json as _json
        snapshot_raw = await asyncio.to_thread(
            lambda: __import__('redis').from_url(settings.REDIS_URL).get('qmt:account:snapshot')
        )
        price_map: dict[str, float] = {}
        if snapshot_raw:
            try:
                for item in _json.loads(snapshot_raw):
                    price_map[item.get("code", "")] = float(item.get("last", 0))
            except Exception:
                pass

        total_pnl = 0.0
        for h in holdings:
            meta = buy_meta.get((h.stock_code, h.strategy, h.factor))
            trade_date = meta[1] if meta else date.today()
            buy_volume = int(h.buy_volume)
            buy_cost = float(h.buy_cost)
            avg_price = buy_cost / buy_volume if buy_volume > 0 else 0
            current_price = price_map.get(h.stock_code, 0)
            if current_price > 0 and avg_price > 0:
                adj_ret = calc_adjusted_return(
                    avg_price, current_price, trade_date,
                    adj_factors.get(h.stock_code, []),
                )
                pnl = buy_cost * adj_ret
                total_pnl += pnl
                print(f"  {h.stock_code}: adj_ret={adj_ret:+.4%} pnl={pnl:+.2f} (buy={avg_price:.4f} cur={current_price:.4f})")
        print(f"Total adjusted PnL: {total_pnl:+.2f}")

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
