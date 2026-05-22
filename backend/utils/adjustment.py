"""复权因子计算工具。

从 Redis 加载历史除权数据，计算复权后的真实收益率，
用于持仓盈亏计算时考虑除权除息的影响。

用法:
    from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return

    factors = await get_adjustment_factors(["510310.SH"], settings.REDIS_URL)
    ret = calc_adjusted_return(4.71, 4.69, date(2026, 5, 8), factors.get("510310.SH", []))
"""

import gzip
import pickle
from datetime import date
from typing import Optional

from loguru import logger

# 当日缓存
_cache: dict[str, list[tuple[date, float]]] = {}
_cache_date: str = ""


async def get_adjustment_factors(
    codes: list[str], redis_url: str
) -> dict[str, list[tuple[date, float]]]:
    """从 Redis 加载指定股票的复权因子。

    Returns: {code: [(date, factor), ...]} 按日期排序，无记录的 code 返回空列表
    """
    import asyncio
    import redis as _redis

    global _cache, _cache_date

    today = date.today().isoformat()
    if _cache_date != today:
        _cache = {}
        _cache_date = today

    if not _cache:
        def _load():
            r = _redis.from_url(redis_url)
            raw = r.get("股票复权信息")
            r.close()
            if not raw:
                return {}
            try:
                data = pickle.loads(gzip.decompress(raw))
                # data 是 dict of lists: {col_name: [values...]}
                codes_list = data.get("证券代码", [])
                dates_list = data.get("交易日期", [])
                factors_list = data.get("复权因子", [])
                result: dict[str, list[tuple[date, float]]] = {}
                for i in range(len(codes_list)):
                    code = codes_list[i]
                    d = dates_list[i]
                    f = factors_list[i]
                    # 日期可能是 Timestamp 或 date
                    if hasattr(d, "date"):
                        d = d.date()
                    elif isinstance(d, str):
                        d = date.fromisoformat(d[:10])
                    result.setdefault(code, []).append((d, float(f)))
                # 按日期排序
                for k in result:
                    result[k].sort()
                return result
            except Exception as e:
                logger.error("Failed to load adjustment factors: {}", e)
                return {}

        _cache = await asyncio.to_thread(_load)

    return {c: _cache.get(c, []) for c in codes}


def calc_adjusted_return(
    buy_price: float,
    current_price: float,
    buy_date: date,
    factors: list[tuple[date, float]],
) -> float:
    """计算复权收益率。

    Args:
        buy_price: 买入价格
        current_price: 当前价格
        buy_date: 买入日期
        factors: [(date, factor), ...] 该股票的所有除权记录（已排序）

    Returns: 复权收益率，如 0.05 表示 5%
    """
    cumulative = 1.0
    for factor_date, factor in factors:
        if factor_date > buy_date:
            cumulative *= factor

    if buy_price <= 0:
        return 0.0
    return (current_price / buy_price) * cumulative - 1.0
