<template>
  <el-card shadow="never" class="kline-card">
    <template #header>
      <div class="kline-header">
        <span>K 线走势</span>
        <div class="kline-controls">
          <el-checkbox-group v-model="maVisible" size="small" class="ma-checks">
            <el-checkbox-button value="5">MA5</el-checkbox-button>
            <el-checkbox-button value="10">MA10</el-checkbox-button>
            <el-checkbox-button value="20">MA20</el-checkbox-button>
            <el-checkbox-button value="60">MA60</el-checkbox-button>
          </el-checkbox-group>
          <el-radio-group v-model="period" size="small" @change="loadData">
            <el-radio-button value="1d">日K</el-radio-button>
            <el-radio-button value="1h">60分</el-radio-button>
            <el-radio-button value="15m">15分</el-radio-button>
            <el-radio-button value="5m">5分</el-radio-button>
          </el-radio-group>
        </div>
      </div>
    </template>
    <div ref="chartRef" class="chart-container"></div>
    <div v-if="noData" class="empty-text">暂无K线数据</div>
  </el-card>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, watch } from 'vue'
import { getKline } from '@/api/qmt'
import { isETF } from '@/utils/format'

const props = defineProps<{ code: string }>()

const chartRef = ref<HTMLDivElement>()
const period = ref('1d')
const noData = ref(false)
const maVisible = ref<string[]>(['5', '10', '20', '60'])
let chart: any = null
let resizeObserver: ResizeObserver | null = null
let cachedData: any[] = []

const MA_COLORS: Record<string, string> = {
  '5': '#e6a23c',
  '10': '#409eff',
  '20': '#f56c6c',
  '60': '#909399',
}

function calcMA(closes: number[], n: number): (number | null)[] {
  return closes.map((_, i) => {
    if (i < n - 1) return null
    let sum = 0
    for (let j = i - n + 1; j <= i; j++) sum += closes[j]
    return sum / n
  })
}

async function loadData() {
  if (!props.code) return
  try {
    const data = await getKline(props.code, period.value, 120)
    cachedData = data || []
    noData.value = !data?.length
    renderChart(data)
  } catch {
    // ignore
  }
}

function renderChart(data: any[]) {
  if (!chart || !data?.length) return

  const etf = isETF(props.code)
  const digits = etf ? 3 : 2
  const dates = data.map(d => d.time || d.date || d.datetime || '')
  const closes = data.map(d => d.close)
  const ohlc = data.map(d => [d.open, d.close, d.low, d.high])
  const volumes = data.map(d => d.volume || 0)
  const amounts = data.map(d => d.amount || 0)
  const isUp = data.map(d => d.close >= d.open)

  // 涨跌幅
  const changes = data.map((d, i) => {
    if (i === 0) return 0
    const prev = data[i - 1].close
    return prev ? ((d.close - prev) / prev * 100) : 0
  })

  const fmtPrice = (v: number) => v.toFixed(digits)

  // MA series
  const maSeries = maVisible.value.map(n => ({
    name: `MA${n}`,
    type: 'line' as const,
    xAxisIndex: 0,
    yAxisIndex: 0,
    data: calcMA(closes, Number(n)),
    smooth: true,
    symbol: 'none',
    lineStyle: { width: 1, color: MA_COLORS[n] },
  }))

  const option = {
    animation: false,
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'cross' },
      formatter: (params: any[]) => {
        const idx = params[0]?.dataIndex ?? 0
        const d = data[idx]
        if (!d) return ''
        const dt = dates[idx]
        const chg = changes[idx]
        const chgStr = chg >= 0 ? `+${chg.toFixed(2)}%` : `${chg.toFixed(2)}%`
        const chgColor = chg >= 0 ? '#f56c6c' : '#67c23a'
        const amt = amounts[idx]
        const amtStr = amt >= 100000000 ? (amt / 100000000).toFixed(2) + '亿'
          : amt >= 10000 ? (amt / 10000).toFixed(2) + '万'
          : amt.toFixed(0)

        // MA values
        let maHtml = ''
        for (const n of maVisible.value) {
          const vals = calcMA(closes, Number(n))
          const v = vals[idx]
          if (v != null) {
            maHtml += `<span style="color:${MA_COLORS[n]};margin-right:8px">MA${n}: ${fmtPrice(v)}</span>`
          }
        }

        return `<div style="font-size:12px;line-height:1.6">
          <div style="font-weight:600">${dt}</div>
          ${maHtml ? `<div style="margin-bottom:2px">${maHtml}</div>` : ''}
          <div>开盘 <b>${fmtPrice(d.open)}</b></div>
          <div>收盘 <b>${fmtPrice(d.close)}</b></div>
          <div>最高 <b>${fmtPrice(d.high)}</b></div>
          <div>最低 <b>${fmtPrice(d.low)}</b></div>
          <div>涨跌 <b style="color:${chgColor}">${chgStr}</b></div>
          <div>成交额 <b>${amtStr}</b></div>
          <div>成交量 <b>${volumes[idx].toLocaleString()}</b></div>
        </div>`
      },
    },
    legend: { show: false },
    grid: [
      { left: '10%', right: '5%', top: '5%', height: '60%' },
      { left: '10%', right: '5%', top: '72%', height: '18%' },
    ],
    xAxis: [
      { type: 'category', data: dates, gridIndex: 0, axisLabel: { show: false }, axisTick: { show: false } },
      { type: 'category', data: dates, gridIndex: 1 },
    ],
    yAxis: [
      {
        type: 'value', gridIndex: 0, scale: true,
        splitLine: { lineStyle: { color: '#eee' } },
        axisLabel: { formatter: (v: number) => fmtPrice(v) },
      },
      { type: 'value', gridIndex: 1, scale: true, splitNumber: 2 },
    ],
    dataZoom: [
      { type: 'inside', xAxisIndex: [0, 1], start: 60, end: 100 },
    ],
    axisPointer: {
      link: [{ xAxisIndex: [0, 1] }],
    },
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
      ...maSeries,
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
watch(maVisible, () => {
  if (cachedData.length) renderChart(cachedData)
})
</script>

<style scoped>
.kline-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}
.kline-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.ma-checks :deep(.el-checkbox-button__inner) {
  padding: 4px 8px;
  font-size: 12px;
}
.chart-container {
  width: 100%;
  height: 400px;
}
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
@media (max-width: 768px) {
  .chart-container { height: 300px; }
  .kline-controls { gap: 8px; }
}
</style>
