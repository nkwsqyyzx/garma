<template>
  <div class="trade-form">
    <el-form label-position="top" size="default">
      <!-- 股票代码 -->
      <el-form-item label="股票代码">
        <el-input
          v-model="form.stock_code"
          placeholder="输入代码，如 600519.SH"
          :disabled="!!props.prefillCode"
          @change="onCodeChange"
        />
        <div v-if="stockName" class="stock-name">{{ stockName }}</div>
      </el-form-item>

      <!-- 买卖方向 -->
      <el-form-item label="方向">
        <el-radio-group v-model="form.order_type" @change="onOrderTypeChange">
          <el-radio-button value="buy" :class="{ 'btn-buy': form.order_type === 'buy' }">买入</el-radio-button>
          <el-radio-button value="sell" :class="{ 'btn-sell': form.order_type === 'sell' }">卖出</el-radio-button>
        </el-radio-group>
      </el-form-item>

      <!-- 策略信息（买入时显示） -->
      <div v-if="form.order_type === 'buy'" class="strategy-row">
        <el-form-item label="策略名" class="strategy-field">
          <el-input v-model="buyStrategy" placeholder="如 人工选股$网格" />
        </el-form-item>
        <el-form-item label="因子" class="strategy-field">
          <el-input v-model="buyFactor" placeholder="如 手工信号" />
        </el-form-item>
      </div>
      <div v-if="form.order_type === 'buy' && buyRemarkPreview" class="remark-preview">
        标记: {{ buyRemarkPreview }}
      </div>

      <!-- 策略持仓选择（卖出时显示） -->
      <el-form-item v-if="form.order_type === 'sell' && strategyLots.length > 0" label="关联策略持仓">
        <el-select v-model="selectedLotId" placeholder="选择关联的策略持仓" clearable style="width: 100%" @change="onLotChange">
          <el-option
            v-for="lot in strategyLots"
            :key="lot.order_req_id"
            :label="`${lot.strategy} | ${lot.volume}股 @ ${lot.avg_price.toFixed(2)} | ${lot.trade_date}`"
            :value="lot.order_req_id"
          />
        </el-select>
        <div v-if="selectedLotId" class="lot-hint">将关联 linked_req_id: {{ selectedLotId }}</div>
      </el-form-item>

      <!-- 委托价格 -->
      <el-form-item label="委托价格">
        <div class="price-row">
          <el-input-number v-model="form.price" :precision="pricePrecision" :step="priceStep" :min="0" style="flex: 1" />
          <el-button-group>
            <el-button size="small" @click="form.price = limitDown">跌停</el-button>
            <el-button size="small" @click="form.price = currentPrice">现价</el-button>
            <el-button size="small" @click="form.price = limitUp">涨停</el-button>
          </el-button-group>
        </div>
      </el-form-item>

      <!-- 委托数量 -->
      <el-form-item label="委托数量">
        <div class="volume-row">
          <el-input-number v-model="form.order_volume" :step="100" :min="0" :max="maxVolume" style="flex: 1" />
          <el-button-group>
            <el-button size="small" @click="form.order_volume = Math.floor(maxVolume / 4 / 100) * 100">1/4</el-button>
            <el-button size="small" @click="form.order_volume = Math.floor(maxVolume / 3 / 100) * 100">1/3</el-button>
            <el-button size="small" @click="form.order_volume = Math.floor(maxVolume / 2 / 100) * 100">1/2</el-button>
            <el-button size="small" @click="form.order_volume = maxVolume">全仓</el-button>
          </el-button-group>
        </div>
        <div class="max-volume-hint">最大可{{ form.order_type === 'buy' ? '买' : '卖' }}: {{ maxVolume }}</div>
      </el-form-item>

      <!-- 提交 -->
      <el-form-item>
        <el-button
          :type="form.order_type === 'buy' ? 'danger' : 'success'"
          @click="onSubmit"
          :loading="submitting"
          style="width: 100%"
        >
          {{ form.order_type === 'buy' ? '买入' : '卖出' }}
        </el-button>
      </el-form-item>
    </el-form>
  </div>
</template>

<script setup lang="ts">
import { reactive, ref, computed, onMounted } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { getStockNames, getSnapshot, getStrategyPositions, placeOrder } from '@/api/qmt'
import { isETF } from '@/utils/format'
import { useAccountStore } from '@/stores/account'
import { storeToRefs } from 'pinia'

const props = defineProps<{
  prefillCode?: string
}>()

const emit = defineEmits<{
  (e: 'codeChange', code: string): void
}>()

const account = useAccountStore()
const { asset, positions } = storeToRefs(account)

const form = reactive({
  stock_code: props.prefillCode || '',
  order_type: 'buy' as 'buy' | 'sell',
  price: 0,
  order_volume: 0,
  price_type: 'limit', // limit / market / best5
})

const stockName = ref('')
const currentPrice = ref(0)
const limitUp = ref(0)
const limitDown = ref(0)
const submitting = ref(false)

// Buy strategy fields
const buyStrategy = ref('人工选股$网格')
const buyFactor = ref('手工信号')
const buyRemarkPreview = computed(() => {
  if (!buyStrategy.value && !buyFactor.value) return ''
  const name = stockName.value || form.stock_code
  return `${buyStrategy.value || '-'}:${buyFactor.value || '-'}:0:${name}`
})

