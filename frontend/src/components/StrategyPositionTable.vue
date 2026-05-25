<template>
  <div class="strategy-table">
    <div class="main-layout">
      <!-- Left: A1 + A2 pie charts -->
      <div v-if="factors.length" class="left-panel">
        <div class="chart-card">
          <div class="chart-title">因子分布</div>
          <v-chart :option="factorChartOption" autoresize class="chart-instance" />
        </div>
        <div class="chart-card">
          <div class="chart-title">策略分布</div>
          <v-chart :option="strategyChartOption" autoresize class="chart-instance" />
        </div>
      </div>

      <!-- Right: B factor/strategy list -->
      <div class="right-panel">
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
                <template #default="{ row }">{{ formatPrice(row.stock_code, row.avg_price) }}</template>
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
              <el-table-column label="操作" width="80" align="center">
                <template #default="{ row }">
                  <el-button type="success" size="small" text @click.stop="openSellDialog(row)">卖出</el-button>
                </template>
              </el-table-column>
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
                    <el-button type="success" size="small" text @click.stop="openSellDialog(p)" style="margin-top: 4px">卖出</el-button>
                  </div>
                </div>
              </el-card>
            </div>
          </div>
        </template>
      </div>
    </div>

    <!-- Sell dialog -->
    <el-dialog v-model="sellDialogVisible" title="卖出持仓" width="420px" :close-on-click-modal="false">
      <el-form label-position="top" size="default" v-if="sellTarget">
        <el-form-item label="股票">
          <el-input :model-value="`${sellTarget.stock_name} (${sellTarget.stock_code})`" disabled />
        </el-form-item>
        <el-form-item label="持仓量">
          <el-input :model-value="sellTarget.volume.toLocaleString()" disabled />
        </el-form-item>
        <el-form-item label="委托价格">
          <div class="sell-price-row">
            <el-input-number v-model="sellForm.price" :precision="2" :step="0.01" :min="0" style="flex: 1" />
            <el-button size="small" @click="sellForm.price = sellTarget.current_price || 0">现价</el-button>
          </div>
        </el-form-item>
        <el-form-item label="卖出数量">
          <div class="sell-volume-row">
            <el-input-number v-model="sellForm.volume" :step="100" :min="100" :max="sellTarget.volume" style="flex: 1" />
            <el-button-group>
              <el-button size="small" @click="sellForm.volume = Math.floor(sellTarget.volume / 4 / 100) * 100">1/4</el-button>
              <el-button size="small" @click="sellForm.volume = Math.floor(sellTarget.volume / 3 / 100) * 100">1/3</el-button>
              <el-button size="small" @click="sellForm.volume = Math.floor(sellTarget.volume / 2 / 100) * 100">1/2</el-button>
              <el-button size="small" @click="sellForm.volume = sellTarget.volume">全部</el-button>
            </el-button-group>
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="sellDialogVisible = false">取消</el-button>
        <el-button type="success" :loading="sellSubmitting" @click="onSellSubmit">确认卖出</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import VChart from 'vue-echarts'
