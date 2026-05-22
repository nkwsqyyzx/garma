# Strategy Trades Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create `strategy_trades` / `daily_positions` ORM models, integrate trade recording into the order callback flow, replace the strategy positions API data source from RPC to MySQL aggregation, and provide a CSV import script.

**Architecture:** New `StrategyTrade` and `DailyPosition` SQLAlchemy models in `backend/models/`. New `record_strategy_trade()` method on QmtService that queries `qmt_orders` by req_id, parses remark, and inserts a row. The order worker calls this after `update_order_from_event`. The existing `get_strategy_positions()` switches from HTTP RPC to a SQL GROUP BY aggregation.

**Tech Stack:** SQLAlchemy async (aiomysql), FastAPI, Python csv/stdlib

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `backend/models/strategy_trade.py` | **Create** | `StrategyTrade` ORM model |
| `backend/models/daily_position.py` | **Create** | `DailyPosition` ORM model (schema only) |
| `backend/models/__init__.py` | **Modify** | Re-export new models |
| `backend/service/qmt_service.py` | **Modify** | Add `record_strategy_trade()`, rewrite `get_strategy_positions()` |
| `backend/worker/qmt_order_worker.py` | **Modify** | Call `record_strategy_trade()` after order update |
| `backend/scripts/import_csv_trades.py` | **Create** | One-time CSV import script |

---

### Task 1: Create ORM Models

**Files:**
- Create: `backend/models/strategy_trade.py`
- Create: `backend/models/daily_position.py`

- [ ] **Step 1: Create `strategy_trade.py`**

```python
"""策略成交流水 ORM 模型。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, Integer, DECIMAL, Date, DateTime, Index,
)
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class StrategyTrade(Base):
    __tablename__ = "strategy_trades"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    account_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="证券账号")
    stock_code: Mapped[str] = mapped_column(String(20), nullable=False, comment="股票代码")
    stock_name: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="股票名称")
    direction: Mapped[str] = mapped_column(String(4), nullable=False, comment="buy/sell")
    volume: Mapped[int] = mapped_column(Integer, nullable=False, comment="成交数量")
    price: Mapped[float] = mapped_column(DECIMAL(12, 4), nullable=False, comment="成交均价")
    amount: Mapped[float] = mapped_column(DECIMAL(16, 4), nullable=False, comment="成交金额")
    strategy: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="策略名")
    factor: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="因子")
    remark: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="备注(原始其他字段)")
    trade_date: Mapped[date] = mapped_column(Date, nullable=False, comment="成交日期")
    source: Mapped[str] = mapped_column(String(20), nullable=False, server_default="order", comment="来源: order/import/manual")
    order_req_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="关联 qmt_orders.req_id")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default="CURRENT_TIMESTAMP", comment="创建时间")

    __table_args__ = (
        Index("idx_account_date", "account_id", "trade_date"),
        Index("idx_stock_date", "stock_code", "trade_date"),
        Index("idx_order_req_id", "order_req_id"),
    )
```

- [ ] **Step 2: Create `daily_position.py`**

```python
"""每日持仓快照 ORM 模型（盘后定时任务生成，本期仅建表）。"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger, String, Integer, DECIMAL, Date, DateTime, UniqueConstraint,
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
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default="CURRENT_TIMESTAMP", comment="创建时间")

    __table_args__ = (
        UniqueConstraint(
            "snapshot_date", "account_id", "stock_code", "strategy", "factor", "buy_date",
            name="uk_snapshot_account_stock_strategy_buy",
        ),
    )
```

- [ ] **Step 3: Update `backend/models/__init__.py` to re-export**

Read current `backend/models/__init__.py`. Add imports for the new models:

```python
from backend.models.strategy_trade import StrategyTrade
from backend.models.daily_position import DailyPosition
```

(Only add these lines — keep existing `QmtOrder` import if present.)

- [ ] **Step 4: Create database tables**

Run from `garma/` directory to let Python create the tables via `Base.metadata.create_all`:

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -c "
import asyncio
from backend.database import init_db, engine, Base
from backend.models.qmt_order import QmtOrder
from backend.models.strategy_trade import StrategyTrade
from backend.models.daily_position import DailyPosition

async def main():
    await init_db()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('Tables created:', list(Base.metadata.tables.keys()))
    await engine.dispose()

