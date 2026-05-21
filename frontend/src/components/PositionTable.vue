<template>
  <div class="position-table">
    <div class="table-header">
      <h3>持仓列表</h3>
      <el-tag v-if="positions.length" type="info" size="small">{{ positions.length }} 只</el-tag>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="positions" stripe @row-click="onRowClick" style="width: 100%" highlight-current-row :default-sort="{ prop: 'market_value', order: 'descending' }">
      <el-table-column prop="stock_code" label="代码" width="110" sortable />
      <el-table-column label="名称" width="100" sortable :sort-method="sortByStockName">
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
        v-for="pos in positions"
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

    <div v-if="!positions.length" class="empty-text">暂无持仓</div>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { useRouter } from 'vue-router'
import { getStockNames } from '@/api/qmt'
import { watch } from 'vue'

const router = useRouter()
const account = useAccountStore()
const { positions } = storeToRefs(account)
const { isMobile } = useBreakpoint()

function onRowClick(row: any) {
  router.push(`/position/${encodeURIComponent(row.stock_code)}`)
}

function sortByStockName(a: any, b: any): number {
  const na = account.getStockName(a.stock_code)
  const nb = account.getStockName(b.stock_code)
  return na.localeCompare(nb, 'zh-CN')
}

function formatMoney(val: number | undefined): string {
  if (val == null) return '--'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function formatPct(val: number | undefined): string {
  if (val == null) return '--'
  const prefix = val > 0 ? '+' : ''
  return `${prefix}${val.toFixed(2)}%`
}

function pnlClass(val: number | undefined): string {
  if (val == null) return ''
  return val > 0 ? 'pnl-up' : val < 0 ? 'pnl-down' : ''
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
/* 排序图标默认隐藏，hover 时显示 */
:deep(.el-table .caret-wrapper) {
  opacity: 0;
  transition: opacity 0.2s;
}
:deep(.el-table .el-table__column-header:hover .caret-wrapper) {
  opacity: 1;
}
/* 正在排序的列始终显示图标 */
:deep(.el-table .ascending .caret-wrapper),
:deep(.el-table .descending .caret-wrapper) {
  opacity: 1;
}
:deep(.el-table .sort-caret.ascending) {
  border-bottom-color: #c0c4cc;
}
:deep(.el-table .sort-caret.descending) {
  border-top-color: #c0c4cc;
}
:deep(.el-table .ascending .sort-caret.ascending) {
  border-bottom-color: #409eff;
}
:deep(.el-table .descending .sort-caret.descending) {
  border-top-color: #409eff;
}
</style>
