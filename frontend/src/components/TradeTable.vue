<template>
  <div class="trade-table">
    <div class="table-header">
      <h3>当日成交</h3>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="trades" stripe style="width: 100%">
      <el-table-column prop="traded_time" label="时间" width="100" />
      <el-table-column prop="stock_code" label="代码" width="110" />
      <el-table-column label="名称" width="90">
        <template #default="{ row }">{{ account.getStockName(row.stock_code) }}</template>
      </el-table-column>
      <el-table-column label="方向" width="60" align="center">
        <template #default="{ row }">
          <el-tag :type="row.order_type === 'buy' ? 'danger' : 'success'" size="small">
            {{ row.order_type === 'buy' ? '买' : '卖' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="90" align="right">
        <template #default="{ row }">{{ row.traded_price?.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column prop="traded_volume" label="成交量" width="90" align="right" />
      <el-table-column label="成交额" width="120" align="right">
        <template #default="{ row }">{{ formatMoney(row.traded_amount) }}</template>
      </el-table-column>
    </el-table>

    <!-- 手机: 卡片 -->
    <div v-else>
      <el-card v-for="trade in trades" :key="trade.traded_id" shadow="hover" class="trade-card">
        <div class="card-row">
          <div class="card-left">
            <el-tag :type="trade.order_type === 'buy' ? 'danger' : 'success'" size="small">
              {{ trade.order_type === 'buy' ? '买' : '卖' }}
            </el-tag>
            <span class="card-name">{{ account.getStockName(trade.stock_code) }}</span>
          </div>
          <div class="card-right">
            <span class="card-time">{{ trade.traded_time }}</span>
          </div>
        </div>
        <div class="card-info">
          <span>{{ trade.traded_price?.toFixed(2) }} x {{ trade.traded_volume }}</span>
          <span>{{ formatMoney(trade.traded_amount) }}</span>
        </div>
      </el-card>
    </div>

    <div v-if="!trades.length" class="empty-text">暂无成交</div>
  </div>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { getStockNames } from '@/api/qmt'
import { onMounted } from 'vue'

const account = useAccountStore()
const { trades } = storeToRefs(account)
const { isMobile } = useBreakpoint()

function formatMoney(val: number | undefined): string {
  if (val == null) return '--'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

onMounted(async () => {
  if (trades.value.length) {
    const codes = trades.value.map(t => t.stock_code)
    try {
      const names = await getStockNames(codes)
      account.setStockNames(names)
    } catch { /* ignore */ }
  }
})
</script>

<style scoped>
.table-header { margin-bottom: 12px; }
.table-header h3 { margin: 0; }
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
.trade-card { margin-bottom: 8px; }
.card-row { display: flex; justify-content: space-between; align-items: center; }
.card-left { display: flex; align-items: center; gap: 8px; }
.card-name { font-weight: 500; }
.card-time { font-size: 12px; color: #909399; }
.card-info {
  display: flex;
  gap: 16px;
  margin-top: 6px;
  font-size: 12px;
  color: #909399;
}
</style>
