<template>
  <section class="page" data-module="data_export">
    <header class="page-head">
      <div><h2>数据导出</h2><p class="page-desc">按任务分片生成导出文件清单（V014）：每个分片独立完成、失败或取消，整组结果逐条记录。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记导出任务</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
      </div>
    </header>

    <div class="stat-row" data-testid="shard-summary">
      <article v-for="item in summaryCards" :key="item.label" class="stat-card" :data-testid="`summary-${item.label}`">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <label><span>任务筛选</span>
        <select v-model="taskFilter" data-testid="task-filter" @change="reloadManifests(Number(taskFilter) || undefined)">
          <option value="">全部任务</option>
          <option v-for="task in allItems" :key="task.id" :value="task.id">{{ task.name || task.id }}（#{{ task.id }}）</option>
        </select>
      </label>
      <button class="btn" type="button" @click="reload">查询</button>
      <button class="btn primary" type="button" data-testid="open-batch" @click="openBatch">整批提交演示</button>
      <button class="btn" type="button" data-testid="open-retry" @click="openRetry">批次重试</button>
      <button class="btn ghost" type="button" data-testid="open-archive" @click="showArchive = true; reloadArchive()">V014 留档核对</button>
    </div>

    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <h3 style="margin:18px 0 8px;">导出文件清单（V014 分片工作区）</h3>
    <p class="page-desc" data-testid="sync-hint">工作台与详情抽屉共用同一接口数据，最后加载：{{ lastLoadedAt || '—' }}</p>
    <table class="data-table" data-testid="manifest-table">
      <thead>
        <tr>
          <th v-for="column in manifestColumns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in manifests" :key="row.id" :data-testid="`manifest-row-${row.id}`" :data-batch-no="row.batch_no" :data-status="row.shard_status">
          <td>{{ row.shard_no }}</td>
          <td>{{ row.batch_no }}</td>
          <td>{{ row.task_no }}</td>
          <td>{{ row.signal_no }}</td>
          <td>{{ row.alert_range }}</td>
          <td><span class="shard-badge" :class="`shard-${row.shard_status}`">{{ row.shard_status }}</span></td>
          <td>{{ row.file_name || '—' }}</td>
          <td>{{ row.error_message || '—' }}</td>
          <td>{{ row.retry_count }}</td>
          <td class="row-actions">
            <button class="link" type="button" data-testid="view" @click="viewDetail(row)">查看详情</button>
            <button v-if="row.downloadable" class="link" type="button" data-testid="download-shard" @click="downloadShard(row.id)">下载</button>
            <button v-if="row.shard_status === '待处理' || row.shard_status === '处理中' || row.shard_status === '失败'" class="link" type="button" @click="markSuccess(row)">标记成功</button>
            <button v-if="row.shard_status !== '成功' && row.shard_status !== '已取消'" class="link" type="button" @click="markFailed(row)">标记失败</button>
            <button v-if="row.shard_status !== '成功' && row.shard_status !== '已取消'" class="link" type="button" @click="cancelWithPolicy(row, 'keep_success')">取消(保留成功)</button>
            <button v-if="row.shard_status !== '成功' && row.shard_status !== '已取消'" class="link" type="button" @click="cancelWithPolicy(row, 'retry_batch')">取消(整批重试)</button>
          </td>
        </tr>
        <tr v-if="!manifests.length">
          <td :colspan="manifestColumns.length + 1" class="empty-state">暂无分片清单，可使用「整批提交演示」生成一批</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span v-if="manifestError || errorMessage" class="error-text" data-testid="manifest-error">{{ manifestError || errorMessage }}</span>
      <span v-else>共 {{ manifests.length }} 条分片清单</span>
    </footer>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记导出任务</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>

    <div v-if="showBatch" class="modal-backdrop" data-testid="batch-modal">
      <form class="modal" @submit.prevent="submitDemoBatch"><h3>整组导出：统一提交一批分片清单</h3>
        <label class="create-field"><span>导出任务</span>
          <select v-model.number="batchForm.task_id" required>
            <option v-for="task in allItems" :key="task.id" :value="task.id">{{ task.name || task.id }}（#{{ task.id }}）</option>
          </select>
        </label>
        <label class="create-field"><span>批次号</span><input v-model="batchForm.batch_no" required /></label>
        <label class="create-field"><span>分片数量</span><input v-model.number="batchForm.shard_count" type="number" min="1" max="8" required /></label>
        <p class="page-desc">演示将生成 3 种独立结果：成功（带完整文件元数据）、失败、取消；任一中断不影响其余分片。</p>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showBatch=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-batch">统一提交</button>
        </div>
      </form>
    </div>

    <div v-if="showRetry" class="modal-backdrop" data-testid="retry-modal" @click.self="showRetry=false">
      <form class="modal" @submit.prevent="submitRetry">
        <h3>批次重试：只补未完成分片</h3>
        <label class="create-field"><span>导出任务</span>
          <select v-model.number="retryForm.task_id" required>
            <option v-for="task in allItems" :key="task.id" :value="task.id">{{ task.name || task.id }}（#{{ task.id }}）</option>
          </select>
        </label>
        <label class="create-field"><span>批次号</span><input v-model="retryForm.batch_no" required placeholder="如 BATCH-xxx" /></label>
        <p class="page-desc">已完成分片及其文件保持不动，仅重置失败、取消与待处理分片。</p>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showRetry=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-retry">执行重试</button>
        </div>
      </form>
    </div>

    <div v-if="showArchive" class="modal-backdrop" data-testid="archive-modal" @click.self="showArchive=false">
      <div class="modal" style="max-width:820px">
        <h3>V014 事务留档核对</h3>
        <table class="data-table">
          <thead><tr><th>留档编号</th><th>结果</th><th>批次</th><th>残留数</th><th>时间</th><th>说明</th></tr></thead>
          <tbody>
            <tr v-for="record in archiveRecords" :key="record.id" :data-testid="`archive-${record.id}`">
              <td>{{ record.archive_no }}</td>
              <td>{{ record.result }}</td>
              <td>{{ record.batch_no || '—' }}</td>
              <td>{{ record.residue_count ?? '—' }}</td>
              <td>{{ record.archived_at }}</td>
              <td>{{ record.remark }}</td>
            </tr>
            <tr v-if="!archiveRecords.length"><td colspan="6" class="empty-state">暂无留档记录</td></tr>
          </tbody>
        </table>
        <div class="page-actions" style="margin-top:12px"><button class="btn" type="button" @click="showArchive=false">关闭</button></div>
      </div>
    </div>

    <ManifestDetail :manifest="detailManifest" @close="detailManifest = null" @download="downloadShard" />
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
import ManifestDetail from './ManifestDetail.vue'
import { useExportManifests, type ShardManifest } from '@/composables/useExportManifests'

