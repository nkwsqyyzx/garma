<template>
  <div class="position-detail">
    <div class="detail-header">
      <el-button @click="router.back()" text>&larr; 返回</el-button>
      <span class="detail-title">{{ account.getStockName(code) }} ({{ code }})</span>
    </div>

    <!-- K 线图 -->
    <KlineChart :code="code" />

    <!-- 五档盘口 -->
    <OrderBook :code="code" />

    <!-- 快捷下单 -->
    <el-card shadow="never" class="trade-section">
      <template #header>快捷下单</template>
      <TradeForm :prefillCode="code" />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAccountStore } from '@/stores/account'
import { useWebSocket } from '@/composables/useWebSocket'
import KlineChart from '@/components/KlineChart.vue'
import OrderBook from '@/components/OrderBook.vue'
import TradeForm from '@/components/TradeForm.vue'
import { onMounted } from 'vue'
import { getStockNames } from '@/api/qmt'

const route = useRoute()
const router = useRouter()
const account = useAccountStore()
const { subscribe } = useWebSocket()

const code = computed(() => decodeURIComponent(route.params.code as string))

onMounted(async () => {
  subscribe(['positions', 'asset', 'orders'])
  try {
    const names = await getStockNames([code.value])
    account.setStockNames(names)
  } catch { /* ignore */ }
})
</script>

<style scoped>
.position-detail { max-width: 1200px; margin: 0 auto; }
.detail-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 16px;
}
.detail-title { font-size: 16px; font-weight: 600; }
.trade-section { margin-top: 16px; }

@media (max-width: 768px) {
  .detail-header { flex-wrap: wrap; }
}
</style>
