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
