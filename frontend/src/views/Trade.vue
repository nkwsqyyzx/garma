<template>
  <div class="trade-page">
    <h3>下单交易</h3>
    <el-card shadow="never">
      <TradeForm />
    </el-card>
  </div>
</template>

<script setup lang="ts">
import TradeForm from '@/components/TradeForm.vue'
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { subscribe } = useWebSocket()

onMounted(() => {
  subscribe(['asset', 'positions', 'orders'])
})
</script>

<style scoped>
.trade-page { max-width: 600px; margin: 0 auto; }
.trade-page h3 { margin: 0 0 16px 0; }
</style>
