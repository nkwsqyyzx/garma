<template>
  <div class="position-table">
    <div class="table-header">
      <h3>持仓列表</h3>
      <el-tag v-if="positions.length" type="info" size="small">{{ positions.length }} 只</el-tag>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="positions" stripe @row-click="onRowClick" style="width: 100%" highlight-current-row>
      <el-table-column prop="stock_code" label="代码" width="110" />
      <el-table-column label="名称" width="100">
        <template #default="{ row }">{{ account.getStockName(row.stock_code) }}</template>
      </el-table-column>
      <el-table-column prop="volume" label="持仓" width="90" align="right" />
      <el-table-column prop="can_use_volume" label="可卖" width="90" align="right" />
      <el-table-column label="市值" width="120" align="right">
        <template #default="{ row }">{{ formatMoney(row.market_value) }}</template>
      </el-table-column>
      <el-table-column label="盈亏" width="120" align="right">
        <template #default="{ row }">
          <span :class="pnlClass(row.profit_loss)">{{ formatMoney(row.profit_loss) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="盈亏%" width="100" align="right">
        <template #default="{ row }">
          <span :class="pnlClass(row.profit_loss)">{{ formatPct(row.profit_loss_ratio) }}</span>
        </template>
      </el-table-column>
      <el-table-column label="成本价" width="90" align="right">
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
import { onMounted } from 'vue'

const router = useRouter()
const account = useAccountStore()
const { positions } = storeToRefs(account)
const { isMobile } = useBreakpoint()

function onRowClick(row: any) {
  router.push(`/position/${encodeURIComponent(row.stock_code)}`)
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

onMounted(async () => {
  // 加载持仓股票名称
  if (positions.value.length) {
    const codes = positions.value.map(p => p.stock_code)
    try {
      const names = await getStockNames(codes)
      account.setStockNames(names)
    } catch { /* ignore */ }
  }
})
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
</style>
