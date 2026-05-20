<template>
  <div class="orders-page">
    <OrderTable />
  </div>
</template>

<script setup lang="ts">
import OrderTable from '@/components/OrderTable.vue'
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { subscribe } = useWebSocket()

onMounted(() => {
  subscribe(['orders'])
})
</script>
