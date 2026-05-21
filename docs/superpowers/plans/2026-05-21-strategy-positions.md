# Strategy Positions Page Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/strategies` page showing positions grouped by factor → sub-strategy, with a tag bar to toggle factor visibility.

**Architecture:** Two-component structure — page shell handles API + data parsing/grouping, core component handles rendering. Follows existing project conventions: Vue 3 Composition API, Element Plus, `useBreakpoint` for responsive, `formatMoney`/`formatPct`/`pnlClass` from shared utils.

**Tech Stack:** Vue 3, TypeScript, Element Plus (`el-table`, `el-check-tag`, `el-card`, `el-divider`), Vue Router, Pinia (storeToRefs for account store only)

---

### Task 1: Add API placeholder and route

**Files:**
- Modify: `frontend/src/api/qmt.ts` (append at end)
- Modify: `frontend/src/router/index.ts` (add route at line 14)

- [ ] **Step 1: Add API function to qmt.ts**

Append after the `cancelAll()` function at the end of `frontend/src/api/qmt.ts`:

```typescript
// ── 策略持仓 ─────────────────────────────────────────

export interface StrategyPosition {
  stock_code: string
  volume: number
  trade_date: string
  avg_price: number
  other: string
  cost: number
  pct_change: number
  current_price: number
  pnl: number
}

export async function getStrategyPositions() {
  return request<StrategyPosition[]>('/strategy/positions')
}
```

- [ ] **Step 2: Add route to router/index.ts**

Insert after the `trade` route (line 14) in the `children` array:

```typescript
        { path: 'strategies', name: 'strategies', component: () => import('@/views/StrategyPositions.vue') },
```

- [ ] **Step 3: Add sidebar menu item to MainLayout.vue**

Insert after the "下单" menu item (after line 25), before `</el-menu>`:

```html
        <el-menu-item index="/strategies">
          <span>策略持仓</span>
        </el-menu-item>
```

- [ ] **Step 4: Verify route loads blank page**

Run: `cd frontend && npm run dev`

Open `http://localhost:8999/strategies` — should show blank page with sidebar including "策略持仓" menu item (404 from API is expected, page not yet built).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/api/qmt.ts frontend/src/router/index.ts frontend/src/layouts/MainLayout.vue
git commit -m "feat: add /strategies route and API placeholder"
```

---

### Task 2: Create StrategyPositions.vue (page shell)

**Files:**
- Create: `frontend/src/views/StrategyPositions.vue`

This page shell fetches API data, parses the `other` field, groups by factor→strategy, computes aggregations, and passes structured data to the display component.

- [ ] **Step 1: Create the page shell**

Create `frontend/src/views/StrategyPositions.vue`:

```vue
<template>
  <div class="strategy-positions" v-loading="loading">
    <StrategyPositionTable
      v-if="groupedData.length"
      :factors="groupedData"
      @row-click="onRowClick"
    />
    <div v-else-if="!loading" class="empty-text">暂无策略持仓数据</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import StrategyPositionTable from '@/components/StrategyPositionTable.vue'
import { getStrategyPositions, type StrategyPosition } from '@/api/qmt'

const router = useRouter()
const loading = ref(false)

export interface PositionRow {
  stock_code: string
  stock_name: string
  volume: number
  trade_date: string
  avg_price: number
  cost: number
  pct_change: number
  current_price: number
  pnl: number
  rank: number
}

export interface StrategyGroup {
  name: string
  positions: PositionRow[]
  totalCost: number
  totalPnl: number
  weightedPct: number
}

export interface FactorGroup {
  factor: string
  strategies: StrategyGroup[]
  totalCost: number
  totalPnl: number
  weightedPct: number
  positionCount: number
}

const groupedData = ref<FactorGroup[]>([])

function parseOther(other: string): { strategy: string; factor: string; rank: number; stock_name: string } {
  const parts = other.split(':')
  return {
    strategy: parts[0] || '',
    factor: parts[1] || '',
    rank: Number(parts[2]) || 0,
    stock_name: parts[3] || '',
  }
}

function aggregate(positions: PositionRow[]): { totalCost: number; totalPnl: number; weightedPct: number } {
  let totalCost = 0
  let totalPnl = 0
  let weightedPctSum = 0
  for (const p of positions) {
    totalCost += p.cost
    totalPnl += p.pnl
    weightedPctSum += p.cost * p.pct_change
  }
  return {
    totalCost,
    totalPnl,
    weightedPct: totalCost > 0 ? weightedPctSum / totalCost : 0,
  }
}

