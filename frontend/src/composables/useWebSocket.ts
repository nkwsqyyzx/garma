import { useAccountStore } from '@/stores/account'
import { useConnectionStore } from '@/stores/connection'

let ws: WebSocket | null = null
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let reconnectDelay = 1000
const maxDelay = 30000

export function useWebSocket() {
  const connection = useConnectionStore()
  const account = useAccountStore()

  function connect() {
    const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
    const url = `${protocol}//${location.host}/ws/qmt-data`

    ws = new WebSocket(url)

    ws.onopen = () => {
      connection.setConnected(true)
      reconnectDelay = 1000
    }

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data)
        handleMessage(msg)
      } catch {
        // ignore invalid messages
      }
    }

    ws.onclose = () => {
      connection.setConnected(false)
      scheduleReconnect()
    }

    ws.onerror = () => {
      connection.setConnected(false)
    }
  }

  function handleMessage(msg: { type: string; data: any }) {
    switch (msg.type) {
      case 'asset':
        account.setAsset(msg.data)
        break
      case 'positions':
        account.setPositions(msg.data || [])
        break
      case 'orders':
        account.setOrders(msg.data || [])
        break
      case 'trades':
        account.setTrades(msg.data || [])
        break
    }
  }

  function subscribe(types: string[]) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ action: 'subscribe', types }))
    }
  }

  function scheduleReconnect() {
    if (reconnectTimer) return
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      connect()
    }, reconnectDelay)
    reconnectDelay = Math.min(reconnectDelay * 2, maxDelay)
  }

  function disconnect() {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws) {
      ws.close()
      ws = null
    }
    connection.setConnected(false)
  }

  return { connect, disconnect, subscribe }
}
