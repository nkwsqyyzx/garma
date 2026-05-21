<template>
  <div class="position-table">
    <div class="table-header">
      <h3>持仓列表</h3>
      <el-tag v-if="filteredPositions.length" type="info" size="small">{{ filteredPositions.length }} 只</el-tag>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="filteredPositions" stripe border @row-click="onRowClick" style="width: 100%" highlight-current-row :default-sort="{ prop: 'market_value', order: 'descending' }">
      <el-table-column prop="stock_code" label="代码" width="110" sortable />
      <el-table-column label="名称" width="130" sortable :sort-method="sortByStockName">
        <template #default="{ row }">{{ account.getStockName(row.stock_code) }}</template>
      </el-table-column>
      <el-table-column prop="volume" label="持仓" width="90" align="right" sortable />
      <el-table-column prop="can_use_volume" label="可卖" width="90" align="right" sortable />
      <el-table-column prop="market_value" label="市值" width="120" align="right" sortable>
        <template #default="{ row }">{{ formatMoney(row.market_value) }}</template>
      </el-table-column>
      <el-table-column prop="profit_loss" label="盈亏" width="120" align="right" sortable>
        <template #default="{ row }">
          <span :class="pnlClass(row.profit_loss)">{{ formatMoney(row.profit_loss) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="profit_loss_ratio" label="盈亏%" width="100" align="right" sortable>
        <template #default="{ row }">
          <span :class="pnlClass(row.profit_loss)">{{ formatPct(row.profit_loss_ratio) }}</span>
        </template>
      </el-table-column>
      <el-table-column prop="open_price" label="成本价" width="90" align="right" sortable>
        <template #default="{ row }">{{ row.open_price?.toFixed(2) }}</template>
      </el-table-column>
    </el-table>

    <!-- 手机: 卡片列表 -->
    <div v-else class="position-cards">
      <el-card
        v-for="pos in filteredPositions"
        :key="pos.stock_code"
        shadow="hover"
        class="position-card"
        @click="onRowClick(pos)"
      >
        <div class="card-row">
          <div class="card-left">
            <div class="card-name">{{ account.getStockName(pos.stock_code) }}</div>
            <div class="card-code">{{ pos.stock_code }}</div>
          </div>
          <div class="card-right">
            <div :class="['card-pnl', pnlClass(pos.profit_loss)]">
              {{ formatPct(pos.profit_loss_ratio) }}
            </div>
            <div class="card-value">{{ formatMoney(pos.market_value) }}</div>
          </div>
        </div>
      </el-card>
    </div>

    <div v-if="!filteredPositions.length" class="empty-text">暂无持仓</div>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useRouter } from 'vue-router'
import { getStockNames } from '@/api/qmt'
import { formatMoney, formatPct, pnlClass } from '@/utils/format'
import { watch, computed } from 'vue'

const router = useRouter()
const account = useAccountStore()
const { positions } = storeToRefs(account)
const { isMobile } = useBreakpoint()

const filteredPositions = computed(() => positions.value.filter(p => (p.volume || 0) > 0))

function onRowClick(row: any) {
  router.push(`/position/${encodeURIComponent(row.stock_code)}`)
}

function sortByStockName(a: any, b: any): number {
  const na = account.getStockName(a.stock_code)
  const nb = account.getStockName(b.stock_code)
  return na.localeCompare(nb, 'zh-CN')
}

async function fetchNames() {
  const codes = positions.value
    .map(p => p.stock_code)
    .filter(c => !account.stockNames[c])
  if (!codes.length) return
  try {
    const names = await getStockNames(codes)
    account.setStockNames(names)
  } catch { /* ignore */ }
}

watch(positions, () => fetchNames(), { immediate: true })
</script>

<style scoped>
.table-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
}
.table-header h3 { margin: 0; font-size: 16px; }
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
.position-card { margin-bottom: 8px; cursor: pointer; }
.card-row { display: flex; justify-content: space-between; align-items: center; }
.card-name { font-size: 15px; font-weight: 500; }
.card-code { font-size: 12px; color: #909399; }
.card-right { text-align: right; }
.card-pnl { font-size: 16px; font-weight: 600; }
.card-value { font-size: 12px; color: #909399; margin-top: 2px; }
.pnl-up { color: #f56c6c; }
.pnl-down { color: #67c23a; }
:deep(.el-table) { cursor: pointer; }
:deep(.el-table .cell) { white-space: nowrap; }

/* 隐藏原生 border 三角 */
:deep(.el-table .sort-caret) { display: none !important; }

/* caret-wrapper 用文字替代 */
:deep(.el-table .caret-wrapper) {
  width: auto;
  height: auto;
  position: relative;
  opacity: 0;
  transition: opacity 0.2s;
  font-size: 12px;
  line-height: 1;
  margin-left: 2px;
}
:deep(.el-table .caret-wrapper::after) {
  content: '↕';
  color: #c0c4cc;
}
:deep(.el-table .el-table__column-header:hover .caret-wrapper) {
  opacity: 1;
}
/* 排序激活态始终显示 */
:deep(.el-table .ascending .caret-wrapper),
:deep(.el-table .descending .caret-wrapper) {
  opacity: 1;
}
:deep(.el-table .ascending .caret-wrapper::after) {
  content: '↑';
  color: #409eff;
}
:deep(.el-table .descending .caret-wrapper::after) {
  content: '↓';
  color: #409eff;
}
</style>