function groupByFactorAndStrategy(raw: StrategyPosition[]): FactorGroup[] {
  // Group: factor -> strategy -> positions
  const factorMap = new Map<string, Map<string, PositionRow[]>>()

  for (const item of raw) {
    const { strategy, factor, rank, stock_name } = parseOther(item.other)
    if (!factorMap.has(factor)) {
      factorMap.set(factor, new Map())
    }
    const stratMap = factorMap.get(factor)!
    if (!stratMap.has(strategy)) {
      stratMap.set(strategy, [])
    }
    stratMap.get(strategy)!.push({
      stock_code: item.stock_code,
      stock_name,
      volume: item.volume,
      trade_date: item.trade_date,
      avg_price: item.avg_price,
      cost: item.cost,
      pct_change: item.pct_change,
      current_price: item.current_price,
      pnl: item.pnl,
      rank,
    })
  }

  // Build FactorGroup[] sorted by position count desc
  const result: FactorGroup[] = []
  for (const [factor, stratMap] of factorMap) {
    const strategies: StrategyGroup[] = []
    let factorPositions: PositionRow[] = []

    for (const [name, positions] of stratMap) {
      strategies.push({
        name,
        positions,
        ...aggregate(positions),
      })
      factorPositions = factorPositions.concat(positions)
    }

    const agg = aggregate(factorPositions)
    result.push({
      factor,
      strategies,
      totalCost: agg.totalCost,
      totalPnl: agg.totalPnl,
      weightedPct: agg.weightedPct,
      positionCount: factorPositions.length,
    })
  }

  result.sort((a, b) => b.positionCount - a.positionCount)
  return result
}

async function loadData() {
  loading.value = true
  try {
    const data = await getStrategyPositions()
    groupedData.value = groupByFactorAndStrategy(data || [])
  } catch {
    groupedData.value = []
  } finally {
    loading.value = false
  }
}

function onRowClick(code: string) {
  router.push(`/position/${encodeURIComponent(code)}`)
}

onMounted(() => loadData())
</script>

<style scoped>
.strategy-positions {
  max-width: 1200px;
  margin: 0 auto;
  min-height: 200px;
}
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
</style>
```

- [ ] **Step 2: Verify page loads with empty state**

Open `http://localhost:8999/strategies` — should show "暂无策略持仓数据" (API returns 404, caught by try/catch).

- [ ] **Step 3: Commit**

```bash
git add frontend/src/views/StrategyPositions.vue
git commit -m "feat: add StrategyPositions page shell with data grouping"
```

---

### Task 3: Create StrategyPositionTable.vue (core display)

**Files:**
- Create: `frontend/src/components/StrategyPositionTable.vue`

This component renders the factor tag bar, factor sections with headers, sub-strategy blocks with `el-table`, and handles mobile responsive layout.

- [ ] **Step 1: Create the core display component**

Create `frontend/src/components/StrategyPositionTable.vue`:

