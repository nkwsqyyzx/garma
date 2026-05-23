<template>
  <div class="strategy-trade-table">
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
      >{{ fg.factor }}({{ fg.tradeCount }})</el-check-tag>
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
          金额 <b>{{ formatMoney(fg.totalAmount) }}</b>
          <span class="factor-sep">|</span>
          <span :class="pnlClass(fg.totalPnl)">
            盈亏 <b>{{ formatMoney(fg.totalPnl) }}</b>
          </span>
        </span>
      </div>

      <!-- Sub-strategy blocks -->
      <div v-for="sg in fg.strategies" :key="sg.name" class="strategy-block">
        <div class="strategy-title">
          <span class="strategy-name">{{ sg.name }}</span>
          <span class="strategy-meta">
            {{ sg.tradeCount }}笔
            <span class="factor-sep">|</span>
            金额 {{ formatMoney(sg.totalAmount) }}
            <span class="factor-sep">|</span>
            <span :class="pnlClass(sg.totalPnl)">盈亏 {{ formatMoney(sg.totalPnl) }}</span>
          </span>
        </div>

        <!-- PC: table -->
        <el-table
          v-if="!isMobile"
          :data="sg.trades"
          size="small"
          :row-class-name="tradeRowClass"
          class="trade-detail-table"
        >
          <el-table-column prop="stock_code" label="代码" width="110" />
          <el-table-column prop="stock_name" label="名称" width="100">
            <template #default="{ row }">{{ row.stock_name || nameMap[row.stock_code] || '' }}</template>
          </el-table-column>
          <el-table-column label="方向" width="70" align="center">
            <template #default="{ row }">
              <el-tag :type="row.direction === 'buy' ? 'danger' : 'success'" size="small">
                {{ row.direction === 'buy' ? '买' : '卖' }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column prop="price" label="价格" width="90" align="right">
            <template #default="{ row }">{{ formatPrice(row.stock_code, row.price) }}</template>
          </el-table-column>
          <el-table-column prop="volume" label="数量" width="90" align="right">
            <template #default="{ row }">{{ row.volume.toLocaleString() }}</template>
          </el-table-column>
          <el-table-column prop="amount" label="金额" width="120" align="right">
            <template #default="{ row }">{{ formatMoney(row.amount) }}</template>
          </el-table-column>
          <el-table-column prop="pct_change" label="涨跌幅" width="100" align="right">
            <template #default="{ row }">
              <span class="sell-hide-cell" :class="pnlClass(row.pnl)">{{ formatPct(row.pct_change) }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="pnl" label="盈亏" width="120" align="right">
            <template #default="{ row }">
              <span class="sell-hide-cell" :class="pnlClass(row.pnl)">{{ formatMoney(row.pnl) }}</span>
            </template>
          </el-table-column>
        </el-table>

        <!-- Mobile: card list -->
        <div v-else class="trade-cards">
          <el-card
            v-for="t in sg.trades"
            :key="t.stock_code + t.direction"
            shadow="hover"
            class="trade-card"
          >
            <div class="card-row">
              <div class="card-left">
                <el-tag :type="t.direction === 'buy' ? 'danger' : 'success'" size="small">
                  {{ t.direction === 'buy' ? '买' : '卖' }}
                </el-tag>
                <span class="card-name">{{ t.stock_name || nameMap[t.stock_code] || '' }}</span>
                <span class="card-code">{{ t.stock_code }}</span>
              </div>
              <div class="card-right">
                <div class="card-price">{{ formatPrice(t.stock_code, t.price) }} x {{ t.volume.toLocaleString() }}</div>
                <div class="card-amount">{{ formatMoney(t.amount) }}</div>
                <template v-if="t.direction === 'buy'">
                  <div :class="['card-pnl', pnlClass(t.pnl)]">{{ formatPct(t.pct_change) }}</div>
                  <div class="card-value">{{ formatMoney(t.pnl) }}</div>
                </template>
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
import { formatMoney, formatPct, formatPrice, pnlClass } from '@/utils/format'
import { getStockNames } from '@/api/qmt'
import type { TradeFactorGroup } from '@/views/Trades.vue'

const props = defineProps<{
  factors: TradeFactorGroup[]
}>()

const { isMobile } = useBreakpoint()

// Stock name fallback map
const nameMap = ref<Record<string, string>>({})

watch(() => props.factors, async (val) => {
  if (!val.length) return
  // Collect codes with empty names
  const missing = new Set<string>()
  for (const fg of val) {
    for (const sg of fg.strategies) {
      for (const t of sg.trades) {
        if (!t.stock_name) missing.add(t.stock_code)
      }
    }
  }
  if (missing.size) {
    try {
      const names = await getStockNames([...missing])
      nameMap.value = names
    } catch { /* ignore */ }
  }
}, { immediate: true })

// Factor selection
const selectedFactors = ref<Set<string>>(new Set())

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

function tradeRowClass({ row }: { row: { direction: string } }) {
  return row.direction === 'sell' ? 'sell-row' : ''
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
:deep(.trade-detail-table .cell) {
  white-space: nowrap;
}
/* PnL colors */
.pnl-up { color: #f56c6c; }
.pnl-down { color: #67c23a; }
/* Sell row: hide pct/pnl by default, show on hover */
:deep(.sell-row .sell-hide-cell) {
  opacity: 0;
  transition: opacity 0.25s ease;
}
:deep(.el-table__row.sell-row:hover .sell-hide-cell) {
  opacity: 1;
}
/* Mobile cards */
.trade-card {
  margin-bottom: 8px;
}
.card-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.card-left {
  display: flex;
  align-items: center;
  gap: 8px;
}
.card-name { font-weight: 500; }
.card-code { font-size: 12px; color: #909399; }
.card-right { text-align: right; }
.card-price { font-size: 13px; }
.card-amount { font-size: 12px; color: #909399; }
.card-pnl { font-size: 15px; font-weight: 600; }
.card-value { font-size: 12px; color: #909399; }
/* Mobile tag bar scroll */
@media (max-width: 768px) {
  .tag-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
}
</style>
