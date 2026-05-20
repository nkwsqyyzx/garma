# QMT Trading Terminal Frontend Design

## Overview

Build a responsive SPA trading terminal for the Garma QMT system. The frontend provides account overview, position management, stock charts, order/trade history, and trade execution. Served by the existing FastAPI backend.

## Tech Stack

- **Framework**: Vue 3 + TypeScript + Vite
- **UI Library**: Element Plus
- **Charts**: ECharts (candlestick K-line, volume)
- **Data**: WebSocket push + HTTP on-demand
- **Responsive**: PC (sidebar nav) + Mobile (bottom tab nav), breakpoint 768px

## CDN Strategy

Large libraries loaded via domestic CDN (e.g. bootcdn, cdnjs, unpkg domestic mirror), not bundled:

| Library | CDN Source | Usage |
|---------|-----------|-------|
| ECharts | `https://cdn.bootcdn.net/ajax/libs/echarts/5.x/echarts.min.js` | K-line chart, data visualization |
| Vue 3 | `https://cdn.bootcdn.net/ajax/libs/vue/3.x/vue.global.prod.js` | Framework runtime |
| Element Plus | `https://cdn.bootcdn.net/ajax/libs/element-plus/2.x/index.full.min.js` | UI components |
| Element Plus CSS | `https://cdn.bootcdn.net/ajax/libs/element-plus/2.x/index.min.css` | UI styles |

Vite config uses `build.rollupOptions.external` + `output.globals` to exclude these from bundle.

`index.html` includes `<script>` / `<link>` tags for CDN resources, with fallback to local if CDN fails.

## Project Structure

```
garma/
├── backend/                    # Existing FastAPI (modified)
│   ├── main.py                 # + StaticFiles mount, SPA fallback, CORS
│   ├── api/qmt.py              # + stock_names endpoint
│   ├── service/qmt_service.py  # + stock_names, WebSocket data push
│   └── static/                 # Build output (gitignored)
├── frontend/                   # New Vue 3 SPA
│   ├── src/
│   │   ├── layouts/
│   │   │   └── MainLayout.vue          # Responsive layout container
│   │   ├── views/
│   │   │   ├── Dashboard.vue           # Asset overview + position list
│   │   │   ├── PositionDetail.vue      # K-line chart + order book + quick trade
│   │   │   ├── Orders.vue              # Today's orders
│   │   │   ├── Trades.vue              # Today's trades
│   │   │   └── Trade.vue               # Order placement form
│   │   ├── components/
│   │   │   ├── KlineChart.vue          # ECharts candlestick chart
│   │   │   ├── OrderBook.vue           # 5-level bid/ask display
│   │   │   ├── PositionTable.vue       # Position table (PC) / card list (mobile)
│   │   │   ├── OrderTable.vue          # Order table (PC) / card list (mobile)
│   │   │   ├── TradeTable.vue          # Trade table (PC) / card list (mobile)
│   │   │   ├── TradeForm.vue           # Buy/sell order form
│   │   │   ├── AssetCard.vue           # Account asset summary card
│   │   │   └── StatusBar.vue           # Service connection status
│   │   ├── composables/
│   │   │   ├── useWebSocket.ts         # WS connection, auto-reconnect, message dispatch
│   │   │   └── useBreakpoint.ts        # Responsive breakpoint detection
│   │   ├── api/
│   │   │   └── qmt.ts                  # All /api/v1/qmt/* HTTP requests
│   │   ├── stores/
│   │   │   ├── account.ts              # Reactive state: asset, positions, orders, trades
│   │   │   └── connection.ts           # WebSocket connection state
│   │   ├── router/
│   │   │   └── index.ts                # Vue Router config
│   │   ├── App.vue
│   │   └── main.ts
│   ├── index.html
│   ├── vite.config.ts                  # Dev proxy to backend:8000
│   ├── tsconfig.json
│   └── package.json
```

## Routes

| Path | Component | Description |
|------|-----------|-------------|
| `/` | Dashboard | Asset card + position table |
| `/position/:code` | PositionDetail | K-line chart + order book + quick trade |
| `/orders` | Orders | Today's order list with cancel buttons |
| `/trades` | Trades | Today's trade records |
| `/trade` | Trade | Order placement form |

## Backend Changes

### 1. Static Files & SPA Fallback

In `backend/main.py`:

- Mount `StaticFiles(directory="static/assets")` for `/assets/*`
- Add catch-all route returning `index.html` for Vue Router history mode
- Add CORS middleware (allow `localhost:5173` in development)

### 2. New Endpoint: Stock Names

```
GET /api/v1/qmt/quote/stock_names?codes=600519.SH,000001.SZ
```

Implementation:
- Read Redis key `股票基础信息` (gzip + pickle compressed DataFrame)
- Extract `股票名称` column, filter by requested codes
- In-memory cache with daily TTL (avoid repeated decompression)
- Response: `{"code": 0, "msg": "success", "data": {"600519.SH": "贵州茅台", ...}}`

