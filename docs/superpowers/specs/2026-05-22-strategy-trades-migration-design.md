# Strategy Trades Migration Spec

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate strategy trade records from CSV file to MySQL, integrate with the order callback system, and replace the RPC-based strategy positions endpoint.

**Architecture:** Add a `strategy_trades` append-only table for recording every buy/sell event. When the QmtOrderWorker receives a `filled` callback, it reads the full `order_remark` from `qmt_orders`, parses strategy/factor metadata, and inserts a new row. The `/strategy/positions` API switches from reading qmt-market RPC to aggregating from this table. Existing CSV data is imported via a one-time script.

**Tech Stack:** SQLAlchemy async (aiomysql), FastAPI, Redis, Python csv module

---

## 1. Data Model

### 1.1 `strategy_trades` Table

Append-only trade journal. Every buy/sell generates one row.

| Column | Type | Nullable | Default | Description |
|---|---|---|---|---|
| `id` | BIGINT AUTO_INCREMENT PK | NO | | Surrogate primary key |
| `account_id` | VARCHAR(32) | NO | | Securities account ID |
| `stock_code` | VARCHAR(20) | NO | | Stock code (e.g. `600519.SH`) |
| `stock_name` | VARCHAR(50) | YES | NULL | Stock name |
| `direction` | VARCHAR(4) | NO | | `buy` or `sell` |
| `volume` | INT | NO | | Trade quantity |
| `price` | DECIMAL(12,4) | NO | | Average fill price |
| `amount` | DECIMAL(16,4) | NO | | Trade amount = volume * price |
| `strategy` | VARCHAR(100) | YES | NULL | Strategy name (e.g. `25年策略_指相_红三兵过滤_hfstd13`) |
| `factor` | VARCHAR(50) | YES | NULL | Factor name (e.g. `hf_std13`) |
| `remark` | VARCHAR(200) | YES | NULL | Original `其他` field content |
| `trade_date` | DATE | NO | | Trade execution date |
| `source` | VARCHAR(20) | NO | `order` | Origin: `order` (callback), `import` (CSV), `manual` |
| `order_req_id` | VARCHAR(64) | YES | NULL | Foreign key to `qmt_orders.req_id` |
| `linked_req_id` | VARCHAR(64) | YES | NULL | Links sell record to the original buy `order_req_id` |
| `created_at` | DATETIME | NO | `CURRENT_TIMESTAMP` | Row creation time |

**Indexes:**
- `idx_account_date` ON (`account_id`, `trade_date`)
- `idx_stock_date` ON (`stock_code`, `trade_date`)
- `idx_order_req_id` ON (`order_req_id`)

**Remark parsing convention:** `order_remark` format is `策略名:因子:序号:股票名` (colon-separated). Parse by splitting on `:` — first segment = strategy, second = factor, rest = remark. If format doesn't match, store raw value in `remark`, leave `strategy`/`factor` null.

### 1.2 `daily_positions` Table (schema only, not implemented this phase)

Daily snapshot of positions for fast historical queries. Will be populated by a post-market batch job in a future phase.

| Column | Type | Nullable | Description |
|---|---|---|---|
| `id` | BIGINT AUTO_INCREMENT PK | NO | |
| `account_id` | VARCHAR(32) | NO | |
| `snapshot_date` | DATE | NO | Snapshot date |
| `stock_code` | VARCHAR(20) | NO | |
| `stock_name` | VARCHAR(50) | YES | |
| `strategy` | VARCHAR(100) | YES | |
| `factor` | VARCHAR(50) | YES | |
| `remark` | VARCHAR(200) | YES | |
| `buy_date` | DATE | NO | Original buy date |
| `volume` | INT | NO | Holding quantity |
| `avg_price` | DECIMAL(12,4) | NO | Weighted average buy price |
| `cost` | DECIMAL(16,4) | NO | Total cost = volume * avg_price |
| `created_at` | DATETIME | NO | |

**Unique constraint:** `uk_snapshot_account_stock_strategy_buy` ON (`snapshot_date`, `account_id`, `stock_code`, `strategy`, `factor`, `buy_date`)

---

## 2. Order Callback Integration

### 2.1 Flow

