# Strategy Positions Page Design

## Overview

New frontend page `/strategies` to display strategy-based position data from a backend API. Positions are grouped by a two-level hierarchy: **factor** (e.g., `hf_std13`) -> **sub-strategy** (e.g., `25年策略_疯板_hfstd13`) -> position rows. A top tag bar allows toggling factor visibility.

## Data Source

### Backend API (TBD - user will implement)

```
GET /api/v1/qmt/strategy/positions
Response: JSONArray
```

Each record:

| Field | Type | Description |
|---|---|---|
| `stock_code` | string | e.g. `600519.SH` |
| `volume` | number | Position quantity |
| `trade_date` | string | Trade date, e.g. `2026-05-21` |
| `avg_price` | number | Average fill price (adjusted by backend) |
| `other` | string | Raw "其他" column, format `{strategy}:{factor}:{rank}:{stock_name}` |
| `cost` | number | Total position cost |
| `pct_change` | number | Price change % (backend computes from tick, adjusted) |
| `current_price` | number | Latest price (from tick, adjusted) |
| `pnl` | number | P&L = (current_price - avg_price) * volume |

Frontend parses `other` by splitting on `:`:
- `factor` = segment 2 (e.g., `hf_std13`, `Alpha95`, `totalValue`, `手工信号`)
- `strategy` = segment 1 (e.g., `25年策略_疯板_hfstd13`)
- `rank` = segment 3 (integer)
- `stock_name` = segment 4 (e.g., `天和磁材`)

## Page Layout

```
┌───────────────────────────────────────────────────────────┐
│ [全部] [hf_std13(18)] [Alpha95(3)] [totalValue(2)] [...]  │  Factor tag bar
├───────────────────────────────────────────────────────────┤
│                                                           │
│  ▌hf_std13  3 sub-strategies  cost¥xxx  pnl+¥1,234  +0.85%  │  Factor header
│  ─────────────────────────                                │
│  ▸ 25年策略_疯板_hfstd13  5只  cost¥269,870  -¥856  -0.32%  │  Sub-strategy title
│    ┌──────┬────┬─────┬──────┬──────┬──────┬────┐         │
│    │code  │name│vol  │cost  │price │pct%  │pnl │         │  el-table
│    └──────┴────┴─────┴──────┴──────┴──────┴────┘         │
│                                                           │
│  ▸ 25年策略_ACF收敛_疯板分域_hfstd13  4只  +¥320  +0.12%  │
│    ...                                                    │
│                                                           │
│  ═════════════════════════════════════════════════════════ │  el-divider
│                                                           │
│  ▌Alpha95  1 sub-strategy  cost¥xxx  pnl-¥456  -0.21%    │
│  ...                                                      │
└───────────────────────────────────────────────────────────┘
```

### Aggregation (frontend)

- **Sub-strategy**: sum `pnl`, weighted average `pct_change` by cost
- **Factor**: sum `pnl`, weighted average `pct_change` by cost across all positions in factor

## Component Architecture

```
/views/StrategyPositions.vue              Page shell: API call, data parsing, grouping
  └─ /components/StrategyPositionTable.vue  Core display: tag bar + factor cards + tables
```

### StrategyPositions.vue (page shell)

- Fetches data from API on `onMounted`
- Parses `other` field, extracts factor/strategy/rank/stock_name
- Groups positions: `Map<factor, Map<strategy, Position[]>>`
- Computes aggregation (pnl sum, weighted pct_change) per sub-strategy and factor
- Passes structured data as props to StrategyPositionTable
- Handles row click navigation to `/position/:code`

### StrategyPositionTable.vue (core display)

- Props: grouped data structure, factor list
- Renders factor tag bar (`el-check-tag` multi-select)
- Renders factor sections: header + sub-strategy blocks with `el-table`
- Filters visible factors based on tag selection
- Row click emits event or calls router directly

No new store or composable needed. Page-level `ref` for data.

## Route & Navigation

- Route: `/strategies`, name: `strategies`
- Sidebar menu: add "策略持仓" item (after "下单")
- Mobile bottom nav: not added (secondary page, accessible via sidebar collapse or direct URL)

## Interactions

### Factor tag bar
- `el-check-tag` multi-select. "全部" = toggle all on/off
- Default: all selected (all factors visible)
- Each tag shows factor name + position count, e.g., `hf_std13(18)`
- Unselected factors hide their entire section below

### Table rows
- Clickable, navigates to `/position/:code` (same as PositionTable)
- Columns: stock_code, stock_name, volume, cost, avg_price, pct_change, pnl, trade_date
- Pnl colored red-up/green-down using existing `pnlClass`

### Mobile responsive
- `useBreakpoint().isMobile` to switch layout
- Factor tag bar: horizontal scroll, no wrap
- Tables become card lists (same pattern as PositionTable/OrderTable)

## Error States

- Loading: `v-loading` directive on main container
- Empty: "暂无策略持仓数据" centered text
- API error: silent, show empty state
- Factor with no visible sub-strategies: factor section not rendered

## Files to Create/Modify

| File | Action |
|---|---|
| `frontend/src/views/StrategyPositions.vue` | Create |
| `frontend/src/components/StrategyPositionTable.vue` | Create |
| `frontend/src/router/index.ts` | Add `/strategies` route |
| `frontend/src/layouts/MainLayout.vue` | Add sidebar menu item |
| `frontend/src/api/qmt.ts` | Add `getStrategyPositions()` (placeholder) |
