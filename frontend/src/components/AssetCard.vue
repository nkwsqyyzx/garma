<template>
  <el-card shadow="hover" class="asset-card">
    <div class="asset-row" v-if="asset">
      <div class="asset-item">
        <div class="asset-label">总资产</div>
        <div class="asset-value primary">{{ formatMoney(asset.total_asset) }}</div>
      </div>
      <div class="asset-item">
        <div class="asset-label">可用资金</div>
        <div class="asset-value">{{ formatMoney(asset.cash) }}</div>
      </div>
      <div class="asset-item">
        <div class="asset-label">持仓市值</div>
        <div class="asset-value">{{ formatMoney(asset.market_value) }}</div>
      </div>
      <div class="asset-item">
        <div class="asset-label">冻结资金</div>
        <div class="asset-value">{{ formatMoney(asset.frozen_cash) }}</div>
      </div>
    </div>
    <div v-else class="asset-empty">暂无资金数据</div>
  </el-card>
</template>

<script setup lang="ts">
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'

const account = useAccountStore()
const { asset } = storeToRefs(account)

function formatMoney(val: number | undefined): string {
  if (val == null) return '--'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<style scoped>
.asset-card { margin-bottom: 16px; }
.asset-row {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
}
.asset-item {
  min-width: 120px;
}
.asset-label {
  font-size: 12px;
  color: #909399;
  margin-bottom: 4px;
}
.asset-value {
  font-size: 18px;
  font-weight: 600;
  color: #303133;
}
.asset-value.primary {
  color: #409eff;
}
.asset-empty {
  color: #c0c4cc;
  text-align: center;
  padding: 16px 0;
}
@media (max-width: 768px) {
  .asset-row { gap: 12px; }
  .asset-value { font-size: 15px; }
  .asset-item { min-width: 45%; }
}
</style>
