/** QMT Backend API 封装 */

const BASE = '/api/v1/qmt'

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const json = await resp.json()
  if (json.code !== 0) {
    throw new Error(json.msg || 'API Error')
  }
  return json.data as T
}

// ── 行情 ──────────────────────────────────────────────

export async function getSnapshot(codes: string[]) {
  return request<Record<string, any>>(`/quote/snapshot?codes=${codes.join(',')}`)
}

export async function getTick(code: string) {
  return request<any>(`/quote/tick/${encodeURIComponent(code)}`)
}

export async function getKline(code: string, period: string = '1d', count: number = 100) {
  return request<any[]>(`/quote/kline?code=${encodeURIComponent(code)}&period=${period}&count=${count}`)
}

export async function getStockNames(codes: string[]) {
  return request<Record<string, string>>(`/quote/stock_names?codes=${codes.join(',')}`)
}

// ── 账户 ──────────────────────────────────────────────

export async function getAsset() {
  return request<any>('/account/asset')
}

export async function getPositions() {
  return request<any[]>('/account/positions')
}

export async function getOrders(cancelableOnly: boolean = false) {
  return request<any[]>(`/account/orders?cancelable_only=${cancelableOnly}`)
}

export async function getTrades() {
  return request<any[]>('/account/trades')
}

// ── 交易 ──────────────────────────────────────────────

export async function placeOrder(params: {
  stock_code: string
  order_type: string
  order_volume: number
  price_type: number
  price: number
  strategy_name?: string
  order_remark?: string
}) {
  return request<{ req_id: string }>('/trade/order', {
    method: 'POST',
    body: JSON.stringify(params),
  })
}

export async function cancelOrder(orderId: string) {
  return request<{ req_id: string }>('/trade/cancel', {
    method: 'POST',
    body: JSON.stringify({ order_id: orderId }),
  })
}

export async function cancelAll() {
  return request<{ req_id: string }>('/trade/cancel_all', {
    method: 'POST',
  })
}