```
QmtOrderWorker._handle_message()
  → qmt_service.update_order_from_event(event)     # existing: update qmt_orders
  → qmt_service.record_strategy_trade(event)        # NEW: insert strategy_trades
```

### 2.2 `record_strategy_trade()` Logic

Called when order status is `filled` or `partial` (with traded_volume > 0).

1. Extract `req_id` from event.
2. Query `qmt_orders` by `req_id` to get `strategy_name`, `order_remark`, `stock_code`, `stock_name`, `order_type`, `linked_req_id`.
3. Parse `order_remark` to extract strategy, factor, remark (see parsing convention in 1.1).
4. Use `order_type` as direction (`buy`/`sell`).
5. Use `traded_price` as price, `traded_volume` as volume.
6. If selling, store `linked_req_id` from the order to link this sell to the original buy position.
7. Insert into `strategy_trades`.

If the `qmt_orders` row is not found (edge case: order not in our system), skip silently and log a warning.

---

## 3. Strategy Positions API

### 3.1 Replace Data Source

The existing `GET /api/v1/qmt/strategy/positions` endpoint currently calls `qmt-market` RPC to read `{today}_成交缓存信息.hold`. Replace with MySQL aggregation.

### 3.2 Query Logic

```python
async def get_strategy_positions(self) -> list[dict]:
    # 1. Aggregate from strategy_trades
    #    SELECT stock_code, strategy, factor, remark, trade_date as buy_date,
    #           SUM(CASE WHEN direction='buy' THEN volume ELSE -volume END) AS holding_volume,
    #           SUM(CASE WHEN direction='buy' THEN amount ELSE -amount END) AS total_cost
    #    FROM strategy_trades
    #    WHERE account_id = ?
    #    GROUP BY stock_code, strategy, factor, remark, trade_date
    #    HAVING holding_volume > 0

    # 2. Collect distinct stock_codes, batch get real-time prices via get_snapshot()

    # 3. Compute current_price, pnl, pct_change per position
    #    avg_price = total_cost / holding_volume
    #    pnl = (current_price - avg_price) * holding_volume
    #    pct_change = (current_price - avg_price) / avg_price * 100

    # 4. Return list matching StrategyPosition interface
```

### 3.3 Response Format (unchanged)

```json
{
  "code": 0,
  "msg": "success",
  "data": [
    {
      "stock_code": "301166.SZ",
      "volume": 3200,
      "trade_date": "2026-05-19",
      "avg_price": 31.3344,
      "other": "25年策略_指相_红三兵过滤_hfstd13:hf_std13:2:优宁维",
      "cost": 100270.0,
      "pct_change": -2.5,
      "current_price": 30.55,
      "pnl": -2510.0,
      "order_req_id": "日内择时+20260521盘中9030800"
    }
  ]
}
```

---

## 4. CSV Import

### 4.1 One-time Script

File: `backend/scripts/import_csv_trades.py`

- Reads CSV file path from command line arg (default: `/tmp/1/b.csv`)
- Maps CSV columns to `strategy_trades` fields
- All records: `direction='buy'`, `source='import'`, `account_id` from config
- Column mapping:
  - `证券代码` → `stock_code`
  - `策略名称` → use as fallback for strategy if parsing from `其他` fails
  - `持仓量` → `volume`
  - `成交均价` → `price`
  - `持仓成本` → `amount`
  - `交易日期` → `trade_date` (parse date string)
  - `其他` → parse for strategy/factor/remark, also store as `remark`
  - `策略` → `strategy`
  - `因子` → `factor`
  - `订单标记` → `order_req_id`
- Idempotent: skip if row with same (account_id, stock_code, direction, volume, price, trade_date, strategy, factor) already exists
- Run: `python -m backend.scripts.import_csv_trades /tmp/1/b.csv`

---

## 5. Scope

### This Phase
- [x] Create `strategy_trades` table and ORM model
- [x] Create `daily_positions` table schema (model only, no population logic)
- [x] Implement `record_strategy_trade()` in QmtService
- [x] Integrate into QmtOrderWorker callback flow
- [x] Replace `get_strategy_positions()` to query from MySQL
- [x] CSV import script

### Future Phase
- [ ] Post-market daily position snapshot job
- [ ] Trade history query API (`GET /strategy/trades`)
- [ ] Position reconciliation with broker data
