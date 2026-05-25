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
      <div class="asset-item" v-if="preAsset != null">
        <div class="asset-label">预计盈亏</div>
        <div class="asset-value" :class="pnlClass">
          {{ pnlSign }}{{ formatMoney(Math.abs(pnlAmount)) }}
          <span class="pnl-pct">({{ pnlSign }}{{ pnlPct }}%)</span>
        </div>
      </div>
    </div>
    <div v-else class="asset-empty">暂无资金数据</div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { formatMoney } from '@/utils/format'
import { getAssetSnapshots } from '@/api/qmt'

const account = useAccountStore()
const { asset } = storeToRefs(account)

const preAsset = ref<number | null>(null)

async function loadPreMarket() {
  try {
    const today = new Date().toISOString().slice(0, 10)
    const data = await getAssetSnapshots(today, today)
    const pre = (data || []).find((s) => s.snapshot_type === 'pre_market')
    if (pre) preAsset.value = pre.total_asset
  } catch {}
}

loadPreMarket()

const pnlAmount = computed(() => {
  if (preAsset.value == null || !asset.value?.total_asset) return 0
  return asset.value.total_asset - preAsset.value
})

const pnlPct = computed(() => {
  if (!preAsset.value) return '0.00'
  return ((pnlAmount.value / preAsset.value) * 100).toFixed(2)
})

const pnlSign = computed(() => (pnlAmount.value >= 0 ? '+' : ''))

const pnlClass = computed(() => {
  if (pnlAmount.value > 0) return 'pnl-positive'
  if (pnlAmount.value < 0) return 'pnl-negative'
  return ''
})
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
.pnl-positive {
  color: #f56c6c;
}
.pnl-negative {
  color: #67c23a;
}
.pnl-pct {
  font-size: 13px;
  font-weight: 400;
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