const ENDPOINT = '/api/data_export'
const columns = ['任务编号', '数据来源', '导出格式', '字段清单', '数据范围', '压缩方式', '分片大小', '导出批次']
const manifestColumns = ['分片编号', '批次号', '任务编号', '信号编号', '告警范围', '分片状态', '文件名称', '错误信息', '重试次数']

const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string, string>>({})
const allItems = ref<any[]>([])
const taskFilter = ref('')
const showBatch = ref(false)
const showRetry = ref(false)
const showArchive = ref(false)
const detailManifest = ref<ShardManifest | null>(null)
const batchForm = ref({ task_id: 1, batch_no: `BATCH-${Date.now()}`, shard_count: 3 })
const retryForm = ref({ task_id: 1, batch_no: '' })

const {
  manifests, archiveRecords, lastLoadedAt, summary, errorMessage: manifestError,
  reloadManifests, reloadArchive, submitBatch, shardAction, retryBatch, downloadShard,
} = useExportManifests()

const summaryCards = computed(() => [
  { label: '清单总数', value: manifests.value.length },
  { label: '成功分片', value: summary.value['成功'] },
  { label: '失败分片', value: summary.value['失败'] },
  { label: '取消分片', value: summary.value['已取消'] },
  { label: '待处理分片', value: summary.value['待处理'] + summary.value['处理中'] },
])

