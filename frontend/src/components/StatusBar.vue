<template>
  <div class="status-bar">
    <span class="status-dot" :class="connected ? 'online' : 'offline'"></span>
    <span class="status-text">{{ connected ? '已连接' : '未连接' }}</span>
  </div>
</template>

<script setup lang="ts">
import { useConnectionStore } from '@/stores/connection'
import { storeToRefs } from 'pinia'

const connection = useConnectionStore()
const { connected } = storeToRefs(connection)
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.status-dot.online { background: #67c23a; }
.status-dot.offline { background: #f56c6c; }
.status-text { color: #909399; }
</style>
