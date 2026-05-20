<template>
  <div class="trades-page">
    <TradeTable />
  </div>
</template>

<script setup lang="ts">
import TradeTable from '@/components/TradeTable.vue'
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { subscribe } = useWebSocket()

onMounted(() => {
  subscribe(['trades'])
})
</script>
