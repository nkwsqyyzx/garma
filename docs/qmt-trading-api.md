# QMT Trading API Reference

Base URL: `/api/v1/qmt`

All endpoints return a uniform JSON wrapper:

```json
{ "code": 0, "msg": "success", "data": <payload> }
```

Error responses:

```json
{ "code": <int>, "msg": "<error message>", "data": null }
```

---

## Table of Contents

1. [Market Data](#market-data)
2. [Account](#account)
3. [Trading](#trading)
4. [Strategy](#strategy)
5. [Kill Switch](#kill-switch)
6. [Health & Debug](#health--debug)

---

## Market Data

### GET /quote/snapshot

Batch query latest tick data for multiple stocks.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `codes` | string (query) | Yes | Comma-separated stock codes, e.g. `600519.SH,000001.SZ` |

**Response `data`:** `Record<string, Tick>` — keyed by stock code.

**Tick object:**

| Field | Type | Description |
|-------|------|-------------|
| `code` | string | Stock code |
| `time` | string | Tick timestamp |
| `open` | number | Open price |
| `high` | number | High price |
| `low` | number | Low price |
| `last` | number | Latest price |
| `close` | number | Previous close price |
| `amount` | number | Total amount |
| `volume` | number | Total volume |
| `ask1`..`ask5` | number | Ask prices (level 1-5) |
| `bid1`..`bid5` | number | Bid prices (level 1-5) |
| `ask_vol1`..`ask_vol5` | number | Ask volumes (level 1-5) |
| `bid_vol1`..`bid_vol5` | number | Bid volumes (level 1-5) |
| `change` | number | Price change = last - close |
| `pct_change` | number | Percent change (%) = change / close * 100 |
| `avg_price` | number | Average price = amount / (volume * 100) |

**Example:**

```
GET /api/v1/qmt/quote/snapshot?codes=600519.SH,000001.SZ
```

---

### GET /quote/tick/{code}

Single stock latest tick.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `code` | string (path) | Yes | Stock code, e.g. `600519.SH` |

**Response `data`:** Single `Tick` object (same shape as above), or `null` if no data.

---

### GET /quote/kline

Query K-line (candlestick) data.

**Parameters:**

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `code` | string (query) | Yes | — | Stock code |
| `period` | string (query) | No | `"1d"` | K-line period |
| `count` | int (query) | No | `100` | Number of bars (1-1000) |

**Response `data`:** `Array<KlineBar>`

**KlineBar:**

| Field | Type | Description |
|-------|------|-------------|
| `time` | string | Bar timestamp / date |
| `open` | number | Open price |
| `close` | number | Close price |
| `high` | number | High price |
| `low` | number | Low price |
| `volume` | number | Volume |
| `amount` | number | Amount |

> For `period="1d"`, the response appends or replaces the last bar with today's real-time bar derived from tick data.

---

### GET /quote/stock_names

Batch query stock names.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `codes` | string (query) | Yes | Comma-separated stock codes |

**Response `data`:** `Record<string, string>` — `{ "600519.SH": "贵州茅台", ... }`

---

## Account

All account data comes from Redis cache (pushed by QMT-Server). Returns `null` / `[]` when Redis has no data (e.g. weekends).

### GET /account/asset

Account asset / balance info.

**Response `data`:** `object | null` — raw QMT asset data from Redis. Keys depend on upstream data feed.

---

### GET /account/positions

Current positions list.

**Response `data`:** `Array<Position>`

**Position (DB fallback, explicit fields):**

| Field | Type | Description |
|-------|------|-------------|
| `stock_code` | string | Stock code |
| `stock_name` | string | Stock name |
| `volume` | number | Holding volume |
| `can_use_volume` | number | Available volume |
| `market_value` | number | Market value |
| `profit_loss` | number | Unrealized P&L |
| `profit_loss_ratio` | number | P&L ratio (%) |
| `open_price` | number | Average cost price |

> When Redis has data, returns the raw upstream format.

---

### GET /account/orders

Today's order list.

**Parameters:**

| Param | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `cancelable_only` | bool (query) | No | `false` | Only return cancelable orders |

**Response `data`:** `Array<object>` — raw order data from Redis. Filtered to orders with status `submitted`, `reported`, or `partial` when `cancelable_only=true`.

---

### GET /account/trades

Today's trade (fill) list.

**Response `data`:** `Array<object>` — raw trade data from Redis. Known keys: `traded_id`, `order_id`, `stock_code`, `stock_name`, `order_type`, `traded_volume`, `traded_price`, `traded_amount`, `traded_time`, `order_remark`.

---

## Trading

### POST /trade/order

Place a buy or sell order. Order is written to MySQL `qmt_orders` table and pushed to Redis command queue for execution by qmt-trade service.

> Blocked when kill switch is active (HTTP 403).

**Request body:**

```json
{
  "stock_code": "600519.SH",       // required, stock code
  "order_type": "buy",             // required, "buy" or "sell"
  "order_volume": 100,             // required, must be > 0
  "price_type": "limit",           // optional, "limit" | "market" | "best5", default "limit"
  "price": 1800.00,                // optional, order price (0 for market orders)
  "strategy_name": "momentum",     // optional, strategy name for tracking
  "order_remark": "strategy:factor:1:贵州茅台", // optional, remark string
  "linked_req_id": "alpha_xxx_xxx" // optional, links sell to a specific buy order
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `stock_code` | string | Yes | Stock code, e.g. `600519.SH` |
| `order_type` | string | Yes | `"buy"` or `"sell"` |
| `order_volume` | int | Yes | Order quantity, must be > 0 |
| `price_type` | string | No | `"limit"`, `"market"`, or `"best5"` (default: `"limit"`) |
| `price` | float | No | Limit price (default: `0.0`, use 0 for market orders) |
| `strategy_name` | string \| null | No | Strategy name |
| `order_remark` | string \| null | No | Remark, typically `strategy:factor:rank:stock_name` format |
| `linked_req_id` | string \| null | No | For sell orders: links to the buy order's `req_id` |

**Response `data`:**

```json
{ "req_id": "alpha_a1b2c3d4e5f6g7h8_1716364800" }
```

The `req_id` can be used to track order status via `GET /trade/order/{req_id}`.

---

### POST /trade/cancel

Cancel a specific order by its QMT order ID.

**Request body:**

```json
{
  "order_id": "12345"    // QMT 委托编号 (not req_id)
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `order_id` | string | Yes | QMT-assigned order ID |

**Response `data`:**

```json
{ "req_id": "alpha_cancel_a1b2c3d4e5f6_1716364800" }
```

---

### POST /trade/cancel_all

Cancel all pending orders.

**Response `data`:**

```json
{ "req_id": "alpha_cancelall_a1b2c3d4e5f6_1716364800" }
```

---

### GET /trade/order/{req_id}

Query order status by the system-generated `req_id` (returned by `place_order`).

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `req_id` | string (path) | Yes | Order request ID |

**Response `data`:** Order status object

| Field | Type | Description |
|-------|------|-------------|
| `req_id` | string | System request ID |
| `order_id` | string \| null | QMT-assigned order ID (null before accepted) |
| `stock_code` | string | Stock code |
| `stock_name` | string | Stock name |
| `order_type` | string | `"buy"` or `"sell"` |
| `order_volume` | int | Requested volume |
| `traded_volume` | int | Filled volume |
| `price_type` | string | `"limit"` / `"market"` / `"best5"` |
| `price` | float | Order price |
| `traded_price` | float \| null | Average fill price |
| `status` | string | Order status (e.g. `pending`, `submitted`, `filled`, `cancelled`, `rejected`) |
| `status_msg` | string | Status detail message |
| `strategy_name` | string \| null | Strategy name |
| `order_remark` | string \| null | Remark |
| `retry_count` | int | Number of retries |
| `created_at` | string \| null | ISO timestamp |
| `updated_at` | string \| null | ISO timestamp |

Returns `null` if order not found.

---

## Strategy

### GET /strategy/positions

Aggregated strategy holdings from the `strategy_trades` MySQL table. Groups buy/sell trades per stock+strategy+factor, shows only positions with remaining holding volume > 0. Enriches with real-time tick data and adjustment factors for accurate P&L.

**Response `data`:** `Array<StrategyPosition>`

| Field | Type | Description |
|-------|------|-------------|
| `stock_code` | string | Stock code |
| `volume` | int | Net holding volume (buy - sell) |
| `trade_date` | string | Trade date |
| `avg_price` | float | Average cost price |
| `other` | string | Remark field, format: `strategy:factor:rank:stock_name` |
| `cost` | float | Total cost |
| `pct_change` | float | Current percent change from tick |
| `current_price` | float | Current price from tick (fallback to avg_price) |
| `pnl` | float | Unrealized P&L (with adjustment factor) |
| `order_req_id` | string | Original order request ID |

---

### GET /strategy/trades

Strategy trade list from the `strategy_trades` MySQL table. Supports date fallback: if no trades on the given date, automatically returns the most recent trading day's data.

**Parameters:**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `trade_date` | string (query) | No | Date in `YYYY-MM-DD` format, defaults to today |

**Response `data`:** `Array<StrategyTrade>`

| Field | Type | Description |
|-------|------|-------------|
| `stock_code` | string | Stock code |
| `stock_name` | string | Stock name |
| `direction` | string | `"buy"` or `"sell"` |
| `price` | float | Trade price |
| `volume` | int | Trade volume |
| `amount` | float | Trade amount |
| `strategy` | string | Strategy name |
| `factor` | string | Factor name |
| `trade_date` | string | Trade date |
| `pct_change` | float | **Buy:** current pct change from tick; **Sell:** realized pct vs linked buy price; **0** if no linked buy |
| `pnl` | float | **Buy:** unrealized P&L (with adj factor); **Sell:** realized P&L = (sell_price - buy_price) * volume; **0** if no linked buy |

**P&L calculation details:**

- **Buy records:** `pnl = amount * calc_adjusted_return(avg_price, current_price, trade_date, adjustment_factors)`. Uses the `backend.utils.adjustment` module for dividend/split adjustment.
- **Sell records with `linked_req_id`:** Looks up the buy price via the linked buy trade's `order_req_id`. `pnl = (sell_price - buy_price) * volume`, `pct_change = (sell_price - buy_price) / buy_price * 100`.
- **Sell records without linked buy:** Both `pnl` and `pct_change` remain `0`.

---

## Kill Switch

### GET /kill-switch

Query kill switch state.

**Response `data`:**

```json
{ "active": false }
```

### POST /kill-switch

Activate kill switch — blocks all trading via `POST /trade/order` (returns HTTP 403).

**Response `data`:**

```json
{ "active": true }
```

### DELETE /kill-switch

Deactivate kill switch — resumes trading.

**Response `data`:**

```json
{ "active": false }
```

---

## Health & Debug

### GET /health

QMT-Server connection status.

**Response `data`:**

| Field | Type | Description |
|-------|------|-------------|
| `market` | object \| null | Market service status |
| `market.status` | string | Connection status |
| `market.level` | string | `"online"` / `"offline"` |
| `market.tick_delay` | float \| null | Tick delay in seconds |
| `trade` | object \| null | Trade service status |
| `trade.status` | string | Connection status |
| `trade.level` | string | `"online"` / `"offline"` |
| `online` | bool | True if at least one service is online |

---

### POST /proxy

Proxy request to QMT-Server (whitelist validation).

**Request body:**

```json
{
  "method": "GET",
  "path": "/api/v1/subscribe",
  "params": { "key": "value" },
  "body": null
}
```

---

## Data Architecture

```
Frontend  →  /api/v1/qmt/*  →  FastAPI (qmt.py)
                                    ↓
                              QmtService (qmt_service.py)
                              ├── Redis (read cache):  tick data, account data
                              ├── MySQL (read/write):  qmt_orders, strategy_trades
                              ├── qmt-market HTTP:     kline, extended data
                              └── Redis (write queue): trade commands
                                    ↓
                              qmt-trade service (consumes Redis queue, executes via QMT)
```

### Key Design Patterns

1. **Redis-first reads:** Account data (asset, positions, orders, trades) and tick data are read from Redis cache populated by QMT-Server push. Returns empty/null on cache miss (weekends).
2. **MySQL for strategy data:** `strategy_trades` table persists all strategy-aware trade records. The `strategy_positions` endpoint aggregates from this table with `GROUP BY` + `HAVING volume > 0`.
3. **Async command queue:** Orders are written to MySQL first (with a `req_id`), then pushed to a Redis list for the qmt-trade service to consume and execute.
4. **Adjustment factors:** Buy-side P&L uses `calc_adjusted_return()` which accounts for dividends and splits via pre-calculated adjustment factors stored in Redis.
5. **Date fallback:** Strategy queries default to today, but fall back to `MAX(trade_date)` when today has no data (useful on weekends/holidays).
6. **Order linking:** Sell orders can reference a buy order's `req_id` via `linked_req_id`, enabling realized P&L calculation for sell trades.
