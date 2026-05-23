<template>
  <div class="trades-page" v-loading="loading">
    <StrategyTradeTable
      v-if="groupedData.length"
      :factors="groupedData"
    />
    <div v-else-if="!loading" class="empty-text">暂无成交数据</div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import StrategyTradeTable from '@/components/StrategyTradeTable.vue'
import { getStrategyTrades, type StrategyTrade } from '@/api/qmt'

export interface TradeRow {
  stock_code: string
  stock_name: string
  direction: string
  price: number
  volume: number
  amount: number
  trade_date: string
  pct_change: number
  pnl: number
}

export interface TradeStrategyGroup {
  name: string
  trades: TradeRow[]
  totalAmount: number
  totalPnl: number
  tradeCount: number
}

export interface TradeFactorGroup {
  factor: string
  strategies: TradeStrategyGroup[]
  totalAmount: number
  totalPnl: number
  tradeCount: number
}

const loading = ref(false)
const groupedData = ref<TradeFactorGroup[]>([])

function groupByFactorAndStrategy(raw: StrategyTrade[]): TradeFactorGroup[] {
  const factorMap = new Map<string, Map<string, TradeRow[]>>()

  for (const item of raw) {
    const factor = item.factor || '未分组'
    const strategy = item.strategy || '未命名'
    if (!factorMap.has(factor)) {
      factorMap.set(factor, new Map())
    }
    const stratMap = factorMap.get(factor)!
    if (!stratMap.has(strategy)) {
      stratMap.set(strategy, [])
    }
    stratMap.get(strategy)!.push({
      stock_code: item.stock_code,
      stock_name: item.stock_name,
      direction: item.direction,
      price: item.price,
      volume: item.volume,
      amount: item.amount,
      trade_date: item.trade_date,
      pct_change: item.pct_change,
      pnl: item.pnl,
    })
  }

  const result: TradeFactorGroup[] = []
  for (const [factor, stratMap] of factorMap) {
    const strategies: TradeStrategyGroup[] = []
    let factorTrades: TradeRow[] = []

    for (const [name, trades] of stratMap) {
      const totalAmount = trades.reduce((s, t) => s + t.amount, 0)
      const totalPnl = trades.reduce((s, t) => s + t.pnl, 0)
      strategies.push({
        name,
        trades,
        totalAmount,
        totalPnl,
        tradeCount: trades.length,
      })
      factorTrades = factorTrades.concat(trades)
    }

    result.push({
      factor,
      strategies,
      totalAmount: factorTrades.reduce((s, t) => s + t.amount, 0),
      totalPnl: factorTrades.reduce((s, t) => s + t.pnl, 0),
      tradeCount: factorTrades.length,
    })
  }

  result.sort((a, b) => b.tradeCount - a.tradeCount)
  return result
}

async function loadData() {
  loading.value = true
  try {
    const data = await getStrategyTrades()
    groupedData.value = groupByFactorAndStrategy(data || [])
  } catch {
    groupedData.value = []
  } finally {
    loading.value = false
  }
}

onMounted(() => loadData())
</script>

<style scoped>
.trades-page {
  max-width: 1200px;
  margin: 0 auto;
  min-height: 200px;
}
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
</style>