const pricePrecision = computed(() => isETF(form.stock_code) ? 3 : 2)
const priceStep = computed(() => isETF(form.stock_code) ? 0.001 : 0.01)

// Strategy position lots for sell
interface StrategyLot {
  order_req_id: string
  strategy: string
  volume: number
  avg_price: number
  trade_date: string
}
const strategyLots = ref<StrategyLot[]>([])
const selectedLotId = ref<string>('')

function parseStrategy(other: string): string {
  return other.split(':')[0] || ''
}

async function loadStrategyLots(code: string) {
  if (!code) {
    strategyLots.value = []
    selectedLotId.value = ''
    return
  }
  try {
    const all = await getStrategyPositions()
    const matches = (all || []).filter(p => p.stock_code === code && p.volume > 0)
    strategyLots.value = matches.map(p => ({
      order_req_id: p.order_req_id,
      strategy: parseStrategy(p.other),
      volume: p.volume,
      avg_price: p.avg_price,
      trade_date: p.trade_date,
    }))
    if (strategyLots.value.length === 1) {
      selectedLotId.value = strategyLots.value[0].order_req_id
    } else {
      selectedLotId.value = ''
    }
  } catch {
    strategyLots.value = []
    selectedLotId.value = ''
  }
}

function onLotChange() {
  form.order_volume = 0
}

const maxVolume = computed(() => {
  if (form.order_type === 'sell') {
    if (selectedLotId.value) {
      const lot = strategyLots.value.find(l => l.order_req_id === selectedLotId.value)
      return lot?.volume || 0
    }
    const pos = positions.value.find(p => p.stock_code === form.stock_code)
    return pos?.can_use_volume || 0
  }
  if (!asset.value || !form.price || form.price <= 0) return 0
  const cash = asset.value.cash || 0
  return Math.floor(cash / form.price / 100) * 100
})

function onOrderTypeChange() {
  form.order_volume = 0
  if (form.order_type === 'sell' && form.stock_code) {
    loadStrategyLots(form.stock_code)
  }
}

async function onCodeChange() {
  const code = form.stock_code.trim()
  emit('codeChange', code)
  if (!code) return

  // 获取名称
  try {
    const names = await getStockNames([code])
    stockName.value = names[code] || ''
  } catch {
    stockName.value = ''
  }

  // 获取行情
  try {
    const snap = await getSnapshot([code])
    const tick = snap[code]
    if (tick) {
      currentPrice.value = tick.last || 0
      limitUp.value = tick.limit_up || 0
      limitDown.value = tick.limit_down || 0
      if (form.price === 0) {
        form.price = currentPrice.value
      }
    }
  } catch {
    // ignore
  }

  // 卖出时加载策略持仓
  if (form.order_type === 'sell') {
    await loadStrategyLots(code)
  }
}

async function onSubmit() {
  if (!form.stock_code || form.price <= 0 || form.order_volume <= 0) {
    ElMessage.warning('请填写完整的下单信息')
    return
  }

  try {
    await ElMessageBox.confirm(
      `确认${form.order_type === 'buy' ? '买入' : '卖出'} ${stockName.value || form.stock_code} ${form.order_volume}股 @ ${form.price}`,
      '确认下单',
      { confirmButtonText: '确认', cancelButtonText: '取消', type: 'warning' }
    )
  } catch {
    return
  }

  submitting.value = true
  try {
    const params: Parameters<typeof placeOrder>[0] = {
      stock_code: form.stock_code,
      order_type: form.order_type,
      order_volume: form.order_volume,
      price_type: form.price_type,
      price: form.price,
    }
    if (form.order_type === 'buy') {
      if (buyStrategy.value) {
        params.strategy_name = buyStrategy.value
      }
      if (buyStrategy.value || buyFactor.value) {
        const name = stockName.value || form.stock_code
        params.order_remark = `${buyStrategy.value || '-'}:${buyFactor.value || '-'}:0:${name}`
      }
    }
    if (form.order_type === 'sell' && selectedLotId.value) {
      params.linked_req_id = selectedLotId.value
    }
    const result = await placeOrder(params)
    ElMessage.success(`下单成功: ${result.req_id}`)
    form.order_volume = 0
  } catch (e: any) {
    ElMessage.error(e.message || '下单失败')
  } finally {
    submitting.value = false
  }
}

onMounted(() => {
  if (props.prefillCode) {
    onCodeChange()
  }
})
</script>

<style scoped>
.trade-form { padding: 8px 0; }
.stock-name { font-size: 13px; color: #909399; margin-top: 4px; }
.price-row, .volume-row { display: flex; gap: 8px; align-items: center; }
.max-volume-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.lot-hint { font-size: 12px; color: #909399; margin-top: 4px; }
.remark-preview { font-size: 12px; color: #409eff; margin-top: -8px; margin-bottom: 12px; word-break: break-all; }
.strategy-row { display: flex; gap: 8px; }
.strategy-field { flex: 1; }
.btn-buy :deep(.el-radio-button__inner) { color: #f56c6c; }
.btn-sell :deep(.el-radio-button__inner) { color: #67c23a; }
</style>
