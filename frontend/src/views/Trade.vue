<template>
  <div class="trade-page">
    <h3>下单交易</h3>
    <div class="trade-layout">
      <el-card shadow="never" class="trade-form-card">
        <TradeForm @code-change="onCodeChange" />
      </el-card>
      <OrderBook v-if="activeCode" :code="activeCode" class="trade-orderbook" />
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import TradeForm from '@/components/TradeForm.vue'
import OrderBook from '@/components/OrderBook.vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { subscribe } = useWebSocket()
const activeCode = ref('')

function onCodeChange(code: string) {
  activeCode.value = code
}

onMounted(() => {
  subscribe(['asset', 'positions', 'orders'])
})
</script>

<style scoped>
.trade-page { max-width: 960px; margin: 0 auto; }
.trade-page h3 { margin: 0 0 16px 0; }
.trade-layout {
  display: flex;
  gap: 16px;
  align-items: flex-start;
}
.trade-form-card { flex: 1; min-width: 0; }
.trade-orderbook { width: 320px; flex-shrink: 0; }

@media (max-width: 768px) {
  .trade-layout { flex-direction: column; }
  .trade-orderbook { width: 100%; }
}
</style>
