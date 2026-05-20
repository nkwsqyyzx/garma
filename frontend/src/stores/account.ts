import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useAccountStore = defineStore('account', () => {
  const asset = ref<any>(null)
  const positions = ref<any[]>([])
  const orders = ref<any[]>([])
  const trades = ref<any[]>([])
  const stockNames = ref<Record<string, string>>({})

  function setAsset(data: any) { asset.value = data }
  function setPositions(data: any[]) { positions.value = data }
  function setOrders(data: any[]) { orders.value = data }
  function setTrades(data: any[]) { trades.value = data }

  function setStockNames(names: Record<string, string>) {
    stockNames.value = { ...stockNames.value, ...names }
  }

  function getStockName(code: string): string {
    return stockNames.value[code] || code
  }

  return {
    asset, positions, orders, trades, stockNames,
    setAsset, setPositions, setOrders, setTrades,
    setStockNames, getStockName,
  }
})
