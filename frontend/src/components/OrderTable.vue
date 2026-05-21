<template>
  <div class="order-table">
    <div class="table-header">
      <h3>当日委托</h3>
      <el-button type="danger" size="small" @click="onCancelAll" :disabled="!hasCancelable">全部撤单</el-button>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="orders" stripe style="width: 100%">
      <el-table-column prop="order_time" label="时间" width="100" />
      <el-table-column prop="stock_code" label="代码" width="110" />
      <el-table-column prop="stock_name" label="名称" width="90" />
      <el-table-column label="方向" width="60" align="center">
        <template #default="{ row }">
          <el-tag :type="row.order_type === 'buy' ? 'danger' : 'success'" size="small">
            {{ row.order_type === 'buy' ? '买' : '卖' }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column label="价格" width="90" align="right">
        <template #default="{ row }">{{ row.price?.toFixed(2) }}</template>
      </el-table-column>
      <el-table-column prop="order_volume" label="委托量" width="90" align="right" />
      <el-table-column prop="traded_volume" label="已成交" width="90" align="right" />
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="statusType(resolveStatus(row))" size="small">{{ statusLabel(resolveStatus(row)) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button
            v-if="isCancelable(resolveStatus(row))"
            type="warning"
            size="small"
            text
            @click="onCancel(row)"
          >撤单</el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 手机: 卡片 -->
    <div v-else>
      <el-card v-for="order in orders" :key="order.order_id" shadow="hover" class="order-card">
        <div class="card-row">
          <div class="card-left">
            <el-tag :type="order.order_type === 'buy' ? 'danger' : 'success'" size="small">
              {{ order.order_type === 'buy' ? '买' : '卖' }}
            </el-tag>
            <span class="card-name">{{ order.stock_name || order.stock_code }}</span>
          </div>
          <div class="card-right">
            <el-tag :type="statusType(resolveStatus(order))" size="small">{{ statusLabel(resolveStatus(order)) }}</el-tag>
          </div>
        </div>
        <div class="card-info">
          <span>{{ order.order_time }}</span>
          <span>{{ order.price?.toFixed(2) }} x {{ order.order_volume }}</span>
          <span>成交 {{ order.traded_volume }}</span>
          <el-button v-if="isCancelable(resolveStatus(order))" type="warning" size="small" text @click="onCancel(order)">撤单</el-button>
        </div>
      </el-card>
    </div>

    <div v-if="!orders.length" class="empty-text">暂无委托</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { cancelOrder, cancelAll } from '@/api/qmt'
import { ElMessage, ElMessageBox } from 'element-plus'

const account = useAccountStore()
const { orders } = storeToRefs(account)
const { isMobile } = useBreakpoint()

const CANCELABLE = new Set(['submitted', 'reported', 'partial', 'PENDING', 'SUBMITTED', 'PARTIALLY_FILLED'])

const hasCancelable = computed(() => orders.value.some(o => isCancelable(resolveStatus(o))))

function resolveStatus(order: any): string {
  const s = order.status || ''
  const vol = order.order_volume || 0
  const traded = order.traded_volume || 0
  // 完全成交 → filled（不管原始状态是什么）
  if (vol > 0 && traded >= vol) return 'filled'
  // 部分成交
  if (traded > 0 && traded < vol) return 'partial'
  // QMT 原始状态映射
  const map: Record<string, string> = {
    CANCELING: 'cancelled',
    CANCELLED: 'cancelled',
    UNKNOWN: 'submitted',
    PENDING: 'submitted',
    FILED: 'filled',
    REJECTED: 'rejected',
  }
  return map[s] || s
}

function isCancelable(status: string): boolean {
  return CANCELABLE.has(status)
}

function statusType(status: string): string {
  const map: Record<string, string> = {
    filled: 'success', FILLED: 'success',
    cancelled: 'info', CANCELLED: 'info',
    rejected: 'danger', REJECTED: 'danger',
    submitted: 'warning', SUBMITTED: 'warning',
    partial: '', PARTIALLY_FILLED: '',
  }
  return map[status] || 'info'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    PENDING: '待报', submitted: '已报', SUBMITTED: '已报',
    partial: '部成', PARTIALLY_FILLED: '部成',
    filled: '已成', FILLED: '已成',
    cancelled: '已撤', CANCELLED: '已撤',
    REJECTED: '废单', rejected: '废单',
  }
  return map[status] || status
}

async function onCancel(order: any) {
  try {
    await ElMessageBox.confirm(`确认撤单 ${order.stock_name || order.stock_code}?`, '撤单确认', { type: 'warning' })
    await cancelOrder(order.order_id)
    ElMessage.success('撤单请求已发送')
  } catch { /* cancelled */ }
}

async function onCancelAll() {
  try {
    await ElMessageBox.confirm('确认撤销所有可撤委托?', '全部撤单', { type: 'warning' })
    await cancelAll()
    ElMessage.success('全部撤单请求已发送')
  } catch { /* cancelled */ }
}
</script>

<style scoped>
.table-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
}
.table-header h3 { margin: 0; }
.empty-text { text-align: center; color: #c0c4cc; padding: 32px 0; }
.order-card { margin-bottom: 8px; }
.card-row { display: flex; justify-content: space-between; align-items: center; }
.card-left { display: flex; align-items: center; gap: 8px; }
.card-name { font-weight: 500; }
.card-info {
  display: flex;
  gap: 12px;
  margin-top: 8px;
  font-size: 12px;
  color: #909399;
  align-items: center;
}
</style>
