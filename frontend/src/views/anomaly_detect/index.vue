<template>
  <section class="page" data-module="anomaly_detect">
    <header class="page-head">
      <div>
        <h2>异常检测管理</h2>
        <p class="page-desc">维护检测规则的输入、执行约束与结果记录，为异常检测提供可验证的数据工程流程。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-create" type="button" @click="openCreate">登记检测规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出异常检测清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label v-for="field in filterFields" :key="field" class="filter-item">
        <span>{{ field }}</span>
        <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
      </label>
      <button class="btn" data-testid="query" type="submit">查询</button>
      <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button
              v-for="action in actions"
              :key="action"
              class="link"
              type="button"
              :data-testid="`action-${row.id}-${action}`"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无异常检测数据，可先登记检测规则</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记检测规则</h3>
        <label v-for="field in columns" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :data-testid="`create-${field}`" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" data-testid="submit-create" type="submit" :disabled="creating">
            {{ creating ? '提交中…' : '确认登记' }}
          </button>
        </div>
      </form>
    </div>

    <section class="v014-panel" data-testid="v014-panel">
      <header class="page-head">
        <div>
          <h3>V014 跨模块联动：导出清单信号核对</h3>
          <p class="page-desc">读取数据导出文件清单中的信号编号，逐分片核对告警范围，与数据导出侧共用同一份信号表。</p>
        </div>
      </header>
      <div class="filter-bar">
        <label><span>信号状态</span>
          <select v-model="signalFilter" data-testid="signal-filter" @change="loadSignals">
            <option value="">全部</option>
            <option value="生效">生效</option>
            <option value="停用">停用</option>
          </select>
        </label>
        <button class="btn" type="button" data-testid="load-signals" @click="loadSignals">读取信号表</button>
        <label><span>导出任务ID</span><input v-model="reconcileTaskId" data-testid="reconcile-task" placeholder="如 1" style="width:100px" /></label>
        <label><span>批次号(可选)</span><input v-model="reconcileBatch" data-testid="reconcile-batch" placeholder="留空核对全部批次" /></label>
        <button class="btn primary" type="button" data-testid="run-reconcile" @click="runReconcile">开始核对</button>
      </div>
      <table v-if="signals.length" class="data-table" data-testid="signal-table">
        <thead><tr><th>信号编号</th><th>规则编号</th><th>监控指标</th><th>告警范围</th><th>信号状态</th><th>信号版本</th></tr></thead>
        <tbody>
          <tr v-for="signal in signals" :key="String(signal.id)" :data-testid="`signal-row-${signal.id}`">
            <td>{{ signal['信号编号'] }}</td>
            <td>{{ signal['规则编号'] }}</td>
            <td>{{ signal['监控指标'] }}</td>
            <td>{{ signal['告警范围'] }}</td>
            <td>{{ signal['信号状态'] }}</td>
            <td>{{ signal['信号版本'] }}</td>
          </tr>
        </tbody>
      </table>
      <div v-if="reconcileResult" data-testid="reconcile-result">
        <div class="stat-row" style="margin:10px 0">
          <article class="stat-card"><span class="stat-label">核对分片</span><strong class="stat-value">{{ reconcileResult.total }}</strong></article>
          <article class="stat-card"><span class="stat-label">一致</span><strong class="stat-value">{{ reconcileResult.matched }}</strong></article>
          <article class="stat-card"><span class="stat-label">不一致</span><strong class="stat-value">{{ reconcileResult.mismatched }}</strong></article>
        </div>
        <table class="data-table">
          <thead><tr><th>分片编号</th><th>信号编号</th><th>期望告警范围</th><th>清单告警范围</th><th>分片状态</th><th>结论</th></tr></thead>
          <tbody>
            <tr v-for="item in reconcileResult.items" :key="String(item.manifest_id)" :data-testid="`reconcile-row-${item.manifest_id}`" :data-matched="String(item.matched)">
              <td>{{ item.shard_no }}</td>
              <td>{{ item.signal_no }}</td>
              <td>{{ item.expected_alert_range || '—' }}</td>
              <td>{{ item.actual_alert_range }}</td>
              <td>{{ item.shard_status }}</td>
              <td>{{ item.remark }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="v014Error" class="error-text" data-testid="v014-error">{{ v014Error }}</p>
    </section>

    <footer class="page-foot">
      <span>共 {{ total }} 条异常检测记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | null>

const ENDPOINT = '/api/anomaly_detect'
const columns = ["规则编号", "监控指标", "检测算法", "基线窗口", "波动阈值", "告警级别", "静默时段", "规则版本"]
const actions = ["提交校验", "标记生效", "停用规则"]
const statuses = ["草稿", "待校验", "已生效", "已停用"]
const metricNames = ["检测规则总数", "待校验规则", "已生效规则"]

const rows = ref<Row[]>([])
const total = ref(0)
const errorMessage = ref('')
const filters = ref<Record<string, string>>({})
const filterFields = columns.slice(0, 3)
const showCreate = ref(false)
const creating = ref(false)
const createValues = ref<Record<string, string>>({})
const stats = computed(() => [
  { label: metricNames[0], value: total.value },
  { label: metricNames[1], value: rows.value.filter((row) => row.status === statuses[1]).length },
  { label: metricNames[2], value: rows.value.filter((row) => row.status === statuses[statuses.length - 1]).length },
])

function resetFilters() {
  filters.value = {}
  void reload()
}

async function exportRows() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value).toString()
  try {
    const response = await request(`${ENDPOINT}/export?${query}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'anomaly_detect.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

function openCreate() {
  createValues.value = Object.fromEntries(columns.map((field) => [field, '']))
  errorMessage.value = ''
  showCreate.value = true
}

async function submitCreate() {
  creating.value = true
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST', body: JSON.stringify(createValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '登记失败')
    showCreate.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '登记失败'
  } finally {
    creating.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    if (!response.ok) {
      throw new Error('异常检测动作未生效，请稍后重试')
    }
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '异常检测操作失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const query = new URLSearchParams(filters.value as Record<string, string>).toString()
  try {
    const response = await request(`${ENDPOINT}?${query}`)
    if (!response.ok) {
      throw new Error('检测规则列表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '异常检测列表读取失败'
  }
}

// ---------------------------------------------------------- V014 跨模块联动
type SignalRow = Record<string, string | number>
type ReconcileItem = {
  manifest_id: number
  shard_no: string
  signal_no: string
  expected_alert_range: string
  actual_alert_range: string
  shard_status: string
  matched: boolean
  remark: string
}
type ReconcileResult = { total: number; matched: number; mismatched: number; items: ReconcileItem[] }

const signals = ref<SignalRow[]>([])
const signalFilter = ref('')
const reconcileTaskId = ref('1')
const reconcileBatch = ref('')
const reconcileResult = ref<ReconcileResult | null>(null)
const v014Error = ref('')

async function loadSignals() {
  v014Error.value = ''
  try {
    const query = new URLSearchParams()
    if (signalFilter.value) query.set('status', signalFilter.value)
    const response = await request(`${ENDPOINT}/signals?${query.toString()}`)
    if (!response.ok) throw new Error('信号表读取失败')
    const payload = await response.json()
    signals.value = payload.items ?? []
  } catch (error) {
    v014Error.value = error instanceof Error ? error.message : '信号表读取失败'
  }
}

async function runReconcile() {
  v014Error.value = ''
  reconcileResult.value = null
  const taskId = Number(reconcileTaskId.value)
  if (!taskId) {
    v014Error.value = '请填写导出任务 ID'
    return
  }
  try {
    const response = await request(`${ENDPOINT}/reconcile/export/${taskId}`, {
      method: 'POST',
      body: JSON.stringify(reconcileBatch.value ? { batch_no: reconcileBatch.value } : {}),
    })
    const payload = await response.json()
    if (!response.ok || payload.ok === false) {
      throw new Error(payload.detail || payload.message || '核对失败')
    }
    reconcileResult.value = payload
  } catch (error) {
    v014Error.value = error instanceof Error ? error.message : '核对失败'
  }
}

onMounted(() => {
  void reload()
  void loadSignals()
})
</script>

<style scoped>
.v014-panel { margin-top: 24px; padding-top: 12px; border-top: 2px dashed var(--border); }
</style>
