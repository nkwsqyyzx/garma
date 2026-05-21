<template>
  <div class="strategy-positions" v-loading="loading">
    <StrategyPositionTable
      v-if="groupedData.length"
      :factors="groupedData"
      @row-click="onRowClick"
    />
    <div v-else-if="!loading" class="empty-text">暂无策略持仓数据</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import StrategyPositionTable from '@/components/StrategyPositionTable.vue'
import { getStrategyPositions, type StrategyPosition } from '@/api/qmt'

const router = useRouter()
const loading = ref(false)

export interface PositionRow {
  stock_code: string
  stock_name: string
  volume: number
  trade_date: string
  avg_price: number
  cost: number
  pct_change: number
  current_price: number
  pnl: number
  rank: number
}

export interface StrategyGroup {
  name: string
  positions: PositionRow[]
  totalCost: number
  totalPnl: number
  weightedPct: number
}

export interface FactorGroup {
  factor: string
  strategies: StrategyGroup[]
  totalCost: number
  totalPnl: number
  weightedPct: number
  positionCount: number
}

const groupedData = ref<FactorGroup[]>([])

function parseOther(other: string): { strategy: string; factor: string; rank: number; stock_name: string } {
  const parts = other.split(':')
  return {
    strategy: parts[0] || '',
    factor: parts[1] || '',
    rank: Number(parts[2]) || 0,
    stock_name: parts[3] || '',
  }
}

function aggregate(positions: PositionRow[]): { totalCost: number; totalPnl: number; weightedPct: number } {
  let totalCost = 0
  let totalPnl = 0
  let weightedPctSum = 0
  for (const p of positions) {
    totalCost += p.cost
    totalPnl += p.pnl
    weightedPctSum += p.cost * p.pct_change
  }
  return {
    totalCost,
    totalPnl,
    weightedPct: totalCost > 0 ? weightedPctSum / totalCost : 0,
  }
}

function groupByFactorAndStrategy(raw: StrategyPosition[]): FactorGroup[] {
  const factorMap = new Map<string, Map<string, PositionRow[]>>()

  for (const item of raw) {
    const { strategy, factor, rank, stock_name } = parseOther(item.other)
    if (!factorMap.has(factor)) {
      factorMap.set(factor, new Map())
    }
    const stratMap = factorMap.get(factor)!
    if (!stratMap.has(strategy)) {
      stratMap.set(strategy, [])
    }
    stratMap.get(strategy)!.push({
      stock_code: item.stock_code,
      stock_name,
      volume: item.volume,
      trade_date: item.trade_date,
      avg_price: item.avg_price,
      cost: item.cost,
      pct_change: item.pct_change,
      current_price: item.current_price,
      pnl: item.pnl,
      rank,
    })
  }

  const result: FactorGroup[] = []
  for (const [factor, stratMap] of factorMap) {
    const strategies: StrategyGroup[] = []
    let factorPositions: PositionRow[] = []

    for (const [name, positions] of stratMap) {
      strategies.push({
        name,
        positions,
        ...aggregate(positions),
      })
      factorPositions = factorPositions.concat(positions)
    }

    const agg = aggregate(factorPositions)
    result.push({
      factor,
      strategies,
      totalCost: agg.totalCost,
      totalPnl: agg.totalPnl,
      weightedPct: agg.weightedPct,
      positionCount: factorPositions.length,
    })
  }

  result.sort((a, b) => b.positionCount - a.positionCount)
  return result
}

async function loadData() {
  loading.value = true
  try {
    const data = await getStrategyPositions()
    groupedData.value = groupByFactorAndStrategy(data || [])
  } catch {
    groupedData.value = []
  } finally {
    loading.value = false
  }
}

function onRowClick(code: string) {
  router.push(`/position/${encodeURIComponent(code)}`)
}

onMounted(() => loadData())
</script>

<style scoped>
.strategy-positions {
  max-width: 1200px;
  margin: 0 auto;
  min-height: 200px;
}
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
</style>
