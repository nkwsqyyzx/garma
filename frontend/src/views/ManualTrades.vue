<template>
  <div class="manual-trades-page" v-loading="loading">
    <template v-if="trades.length">
      <!-- PC: 表格 -->
      <el-table v-if="!isMobile" :data="trades" stripe style="width: 100%" size="small">
        <el-table-column prop="trade_date" label="日期" width="110" />
        <el-table-column label="股票" min-width="140">
          <template #default="{ row }">
            {{ row.stock_name || row.stock_code }}
            <div class="sub-text">{{ row.stock_code }}</div>
          </template>
        </el-table-column>
        <el-table-column label="方向" width="60" align="center">
          <template #default="{ row }">
            <span :class="row.direction === 'buy' ? 'pnl-up' : 'pnl-down'">
              {{ row.direction === 'buy' ? '买' : '卖' }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="数量" width="90" align="right">
          <template #default="{ row }">
            {{ row.volume?.toLocaleString() }}
          </template>
        </el-table-column>
        <el-table-column label="价格" width="90" align="right">
          <template #default="{ row }">
            {{ row.price ? row.price.toFixed(2) : '--' }}
          </template>
        </el-table-column>
        <el-table-column label="金额" width="110" align="right">
          <template #default="{ row }">
            {{ formatMoney(row.amount) }}
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip />
      </el-table>

      <!-- 手机: 卡片列表 -->
      <template v-else>
        <el-card v-for="row in trades" :key="row.order_req_id" class="trade-card" shadow="never">
          <div class="card-header">
            <span class="card-name">{{ row.stock_name || row.stock_code }}</span>
            <span :class="row.direction === 'buy' ? 'pnl-up' : 'pnl-down'">
              {{ row.direction === 'buy' ? '买入' : '卖出' }}
            </span>
          </div>
          <div class="card-row">
            <span class="card-label">代码</span>
            <span>{{ row.stock_code }}</span>
          </div>
          <div class="card-row">
            <span class="card-label">数量</span>
            <span>{{ row.volume?.toLocaleString() }}</span>
          </div>
          <div class="card-row">
            <span class="card-label">价格</span>
            <span>{{ row.price ? row.price.toFixed(2) : '--' }}</span>
          </div>
          <div class="card-row">
            <span class="card-label">金额</span>
            <span>{{ formatMoney(row.amount) }}</span>
          </div>
          <div class="card-row" v-if="row.remark">
            <span class="card-label">备注</span>
            <span class="card-remark">{{ row.remark }}</span>
          </div>
          <div class="card-footer">{{ row.trade_date }}</div>
        </el-card>
      </template>
    </template>
    <div v-else-if="!loading" class="empty-text">暂无手工成交记录</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { getStrategyTrades, type StrategyTrade } from '@/api/qmt'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { formatMoney } from '@/utils/format'

const { isMobile } = useBreakpoint()
const loading = ref(false)
const trades = ref<StrategyTrade[]>([])

async function loadData() {
  loading.value = true
  try {
    const data = await getStrategyTrades(undefined, 'manual')
    trades.value = (data || []).sort((a, b) => {
      if (a.trade_date !== b.trade_date) return b.trade_date.localeCompare(a.trade_date)
      return (a.stock_code > b.stock_code) ? 1 : -1
    })
  } catch {
    trades.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => loadData())
</script>

<style scoped>
.manual-trades-page {
  max-width: 1200px;
  margin: 0 auto;
  min-height: 200px;
}
.empty-text {
  text-align: center;
  color: #c0c4cc;
  padding: 32px 0;
}
.sub-text {
  font-size: 11px;
  color: #909399;
}
.trade-card {
  margin-bottom: 8px;
}
.card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 6px;
}
.card-name {
  font-weight: 600;
}
.card-row {
  display: flex;
  justify-content: space-between;
  padding: 2px 0;
  font-size: 13px;
}
.card-label {
  color: #909399;
}
.card-remark {
  text-align: right;
  word-break: break-all;
  max-width: 70%;
}
.card-footer {
  text-align: right;
  font-size: 12px;
  color: #c0c4cc;
  margin-top: 4px;
}
</style>
