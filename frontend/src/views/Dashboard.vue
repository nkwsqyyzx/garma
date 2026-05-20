<template>
  <div class="dashboard">
    <AssetCard />
    <PositionTable />
  </div>
</template>

<script setup lang="ts">
import AssetCard from '@/components/AssetCard.vue'
import PositionTable from '@/components/PositionTable.vue'
import { onMounted } from 'vue'
import { useWebSocket } from '@/composables/useWebSocket'

const { subscribe } = useWebSocket()

onMounted(() => {
  subscribe(['asset', 'positions'])
})
</script>

<style scoped>
.dashboard {
  max-width: 1200px;
  margin: 0 auto;
}
</style>
