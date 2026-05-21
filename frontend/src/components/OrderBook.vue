<template>
  <el-card shadow="never" class="orderbook-card">
    <template #header>五档盘口</template>
    <div v-if="tick" class="orderbook">
      <!-- 卖盘 (5→1) -->
      <div class="book-row ask" v-for="i in 5" :key="'a'+i">
        <span class="level-label">卖{{ 6 - i }}</span>
        <span class="price">{{ formatPrice(tick[`ask${6-i}`]) }}</span>
        <span class="vol">{{ formatVol(tick[`ask_vol${6-i}`]) }}</span>
      </div>
      <!-- 最新价 -->
      <div class="last-price" :class="pnlClass(tick.pct_change)">
        {{ formatPrice(tick.last) }}
        <span class="pct">{{ formatPct(tick.pct_change) }}</span>
      </div>
      <!-- 买盘 (1→5) -->
      <div class="book-row bid" v-for="i in 5" :key="'b'+i">
        <span class="level-label">买{{ i }}</span>
        <span class="price">{{ formatPrice(tick[`bid${i}`]) }}</span>
        <span class="vol">{{ formatVol(tick[`bid_vol${i}`]) }}</span>
      </div>
    </div>
    <div v-else class="empty-text">加载中...</div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { getTick } from '@/api/qmt'

const props = defineProps<{ code: string }>()

const tick = ref<any>(null)
let timer: ReturnType<typeof setInterval> | null = null

async function loadTick() {
  try {
    tick.value = await getTick(props.code)
  } catch {
    // ignore
  }
}

function isETF(code: string): boolean {
  const p = code.replace(/\.(SH|SZ)/, '')
  return /^(51|15|16|50|52|56|58|59)/.test(p)
}

function formatPrice(val: number | undefined): string {
  if (val == null || val === 0) return '--'
  const digits = isETF(props.code) ? 3 : 2
  return val.toFixed(digits)
}

function formatVol(val: number | undefined): string {
  if (val == null) return '--'
  if (val >= 10000) return (val / 10000).toFixed(1) + '万'
  return val.toString()
}

function formatPct(val: number | undefined): string {
  if (val == null) return ''
  const prefix = val > 0 ? '+' : ''
  return `${prefix}${val.toFixed(2)}%`
}

function pnlClass(val: number | undefined): string {
  if (val == null) return ''
  return val > 0 ? 'pnl-up' : val < 0 ? 'pnl-down' : ''
}

onMounted(() => {
  loadTick()
  timer = setInterval(loadTick, 3000)
})

onUnmounted(() => {
  if (timer) clearInterval(timer)
})
</script>

<style scoped>
.orderbook { font-size: 13px; }
.book-row {
  display: flex;
  justify-content: space-between;
  padding: 3px 0;
}
.level-label { color: #909399; width: 40px; }
.price { flex: 1; text-align: center; }
.vol { width: 80px; text-align: right; }
.ask .price { color: #67c23a; }
.bid .price { color: #f56c6c; }
.last-price {
  text-align: center;
  font-size: 20px;
  font-weight: 700;
  padding: 8px 0;
  border-top: 1px solid #f0f0f0;
  border-bottom: 1px solid #f0f0f0;
  margin: 4px 0;
}
.last-price .pct {
  font-size: 13px;
  font-weight: 400;
  margin-left: 8px;
}
.pnl-up { color: #f56c6c; }
.pnl-down { color: #67c23a; }
.empty-text { color: #c0c4cc; text-align: center; padding: 16px 0; }
.orderbook-card { margin-top: 16px; }
</style>