asyncio.run(main())
"
```

Expected output: `Tables created: ['qmt_orders', 'strategy_trades', 'daily_positions']`

- [ ] **Step 5: Verify tables in MySQL**

```bash
mysql -u root garma -e "DESCRIBE strategy_trades; DESCRIBE daily_positions;"
```

Expected: both tables show their columns.

- [ ] **Step 6: Commit**

```bash
git add backend/models/strategy_trade.py backend/models/daily_position.py backend/models/__init__.py
git commit -m "feat: add StrategyTrade and DailyPosition ORM models"
```

---

### Task 2: Implement `record_strategy_trade()` in QmtService

**Files:**
- Modify: `backend/service/qmt_service.py`

- [ ] **Step 1: Add imports at the top of `qmt_service.py`**

Add to existing imports section (after line 35, alongside other backend.model imports):

```python
from backend.models.strategy_trade import StrategyTrade
```

- [ ] **Step 2: Add remark parsing helper function**

Add a module-level function near the top of the file (after imports, before class definition, around line 50):

```python
def _parse_remark(remark: str | None) -> tuple[str | None, str | None, str | None]:
    """解析 order_remark 为 (strategy, factor, remark)。

    格式: 策略名:因子:序号:股票名  或  策略名$子策略:因子:序号:股票名
    如果不匹配冒号分隔格式，返回 (None, None, 原始值)。
    """
    if not remark:
        return None, None, None
    parts = remark.split(":")
    if len(parts) >= 4:
        return parts[0], parts[1], remark
    return None, None, remark
```

- [ ] **Step 3: Add `record_strategy_trade()` method**

Insert this method right after the existing `update_order_from_event()` method (after line 538):

```python
    async def record_strategy_trade(self, event: dict) -> None:
        """订单成交时写入 strategy_trades 流水记录。"""
        req_id = event.get("req_id")
        status = event.get("status", "")
        if not req_id:
            return
        # 仅在成交时记录
        if status not in ("partial", "filled"):
            return
        traded_volume = event.get("traded_volume")
        traded_price = event.get("traded_price")
        if not traded_volume or traded_volume <= 0:
            return

        # 查 qmt_orders 获取完整的 strategy_name / order_remark
        async with self._db_session_factory() as session:
            result = await session.execute(
                select(QmtOrder).where(QmtOrder.req_id == req_id)
            )
            order = result.scalar_one_or_none()

        if not order:
            logger.warning("record_strategy_trade: order not found req_id={}", req_id)
            return

        strategy, factor, remark = _parse_remark(order.order_remark)
        # 如果 remark 解析不出 strategy，fallback 到 order.strategy_name
        if not strategy and order.strategy_name:
            strategy = order.strategy_name

        volume = int(traded_volume)
        price = float(traded_price or 0)
        direction = "buy" if order.order_type == "buy" else "sell"

        async with self._db_session_factory() as session:
            trade = StrategyTrade(
                account_id=order.account_id,
                stock_code=order.stock_code,
                stock_name=order.stock_name,
                direction=direction,
                volume=volume,
                price=price,
                amount=round(volume * price, 4),
                strategy=strategy,
                factor=factor,
                remark=remark,
                trade_date=date.today(),
                source="order",
                order_req_id=req_id,
            )
            session.add(trade)
            await session.commit()

        logger.info("Strategy trade recorded: req_id={} {} {} vol={} price={}",
                     req_id, direction, order.stock_code, volume, price)
```

Note: `date` is already imported at line 15 of `qmt_service.py`. `select` is already imported at line 21.

- [ ] **Step 4: Verify import works**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -c "from backend.service.qmt_service import QmtService; print('OK')"
```

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add backend/service/qmt_service.py
git commit -m "feat: add record_strategy_trade() to QmtService"
```

---

### Task 3: Integrate into QmtOrderWorker

**Files:**
- Modify: `backend/worker/qmt_order_worker.py`

- [ ] **Step 1: Add `record_strategy_trade()` call in `_handle_message()`**

In `backend/worker/qmt_order_worker.py`, after the existing `update_order_from_event` call (line 121) and before the notification block (line 124), insert:

```python
            # 写入策略成交流水
            if status in ("partial", "filled") and self._service:
                try:
                    await self._service.record_strategy_trade(event)
                except Exception:
                    logger.exception("Failed to record strategy trade for req_id={}", req_id)
