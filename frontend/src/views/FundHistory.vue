<template>
  <div class="fund-history-page" v-loading="loading">
    <!-- 银证转账 -->
    <div class="section">
      <div class="section-header">
        <h3>银证转账</h3>
        <el-button type="primary" size="small" @click="showAddDialog = true">新增</el-button>
      </div>
      <el-table :data="transfers" stripe size="small" empty-text="暂无转账记录">
        <el-table-column prop="trade_date" label="日期" width="120" />
        <el-table-column prop="direction" label="方向" width="80">
          <template #default="{ row }">
            <el-tag :type="row.direction === 'deposit' ? 'success' : 'danger'" size="small">
              {{ row.direction === 'deposit' ? '入金' : '出金' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="amount" label="金额" align="right">
          <template #default="{ row }">
            {{ formatAmount(row.amount) }}
          </template>
        </el-table-column>
        <el-table-column prop="note" label="备注" min-width="120" />
        <el-table-column label="操作" width="70" fixed="right">
          <template #default="{ row }">
            <el-button type="danger" link size="small" @click="handleDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 每日盈亏 -->
    <div class="section">
      <div class="section-header">
        <h3>每日盈亏</h3>
      </div>
      <el-table :data="dailyPnlList" stripe size="small" empty-text="暂无盈亏数据">
        <el-table-column prop="trade_date" label="日期" width="120" />
        <el-table-column label="盘前资产" align="right">
          <template #default="{ row }">
            {{ row.pre_asset != null ? formatAmount(row.pre_asset) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="盘后资产" align="right">
          <template #default="{ row }">
            {{ row.post_asset != null ? formatAmount(row.post_asset) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="当日盈亏" align="right">
          <template #default="{ row }">
            <span v-if="row.daily_pnl != null" :class="pnlClass(row.daily_pnl)">
              {{ formatAmount(row.daily_pnl) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
        <el-table-column label="净转账" align="right">
          <template #default="{ row }">
            {{ row.net_transfer !== 0 ? formatAmount(row.net_transfer) : '-' }}
          </template>
        </el-table-column>
        <el-table-column label="调整后盈亏" align="right">
          <template #default="{ row }">
            <span v-if="row.adjusted_pnl != null" :class="pnlClass(row.adjusted_pnl)">
              {{ formatAmount(row.adjusted_pnl) }}
            </span>
            <span v-else>-</span>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <!-- 新增转账对话框 -->
    <el-dialog v-model="showAddDialog" title="新增银证转账" width="400px">
      <el-form :model="addForm" label-width="80px" size="default">
        <el-form-item label="日期">
          <el-date-picker v-model="addForm.trade_date" type="date" value-format="YYYY-MM-DD" placeholder="选择日期" />
        </el-form-item>
        <el-form-item label="方向">
          <el-select v-model="addForm.direction">
            <el-option label="入金" value="deposit" />
            <el-option label="出金" value="withdraw" />
          </el-select>
        </el-form-item>
        <el-form-item label="金额">
          <el-input-number v-model="addForm.amount" :min="0.01" :precision="2" :controls="false" style="width: 100%" />
        </el-form-item>
        <el-form-item label="备注">
          <el-input v-model="addForm.note" placeholder="可选" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="showAddDialog = false">取消</el-button>
        <el-button type="primary" @click="handleAdd" :loading="addLoading">确定</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  getFundTransfers,
  createFundTransfer,
  deleteFundTransfer,
  getDailyPnl,
  type FundTransfer,
  type DailyPnl,
} from '@/api/qmt'

const loading = ref(false)
const transfers = ref<FundTransfer[]>([])
const dailyPnlList = ref<DailyPnl[]>([])

const showAddDialog = ref(false)
const addLoading = ref(false)
const addForm = ref({
  trade_date: '',
  direction: 'deposit',
  amount: 0,
  note: '',
})

function formatAmount(val: number): string {
  return val.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function pnlClass(val: number): string {
  if (val > 0) return 'pnl-positive'
  if (val < 0) return 'pnl-negative'
  return ''
}

async function loadData() {
  loading.value = true
  try {
    const [tData, pData] = await Promise.all([
      getFundTransfers(),
      getDailyPnl(),
    ])
    transfers.value = tData || []
    dailyPnlList.value = pData || []
  } catch (e: any) {
    ElMessage.error(e.message || '加载数据失败')
  } finally {
    loading.value = false
  }
}

async function handleAdd() {
  if (!addForm.value.trade_date) {
    ElMessage.warning('请选择日期')
    return
  }
  if (!addForm.value.amount || addForm.value.amount <= 0) {
    ElMessage.warning('请输入金额')
    return
  }
  addLoading.value = true
  try {
    await createFundTransfer({
      trade_date: addForm.value.trade_date,
      direction: addForm.value.direction,
      amount: addForm.value.amount,
      note: addForm.value.note || undefined,
    })
    ElMessage.success('添加成功')
    showAddDialog.value = false
    addForm.value = { trade_date: '', direction: 'deposit', amount: 0, note: '' }
    await loadData()
  } catch (e: any) {
    ElMessage.error(e.message || '添加失败')
  } finally {
    addLoading.value = false
  }
}

async function handleDelete(row: FundTransfer) {
  try {
    await ElMessageBox.confirm(`确定删除 ${row.trade_date} 的转账记录？`, '确认', { type: 'warning' })
  } catch {
    return
  }
  try {
    await deleteFundTransfer(row.id)
    ElMessage.success('删除成功')
    await loadData()
  } catch (e: any) {
    ElMessage.error(e.message || '删除失败')
  }
}

onMounted(() => loadData())
</script>

<style scoped>
.fund-history-page {
  max-width: 1200px;
  margin: 0 auto;
}
.section {
  margin-bottom: 24px;
}
.section-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.section-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.pnl-positive {
  color: #f56c6c;
  font-weight: 500;
}
.pnl-negative {
  color: #67c23a;
  font-weight: 500;
}
</style>