function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if (!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if (!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }

async function reload() {
  try {
    const res = await request(ENDPOINT); const data = await res.json()
    allItems.value = data.items || []
    if (!batchForm.value.task_id && allItems.value[0]) batchForm.value.task_id = allItems.value[0].id
    applyFilter()
    await reloadManifests(Number(taskFilter.value) || undefined)
  } catch (e) { errorMessage.value = '加载失败' }
}
function applyFilter() {
  let items = allItems.value
  if (dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if (dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if (!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch (e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }

function openBatch() {
  batchForm.value = { task_id: allItems.value[0]?.id ?? 1, batch_no: `BATCH-${Date.now()}`, shard_count: 3 }
  showBatch.value = true
}
function openRetry() {
  const firstBatch = manifests.value[0]?.batch_no ?? ''
  retryForm.value = { task_id: Number(taskFilter.value) || allItems.value[0]?.id || 1, batch_no: firstBatch }
  showRetry.value = true
}
async function submitRetry() {
  try {
    await retryBatch(Number(retryForm.value.task_id), retryForm.value.batch_no)
    showRetry.value = false
    errorMessage.value = ''
  } catch (e: any) {
    manifestError.value = e.message
  }
}
async function submitDemoBatch() {
  const count = Number(batchForm.value.shard_count) || 3
  const shards = Array.from({ length: count }, (_, index) => {
    if (index % 3 === 1) return { shard_index: index, result: 'failed', error_message: '源分区缺失，分片独立失败' }
    if (index % 3 === 2) return { shard_index: index, result: 'canceled', error_message: '任务取消，仅影响当前分片' }
    return {
      shard_index: index, result: 'success',
      file_metadata: {
        file_name: `${batchForm.value.batch_no}-S${index + 1}.csv`,
        file_path: `/data/export/${batchForm.value.batch_no}/S${index + 1}.csv`,
        file_size: '20480', checksum: `md5-${batchForm.value.batch_no}-${index}`, content_type: 'text/csv',
      },
    }
  })
  try {
    await submitBatch({ ...batchForm.value, shards })
    showBatch.value = false
    errorMessage.value = ''
  } catch (e: any) {
    manifestError.value = e.message
  }
}

function viewDetail(row: ShardManifest) { detailManifest.value = row }
async function markSuccess(row: ShardManifest) {
  try {
    await shardAction(`/manifests/${row.id}/complete`, {
      expected_version: row.version,
      file_metadata: {
        file_name: `${row.shard_no}.csv`, file_path: `/data/export/${row.batch_no}/${row.shard_no}.csv`,
        file_size: '20480', checksum: `md5-${row.id}`, content_type: 'text/csv',
      },
    })
  } catch (e: any) { manifestError.value = e.message }
}
async function markFailed(row: ShardManifest) {
  try { await shardAction(`/manifests/${row.id}/fail`, { expected_version: row.version, error_message: '工作台手动标记失败' }) }
  catch (e: any) { manifestError.value = e.message }
}
async function cancelWithPolicy(row: ShardManifest, policy: 'keep_success' | 'retry_batch') {
  try { await shardAction(`/manifests/${row.id}/cancel`, { expected_version: row.version, policy }) }
  catch (e: any) { manifestError.value = e.message }
}

onMounted(reload)
</script>

<style scoped>
.shard-badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; color: #fff; }
.shard-成功 { background: #16a34a; }
.shard-失败 { background: #dc2626; }
.shard-已取消 { background: #64748b; }
.shard-待处理, .shard-处理中 { background: #2563eb; }
.row-actions { white-space: nowrap; }
</style>