```

The full `_handle_message` method becomes:

```python
    async def _handle_message(self, msg_id: str, fields: dict) -> None:
        """处理单条订单事件消息。"""
        try:
            # fields 中 data 字段包含 JSON
            raw = fields.get("data") or fields.get("event")
            if not raw:
                logger.warning("Empty event data in msg {}", msg_id)
                await self._ack(msg_id)
                return

            if isinstance(raw, str):
                event = json.loads(raw)
            else:
                event = raw

            req_id = event.get("req_id")
            status = event.get("status", "")

            if not req_id:
                logger.warning("Event missing req_id: {}", event)
                await self._ack(msg_id)
                return

            # 更新 MySQL
            if self._service:
                await self._service.update_order_from_event(event)

            # 写入策略成交流水
            if status in ("partial", "filled") and self._service:
                try:
                    await self._service.record_strategy_trade(event)
                except Exception:
                    logger.exception("Failed to record strategy trade for req_id={}", req_id)

            # 成交/失败通知（预留企业微信接口）
            if status in ("filled", "rejected"):
                await self._send_notification(event)

            # 处理成功 → ACK
            await self._ack(msg_id)
            logger.debug("Order event processed: req_id={} status={}", req_id, status)

        except json.JSONDecodeError:
            logger.error("Invalid JSON in order event msg {}", msg_id)
            await self._ack(msg_id)  # 无效数据直接 ACK，避免重复消费
        except Exception:
            logger.exception("Failed to handle order event msg {}", msg_id)
            # 不 ACK，下次重投递
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -c "from backend.worker.qmt_order_worker import QmtOrderWorker; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/worker/qmt_order_worker.py
git commit -m "feat: integrate strategy trade recording into order worker"
```

---

### Task 4: Rewrite `get_strategy_positions()` to use MySQL

**Files:**
- Modify: `backend/service/qmt_service.py`

- [ ] **Step 1: Replace `get_strategy_positions()` method**

Replace the entire existing `get_strategy_positions()` method (lines 574–618) with:

```python
    async def get_strategy_positions(self) -> list[dict]:
        """从 strategy_trades 流水表聚合当前持仓，补充实时行情。"""
        from sqlalchemy import func, case

        async with self._db_session_factory() as session:
            stmt = (
                select(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
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
                .where(StrategyTrade.account_id == self._config.QMT_ACCOUNT_ID)
                .group_by(
                    StrategyTrade.stock_code,
                    StrategyTrade.strategy,
                    StrategyTrade.factor,
                    StrategyTrade.remark,
                    StrategyTrade.trade_date,
                )
                .having(text("holding_volume > 0"))
            )
            result = await session.execute(stmt)
            rows = result.all()

        if not rows:
            return []

        # 批量获取实时行情
        codes = list({r.stock_code for r in rows})
        snapshot = await self.get_snapshot(codes)

        results = []
        for r in rows:
            volume = int(r.holding_volume)
            total_cost = float(r.total_cost)
            avg_price = total_cost / volume if volume > 0 else 0
            tick = snapshot.get(r.stock_code, {})
            current_price = tick.get("last", avg_price)
            pct_change = tick.get("pct_change", 0)
            pnl = (current_price - avg_price) * volume if avg_price else 0

            results.append({
                "stock_code": r.stock_code,
                "volume": volume,
                "trade_date": str(r.trade_date),
                "avg_price": avg_price,
                "other": r.remark or "",
                "cost": total_cost,
                "pct_change": pct_change,
                "current_price": current_price,
                "pnl": pnl,
            })
        return results
```

Note: `text` needs to be added to the SQLAlchemy import at line 21. Change:
```python
from sqlalchemy import select
```
to:
```python
from sqlalchemy import select, text
```

- [ ] **Step 2: Verify import works**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -c "from backend.service.qmt_service import QmtService; print('OK')"
```

Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add backend/service/qmt_service.py
git commit -m "feat: rewrite get_strategy_positions() to aggregate from MySQL"
```

---

### Task 5: CSV Import Script

**Files:**
- Create: `backend/scripts/__init__.py`
- Create: `backend/scripts/import_csv_trades.py`

- [ ] **Step 1: Create `backend/scripts/__init__.py`**

```python
```

(Empty file to make `backend.scripts` a package.)

- [ ] **Step 2: Create `backend/scripts/import_csv_trades.py`**

```python
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
    from backend.database import init_db, async_session_factory
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
        inserted = 0
        skipped = 0

        async with async_session_factory() as session:
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
                    order_req_id=None,
                )
                session.add(trade)
                inserted += 1

            await session.commit()

        print(f"Import complete: {inserted} inserted, {skipped} skipped")

    asyncio.run(_import())


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run import**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -m backend.scripts.import_csv_trades /tmp/1/b.csv
```

Expected: `Found 62 rows` / `Import complete: 62 inserted, 0 skipped`

- [ ] **Step 4: Verify data in MySQL**

```bash
mysql -u root garma -e "SELECT COUNT(*) as total, source FROM strategy_trades GROUP BY source; SELECT stock_code, direction, volume, price, strategy, factor, trade_date FROM strategy_trades LIMIT 5;"
```

Expected: 62 rows with `source='import'`, valid stock codes, strategies, and factors.

- [ ] **Step 5: Commit**

```bash
git add backend/scripts/__init__.py backend/scripts/import_csv_trades.py
git commit -m "feat: add CSV import script for strategy trades"
```

---

### Task 6: Verify Full Integration

**Files:** None (verification only)

- [ ] **Step 1: Start backend and test the positions API**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -m backend.main
```

Then in another terminal:

```bash
curl -s http://localhost:8998/api/v1/qmt/strategy/positions | python -m json.tool
```

Expected: JSON response with `code: 0` and `data` array containing positions aggregated from the imported CSV data, enriched with real-time prices.

- [ ] **Step 2: Verify idempotency of import script**

```bash
cd /Users/C2H5OH/work/mine/phoenix/garma
python -m backend.scripts.import_csv_trades /tmp/1/b.csv
```

Note: Since this is an append-only table, running twice will create duplicates. This is acceptable for a one-time script — the user should only run it once. If needed, delete with `DELETE FROM strategy_trades WHERE source='import'` before re-running.

- [ ] **Step 3: Commit any final fixes**

```bash
git add -A
git commit -m "fix: any issues found during integration verification"
```