### 3. New WebSocket: Data Push

```
WS /ws/qmt-data
```

Protocol:
- On connect: push full snapshot of asset, positions, orders, trades
- Client sends subscription control: `{"action": "subscribe", "types": ["asset", "positions"]}`
- Server pushes on data change: `{"type": "positions", "data": [...]}`
- Backend monitors Redis key changes (Keyspace Notification or Pub/Sub) to trigger pushes
- Each connection managed via `asyncio.Queue`

### 4. No Changes to Existing API Endpoints

All 14 existing endpoints remain unchanged. The new endpoint and WebSocket are additions only.

## Frontend Component Design

### MainLayout.vue

- **PC (>=768px)**: Fixed left sidebar with `el-menu` vertical navigation
- **Mobile (<768px)**: Bottom tab bar navigation
- Top `StatusBar` showing WebSocket connection status (both layouts)
- Uses `useBreakpoint()` composable for responsive switching

### Dashboard (`/`)

**AssetCard**: Displays total asset, available cash, market value, P&L from `/account/asset`.

**PositionTable**:
- PC: `el-table` with columns: name, code, volume, market value, P&L, P&L%, avg price
- Mobile: `el-card` list showing name, P&L%, market value
- Click row/card navigates to `/position/:code`

### PositionDetail (`/position/:code`)

**KlineChart** (`KlineChart.vue`):
- ECharts candlestick + volume bar chart
- Period selector: 1d, 1h, 15m, 5m
- Touch zoom/drag on mobile
- Responsive resize via `ResizeObserver`
- Data from `/quote/kline`

**OrderBook** (`OrderBook.vue`):
- 5-level bid/ask display from `/quote/tick/:code`
- Shows: bid/ask prices, volumes, spread
- 3s HTTP polling while page is active

**Quick Trade**: Embedded `TradeForm` with pre-filled stock code

### Orders (`/orders`)

**OrderTable**:
- PC: full `el-table` with all order fields + status tag + cancel button
- Mobile: card list with key info + status badge
- Cancelable orders show `el-button` calling `/trade/cancel`
- Top: "Cancel All" button calling `/trade/cancel_all`

### Trades (`/trades`)

**TradeTable**:
- Same table/card dual layout pattern
- Fields: time, code, name, direction, price, volume, amount

### Trade (`/trade`)

**TradeForm** (`TradeForm.vue`):
- Stock code input with name auto-complete (calls `/quote/stock_names`)
- Direction toggle: Buy / Sell
- Price input with quick buttons: limit up / current / limit down (from `/quote/snapshot`)
- Quantity input with fraction buttons: all / 1/2 / 1/3 / 1/4
- Max quantity auto-calculated (buy: by available cash, sell: by available volume)
- Submit calls `/trade/order`
- Confirmation dialog before submission

### StatusBar

- Shows connection status of market service and trade service
- Green dot = connected, red dot = disconnected
- Data from WebSocket `/ws/qmt-data` or existing `/ws/qmt-status`

## Data Flow

### WebSocket Push (primary data channel)

```
qmt-trade service
    │ (event callback)
    ▼
Redis (qmt:account:* keys updated)
    │ (Keyspace Notification / Pub/Sub)
    ▼
Backend /ws/qmt-data handler
    │ (asyncio.Queue per connection)
    ▼
Frontend useWebSocket composable
    │ (message dispatch by type)
    ▼
Pinia stores (account.ts)
    │ (reactive state)
    ▼
Components auto-update
```

### HTTP On-Demand

| Data | When | Endpoint |
|------|------|----------|
| K-line chart | Enter position detail page, switch period | `GET /quote/kline` |
| Tick / Order book | Position detail page active, 3s interval | `GET /quote/tick/:code` |
| Stock names | Page init, cache in memory | `GET /quote/stock_names` |

### Subscription Strategy

Each page subscribes only to needed data types:

| Page | Subscribed types |
|------|-----------------|
| Dashboard | asset, positions |
| Position Detail | positions (for P&L update) |
| Orders | orders |
| Trades | trades |
| Trade | asset, positions, orders |

## Responsive Design

- **Breakpoint**: 768px (Element Plus `xs` boundary)
- **Navigation**: sidebar (PC) / bottom tab bar (mobile)
- **Tables**: `el-table` (PC) / `el-card` list (mobile) in same component, controlled by `useBreakpoint()`
- **Charts**: full width on mobile, hide sub-indicators, enable touch zoom
- **Forms**: full width on mobile, same form component

## Build & Deployment

### Development

```bash
cd frontend && npm run dev    # Vite dev server :5173, proxy API to :8000
cd backend && uvicorn main:app  # FastAPI :8000
```

### Production

```bash
cd frontend && npm run build   # Output to backend/static/
cd backend && uvicorn main:app  # Serves static files + API
```

- `backend/static/` added to `.gitignore`
- Build output served by FastAPI `StaticFiles`
- SPA fallback route for Vue Router history mode
