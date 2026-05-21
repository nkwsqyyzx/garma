/** 通用格式化工具函数 */

/** 判断是否为 ETF 基金 */
export function isETF(code: string): boolean {
  const p = code.replace(/\.(SH|SZ)/, '')
  return /^(51|15|16|50|52|56|58|59)/.test(p)
}

/** 金额格式化（千分位，两位小数） */
export function formatMoney(val: number | undefined): string {
  if (val == null) return '--'
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

/** 百分比格式化（带正负号） */
export function formatPct(val: number | undefined): string {
  if (val == null) return '--'
  const prefix = val > 0 ? '+' : ''
  return `${prefix}${val.toFixed(2)}%`
}

/** 价格格式化（ETF 三位小数，其他两位） */
export function formatPrice(code: string, val: number | undefined): string {
  if (val == null || val === 0) return '--'
  const digits = isETF(code) ? 3 : 2
  return val.toFixed(digits)
}

/** Unix 时间戳 → HH:MM:SS */
export function formatTime(val: number | undefined): string {
  if (!val) return '--'
  const d = new Date(val * 1000)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  const ss = String(d.getSeconds()).padStart(2, '0')
  return `${hh}:${mm}:${ss}`
}

/** 盈亏颜色 class */
export function pnlClass(val: number | undefined): string {
  if (val == null) return ''
  return val > 0 ? 'pnl-up' : val < 0 ? 'pnl-down' : ''
}
