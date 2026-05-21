<template>
  <el-card shadow="never" class="kline-card">
    <template #header>
      <div class="kline-header">
        <span>K 线走势</span>
        <el-radio-group v-model="period" size="small" @change="loadData">
          <el-radio-button value="1d">日K</el-radio-button>
          <el-radio-button value="1h">60分</el-radio-button>
          <el-radio-button value="15m">15分</el-radio-button>
          <el-radio-button value="5m">5分</el-radio-button>
        </el-radio-group>
      </div>
    </template>
    <div ref="chartRef" class="chart-container"></div>
    <div v-if="noData" class="empty-text">暂无K线数据</div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { getKline } from '@/api/qmt'

const props = defineProps<{ code: string }>()

const chartRef = ref<HTMLDivElement>()
const period = ref('1d')
const noData = ref(false)
let chart: any = null
let resizeObserver: ResizeObserver | null = null

async function loadData() {
  if (!props.code) return
  try {
    const data = await getKline(props.code, period.value, 120)
    noData.value = !data?.length
    renderChart(data)
  } catch {
    // ignore
  }
}

function renderChart(data: any[]) {
  if (!chart || !data?.length) return

  const dates = data.map(d => d.time || d.date || d.datetime || '')
  const ohlc = data.map(d => [d.open, d.close, d.low, d.high])
  const volumes = data.map(d => d.volume || 0)
  const isUp = data.map(d => d.close >= d.open)

  const option = {
    animation: false,
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    grid: [
      { left: '10%', right: '5%', top: '5%', height: '60%' },
      { left: '10%', right: '5%', top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1 },
    ],
    yAxis: [
      { type: 'value', gridIndex: 0, scale: true, splitLine: { lineStyle: { color: '#eee' } } },
      { type: 'value', gridIndex: 1, scale: true, splitNumber: 2 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
    ],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: ohlc,
        itemStyle: {
          color: '#f56c6c',
          color0: '#67c23a',
          borderColor: '#f56c6c',
          borderColor0: '#67c23a',
        },
      },
      {
        name: '成交量',
        type: 'bar',
        xAxisIndex: 1,
        yAxisIndex: 1,
        data: volumes.map((v, i) => ({
          value: v,
          itemStyle: { color: isUp[i] ? '#f56c6c' : '#67c23a' },
        })),
      },
    ],
  }
  chart.setOption(option, true)
}

onMounted(() => {
  if (chartRef.value) {
    chart = (window as any).echarts.init(chartRef.value)
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartRef.value)
  }
  loadData()
})

onUnmounted(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})

watch(() => props.code, () => loadData())
</script>

<style scoped>
.kline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.chart-container {
  width: 100%;
  height: 400px;
}
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
@media (max-width: 768px) {
  .chart-container { height: 300px; }
}
</style>
