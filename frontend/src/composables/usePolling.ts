import { onMounted, onUnmounted } from 'vue'

/**
 * 交易时段轮询：仅在 A 股交易时间 (9:30-15:00) 内执行回调。
 * 30 秒间隔，自动在 onUnmounted 时清理。
 */
export function useTradingPolling(callback: () => void, intervalMs: number = 30_000) {
  let timer: ReturnType<typeof setInterval> | null = null

  function isTradingHours(): boolean {
    const now = new Date()
    const minutes = now.getHours() * 60 + now.getMinutes()
    return minutes >= 9 * 60 + 30 && minutes < 15 * 60
  }

  onMounted(() => {
    callback()
    timer = setInterval(() => {
      if (isTradingHours()) {
        callback()
      }
    }, intervalMs)
  })

  onUnmounted(() => {
    if (timer) {
      clearInterval(timer)
      timer = null
    }
  })
}
