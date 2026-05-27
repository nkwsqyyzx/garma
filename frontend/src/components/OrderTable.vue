<template>
  <div class="order-table">
    <div class="table-header">
      <h3>当日委托</h3>
      <el-button type="danger" size="small" @click="onCancelAll" :disabled="!hasCancelable">全部撤单</el-button>
    </div>

    <!-- PC: 表格 -->
    <el-table v-if="!isMobile" :data="sortedOrders" stripe style="width: 100%">
      <el-table-column label="时间" width="130">
        <template #default="{ row }">{{ formatTime(row.order_time) }}</template>
      </el-table-column>
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
        <template #default="{ row }">{{ formatPrice(row.stock_code, row.price) }}</template>
      </el-table-column>
      <el-table-column prop="order_volume" label="委托量" width="90" align="right" />
      <el-table-column prop="traded_volume" label="已成交" width="90" align="right" />
      <el-table-column label="状态" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="statusType(row.display_status)" size="small">{{ statusLabel(row.display_status) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column label="操作" width="80" align="center">
        <template #default="{ row }">
          <el-button
            v-if="isCancelable(row.display_status)"
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
      <el-card v-for="order in sortedOrders" :key="order.order_id" shadow="hover" class="order-card">
        <div class="card-row">
          <div class="card-left">
            <el-tag :type="order.order_type === 'buy' ? 'danger' : 'success'" size="small">
              {{ order.order_type === 'buy' ? '买' : '卖' }}
            </el-tag>
            <span class="card-name">{{ order.stock_name || order.stock_code }}</span>
          </div>
          <div class="card-right">
            <el-tag :type="statusType(order.display_status)" size="small">{{ statusLabel(order.display_status) }}</el-tag>
          </div>
        </div>
        <div class="card-info">
          <span>{{ formatTime(order.order_time) }}</span>
          <span>{{ formatPrice(order.stock_code, order.price) }} x {{ order.order_volume }}</span>
          <span>成交 {{ order.traded_volume }}</span>
          <el-button v-if="isCancelable(order.display_status)" type="warning" size="small" text @click="onCancel(order)">撤单</el-button>
        </div>
      </el-card>
    </div>

    <div v-if="!sortedOrders.length" class="empty-text">暂无委托</div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { storeToRefs } from 'pinia'
import { useAccountStore } from '@/stores/account'
import { useBreakpoint } from '@/composables/useBreakpoint'
import { cancelOrder, cancelAll } from '@/api/qmt'
import { formatTime, formatPrice } from '@/utils/format'
import { ElMessage, ElMessageBox } from 'element-plus'

const account = useAccountStore()
const { orders } = storeToRefs(account)
const { isMobile } = useBreakpoint()

const CANCELABLE = new Set(['submitted', 'partial'])

const sortedOrders = computed(() =>
  [...orders.value].sort((a, b) => (b.order_time || 0) - (a.order_time || 0)),
)

const hasCancelable = computed(() =>
  orders.value.some(o => isCancelable(o.display_status)),
)

function isCancelable(status: string): boolean {
  return CANCELABLE.has(status)
}

function statusType(status: string): string {
  const map: Record<string, string> = {
    filled: 'success',
    cancelled: 'info',
    partially_cancelled: 'info',
    rejected: 'danger',
    submitted: 'warning',
    partial: '',
  }
  return map[status] || 'info'
}

function statusLabel(status: string): string {
  const map: Record<string, string> = {
    submitted: '已报',
    partial: '部成',
    filled: '已成',
    cancelled: '已撤',
    partially_cancelled: '部撤',
    rejected: '废单',
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