```vue
<template>
  <div class="strategy-table">
    <!-- Factor tag bar -->
    <div class="tag-bar">
      <el-check-tag
        :checked="allSelected"
        @change="toggleAll"
        class="tag-item"
      >全部</el-check-tag>
      <el-check-tag
        v-for="fg in factors"
        :key="fg.factor"
        :checked="selectedFactors.has(fg.factor)"
        @change="toggleFactor(fg.factor)"
        class="tag-item"
      >{{ fg.factor }}({{ fg.positionCount }})</el-check-tag>
    </div>

    <!-- Factor sections -->
    <template v-for="(fg, idx) in visibleFactors" :key="fg.factor">
      <el-divider v-if="idx > 0" />

      <!-- Factor header -->
      <div class="factor-header">
        <span class="factor-name">{{ fg.factor }}</span>
        <span class="factor-meta">
          {{ fg.strategies.length }}个子策略
          <span class="factor-sep">|</span>
          成本 <b>{{ formatMoney(fg.totalCost) }}</b>
          <span class="factor-sep">|</span>
          <span :class="pnlClass(fg.totalPnl)">
            盈亏 <b>{{ formatMoney(fg.totalPnl) }}</b>
          </span>
          <span class="factor-sep">|</span>
          <span :class="pnlClass(fg.totalPnl)">{{ formatPct(fg.weightedPct) }}</span>
        </span>
      </div>

      <!-- Sub-strategy blocks -->
      <div v-for="sg in fg.strategies" :key="sg.name" class="strategy-block">
        <div class="strategy-title">
          <span class="strategy-name">{{ sg.name }}</span>
          <span class="strategy-meta">
            {{ sg.positions.length }}只
            <span class="factor-sep">|</span>
            成本 {{ formatMoney(sg.totalCost) }}
            <span class="factor-sep">|</span>
            <span :class="pnlClass(sg.totalPnl)">盈亏 {{ formatMoney(sg.totalPnl) }}</span>
            <span class="factor-sep">|</span>
            <span :class="pnlClass(sg.totalPnl)">{{ formatPct(sg.weightedPct) }}</span>
          </span>
        </div>

        <!-- PC: table -->
        <el-table
          v-if="!isMobile"
          :data="sg.positions"
          size="small"
          @row-click="(row: any) => emit('rowClick', row.stock_code)"
          class="position-detail-table"
          highlight-current-row
        >
          <el-table-column prop="stock_code" label="代码" width="110" />
          <el-table-column prop="stock_name" label="名称" width="100" />
          <el-table-column prop="volume" label="持仓量" width="90" align="right">
            <template #default="{ row }">{{ row.volume.toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="cost" label="成本" width="120" align="right">
            <template #default="{ row }">{{ formatMoney(row.cost) }}</template>
          </el-table-column>
          <el-table-column prop="avg_price" label="均价" width="90" align="right">
            <template #default="{ row }">{{ row.avg_price.toFixed(2) }}</template>
          </el-table-column>
          <el-table-column prop="pct_change" label="涨跌幅" width="100" align="right">
            <template #default="{ row }">
              <span :class="pnlClass(row.pnl)">{{ formatPct(row.pct_change) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="pnl" label="盈亏" width="120" align="right">
            <template #default="{ row }">
              <span :class="pnlClass(row.pnl)">{{ formatMoney(row.pnl) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="trade_date" label="交易日期" width="110" />
        </el-table>

        <!-- Mobile: card list -->
        <div v-else class="position-cards">
          <el-card
            v-for="p in sg.positions"
            :key="p.stock_code"
            shadow="hover"
            class="position-card"
            @click="emit('rowClick', p.stock_code)"
          >
            <div class="card-row">
              <div class="card-left">
                <div class="card-name">{{ p.stock_name }}</div>
                <div class="card-code">{{ p.stock_code }}</div>
              </div>
              <div class="card-right">
                <div :class="['card-pnl', pnlClass(p.pnl)]">{{ formatPct(p.pct_change) }}</div>
                <div class="card-value">{{ formatMoney(p.pnl) }}</div>
              </div>
            </div>
          </el-card>
        </div>
      </div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { formatMoney, formatPct, pnlClass } from '@/utils/format'
import type { FactorGroup } from '@/views/StrategyPositions.vue'

const props = defineProps<{
  factors: FactorGroup[]
}>()

const emit = defineEmits<{
  (e: 'rowClick', code: string): void
}>()

const { isMobile } = useBreakpoint()

// Factor selection
const selectedFactors = ref<Set<string>>(new Set())

// Initialize: select all factors
watch(() => props.factors, (val) => {
  if (val.length && selectedFactors.value.size === 0) {
    selectedFactors.value = new Set(val.map(f => f.factor))
  }
}, { immediate: true })

const allSelected = computed(() => {
  return props.factors.length > 0 && selectedFactors.value.size === props.factors.length
})

const visibleFactors = computed(() => {
  return props.factors.filter(f => selectedFactors.value.has(f.factor))
})

function toggleAll() {
  if (allSelected.value) {
    selectedFactors.value = new Set()
  } else {
    selectedFactors.value = new Set(props.factors.map(f => f.factor))
  }
}

function toggleFactor(factor: string) {
  const next = new Set(selectedFactors.value)
  if (next.has(factor)) {
    next.delete(factor)
  } else {
    next.add(factor)
  }
  selectedFactors.value = next
}
</script>

<style scoped>
.tag-bar {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.tag-item {
  cursor: pointer;
}
/* Factor section */
.factor-header {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.factor-name {
  font-size: 16px;
  font-weight: 600;
  color: #303133;
}
.factor-meta {
  font-size: 13px;
  color: #909399;
}
.factor-meta b { font-weight: 500; }
.factor-sep {
  margin: 0 4px;
  color: #dcdfe6;
}
/* Sub-strategy block */
.strategy-block {
  margin-bottom: 16px;
}
.strategy-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}
.strategy-name {
  font-size: 14px;
  font-weight: 500;
  color: #606266;
}
.strategy-meta {
  font-size: 12px;
  color: #909399;
}
/* Table */
.position-detail-table {
  cursor: pointer;
}
:deep(.position-detail-table .cell) {
  white-space: nowrap;
}
/* PnL colors */
.pnl-up { color: #f56c6c; }
.pnl-down { color: #67c23a; }
/* Mobile cards */
.position-card {
  margin-bottom: 8px;
  cursor: pointer;
}
.card-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-left { flex: 1; }
.card-name { font-size: 15px; font-weight: 500; }
.card-code { font-size: 12px; color: #909399; }
.card-right { text-align: right; }
.card-pnl { font-size: 16px; font-weight: 600; }
.card-value { font-size: 12px; color: #909399; margin-top: 2px; }
/* Mobile tag bar scroll */
@media (max-width: 768px) {
  .tag-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
```

- [ ] **Step 2: Verify component renders with mock data**

Temporarily replace the API call in `StrategyPositions.vue` `loadData()` with mock data to verify rendering. Then revert.

Expected: factor tag bar at top, factor sections with sub-strategy blocks, tables with columns.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/StrategyPositionTable.vue
git commit -m "feat: add StrategyPositionTable component with factor grouping and tag filter"
```

---

### Task 4: Verify full integration

- [ ] **Step 1: Start dev server**

Run: `cd frontend && npm run dev`

- [ ] **Step 2: Check navigation**

Open `http://localhost:8999/` — sidebar should show "策略持仓" menu item. Click it, should navigate to `/strategies` and show "暂无策略持仓数据" (API not implemented yet).

- [ ] **Step 3: Check mobile layout**

Resize browser to < 768px — sidebar hidden, bottom nav visible. Navigate to `/strategies` — tag bar should scroll horizontally, no table wrapping.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "feat: complete strategy positions page with factor grouping"
```
