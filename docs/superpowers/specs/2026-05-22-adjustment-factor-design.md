# 复权因子计算设计

> **Status:** Approved

**Goal:** 在 backend/utils 中提供复权收益率计算工具，用于持仓盈亏计算时考虑除权除息的影响。

## 数据源

Redis key `股票复权信息`，由外部脚本 `fun_计算最新的股票基础信息()` 每日写入。格式：gzip + pickle 序列化的 dict，反序列化后为 DataFrame：

| 列名 | 类型 | 说明 |
|---|---|---|
| `证券代码` | str | 如 `510310.SH` |
| `交易日期` | datetime | 除权日期 |
| `复权因子` | float | `收盘价 / 收盘价_复权` |

每行代表一次除权事件。非除权日无记录。

## 模块设计

**文件**: `backend/utils/adjustment.py`

### `get_adjustment_factors`

```python
async def get_adjustment_factors(
    codes: list[str], redis_url: str
) -> dict[str, list[tuple[date, float]]]:
```

- 从 Redis 加载 `股票复权信息`，反序列化为 DataFrame
- 当日缓存（`_cache_date` + `_cache` 避免重复反序列化）
- 筛选目标 codes，返回 `{code: [(date, factor), ...]}` 按日期排序
- 无除权记录的 code 返回空列表

### `calc_adjusted_return`

```python
def calc_adjusted_return(
    buy_price: float,
    current_price: float,
    buy_date: date,
    factors: list[tuple[date, float]],
) -> float:
```

纯函数，无 IO：

1. 遍历 factors，筛出 `buy_date < factor_date` 的记录
2. `cumulative_factor = Π(factor)` （累积乘积）
3. `return (current_price / buy_price) * cumulative_factor - 1`

当无除权记录时，`cumulative_factor = 1`，退化为普通收益率。

## 使用示例

```python
from backend.utils.adjustment import get_adjustment_factors, calc_adjusted_return

# 获取复权因子
factors = await get_adjustment_factors(["510310.SH", "000591.SZ"], settings.REDIS_URL)

# 计算某只股票的复权收益率
adj_ret = calc_adjusted_return(
    buy_price=4.71,
    current_price=4.69,
    buy_date=date(2026, 5, 8),
    factors=factors.get("510310.SH", []),
)
```

## 后续集成点

- `daily_positions` 盈亏计算
- `get_strategy_positions` 中的 pnl/pct_change