import { use } from 'echarts/core'
import { PieChart } from 'echarts/charts'
import { TooltipComponent, LegendComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { formatMoney, formatPct, pnlClass, formatPrice } from '@/utils/format'
import { placeOrder } from '@/api/qmt'
import type { FactorGroup, PositionRow } from '@/views/StrategyPositions.vue'

use([PieChart, TooltipComponent, LegendComponent, CanvasRenderer])

const props = defineProps<{
  factors: FactorGroup[]
  cash: number
}>()

const emit = defineEmits<{
  (e: 'rowClick', code: string): void
}>()

const { isMobile } = useBreakpoint()

// --- Pie chart data ---

function marketValue(positions: PositionRow[]): number {
  return positions.reduce((sum, p) => sum + (p.current_price || p.avg_price) * p.volume, 0)
}

const factorChartData = computed(() => {
  const items = props.factors.map(fg => ({
    name: fg.factor,
    value: Math.round(marketValue(fg.strategies.flatMap(s => s.positions)) * 100) / 100,
  })).filter(d => d.value > 0)
  if (props.cash > 0) {
    items.push({ name: '可用资金', value: Math.round(props.cash * 100) / 100 })
  }
  return items
})

const strategyChartData = computed(() => {
  const items = props.factors.flatMap(fg =>
    fg.strategies.map(sg => ({
      name: sg.name,
      value: Math.round(marketValue(sg.positions) * 100) / 100,
    }))
  ).filter(d => d.value > 0)
  if (props.cash > 0) {
    items.push({ name: '可用资金', value: Math.round(props.cash * 100) / 100 })
  }
  return items
})

function makePieOption(data: { name: string; value: number }[]) {
  return {
    tooltip: {
      trigger: 'item' as const,
      formatter: (params: any) => {
        return `${params.name}<br/>市值: ${formatMoney(params.value)}<br/>占比: ${params.percent.toFixed(1)}%`
      },
    },
    legend: {
      type: 'scroll' as const,
      bottom: 0,
      textStyle: { fontSize: 11 },
    },
    series: [{
      type: 'pie',
      radius: ['35%', '65%'],
      center: ['50%', '45%'],
      label: {
        formatter: '{b}\n{d}%',
        fontSize: 11,
      },
      data,
    }],
  }
}

const factorChartOption = computed(() => makePieOption(factorChartData.value))
const strategyChartOption = computed(() => makePieOption(strategyChartData.value))

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

// Sell dialog
const sellDialogVisible = ref(false)
const sellTarget = ref<PositionRow | null>(null)
const sellSubmitting = ref(false)
const sellForm = ref({ price: 0, volume: 0 })

function openSellDialog(row: PositionRow) {
  sellTarget.value = row
  sellForm.value = {
    price: row.current_price || row.avg_price,
    volume: row.volume,
  }
  sellDialogVisible.value = true
}

async function onSellSubmit() {
  const target = sellTarget.value
  if (!target || sellForm.value.price <= 0 || sellForm.value.volume <= 0) {
    ElMessage.warning('请填写完整的卖出信息')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确认卖出 ${target.stock_name || target.stock_code} ${sellForm.value.volume}股 @ ${sellForm.value.price}`,
      '确认卖出',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }

  sellSubmitting.value = true
  try {
    const result = await placeOrder({
      stock_code: target.stock_code,
      order_type: 'sell',
      order_volume: sellForm.value.volume,
      price_type: 'limit',
      price: sellForm.value.price,
      linked_batch_id: target.batch_id || undefined,
      linked_req_id: target.batch_id ? undefined : target.order_req_id,
    })
    ElMessage.success(`卖出委托已提交: ${result.req_id}`)
    sellDialogVisible.value = false
    emit('rowClick', target.stock_code)
  } catch (e: any) {
    ElMessage.error(e.message || '卖出失败')
  } finally {
    sellSubmitting.value = false
  }
}
</script>

<style scoped>
/* Main two-column layout */
.main-layout {
  display: flex;
  gap: 20px;
  align-items: flex-start;
}
/* Left panel: pie charts */
.left-panel {
  flex-shrink: 0;
  width: 50%;
  display: flex;
  flex-direction: column;
  gap: 16px;
  position: sticky;
  top: 16px;
}
.chart-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 8px;
  padding: 12px;
}
.chart-title {
  font-size: 14px;
  font-weight: 500;
  color: #303133;
  margin-bottom: 4px;
  text-align: center;
}
.chart-instance {
  width: 100%;
  height: 360px;
}
/* Right panel: position list */
.right-panel {
  flex: 1;
  min-width: 0;
}
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
/* Sell dialog */
.sell-price-row, .sell-volume-row { display: flex; gap: 8px; align-items: center; }
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
/* Mobile: stack vertically */
@media (max-width: 900px) {
  .main-layout {
    flex-direction: column;
  }
  .left-panel {
    width: 100%;
    position: static;
    flex-direction: row;
  }
  .chart-card {
    flex: 1;
  }
}
@media (max-width: 768px) {
  .tag-bar {
    flex-wrap: nowrap;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
  }
  .left-panel {
    flex-direction: column;
  }
}
</style>
