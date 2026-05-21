<template>
  <div class="status-bar">
    <div class="status-left">
      <span class="status-dot" :class="connected ? 'online' : 'offline'"></span>
      <span class="status-text">{{ connected ? '已连接' : '未连接' }}</span>
      <span class="status-divider">|</span>
      <span class="status-dot" :class="marketDotClass"></span>
      <span class="status-text" :class="{ 'delay-warn': marketDelay > 10 }">{{ marketLabel }}</span>
    </div>
  </div>
</template>

<script setup lang="ts">
import { useConnectionStore } from '@/stores/connection'
import { storeToRefs } from 'pinia'
import { ref, computed, onMounted, onUnmounted } from 'vue'
import { getHealth } from '@/api/qmt'

const connection = useConnectionStore()
const { connected } = storeToRefs(connection)

const marketOnline = ref(false)
const marketDelay = ref<number | null>(null)
let timer: ReturnType<typeof setInterval> | null = null

const marketDotClass = computed(() => {
  if (!marketOnline.value) return 'offline'
  if (marketDelay.value === null) return 'online'
  if (marketDelay.value <= 10) return 'online'
  if (marketDelay.value <= 30) return 'warn'
  return 'offline'
})

const marketLabel = computed(() => {
  if (!marketOnline.value) return '行情 离线'
  if (marketDelay.value === null) return '行情 正常'
  if (marketDelay.value <= 10) return '行情 正常'
  return `行情 延迟 ${marketDelay.value.toFixed(0)}s`
})

async function pollHealth() {
  try {
    const data = await getHealth()
    marketOnline.value = data.market?.level !== 'offline' && data.market?.status !== 'unknown'
    marketDelay.value = data.market?.tick_delay ?? null
  } catch {
    marketOnline.value = false
    marketDelay.value = null
  }
}

onMounted(() => {
  pollHealth()
  timer = setInterval(pollHealth, 30_000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.status-bar {
  display: flex;
  align-items: center;
  width: 100%;
  font-size: 13px;
}
.status-left {
  display: flex;
  align-items: center;
  gap: 6px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.status-dot.online { background: #67c23a; }
.status-dot.warn { background: #e6a23c; }
.status-dot.offline { background: #f56c6c; }
.status-text { color: #909399; }
.status-divider { color: #dcdfe6; margin: 0 4px; }
.delay-warn { color: #e6a23c; }
</style>
