/**
 * V014 导出文件清单共享数据层。
 *
 * 工作台（数据导出页）与详情抽屉都只通过这里拿数据，保证两处展示
 * 完全相同的业务数据，不会各自维护一份口径不一致的副本。
 */
import { computed, ref } from 'vue'

import { request } from '@/api/client'

export type ShardManifest = {
  id: number
  task_id: number
  task_no: string
  batch_no: string
  shard_index: number
  shard_total: number
  shard_no: string
  signal_no: string
  alert_range: string
  shard_status: '待处理' | '处理中' | '成功' | '失败' | '已取消'
  file_name: string
  file_metadata: Record<string, string> | null
  error_message: string
  published: boolean
  plan_committed: boolean
  retry_count: number
  version: number
  downloadable: boolean
  created_at: string
  finished_at: string
}

export type ArchiveRecord = {
  id: number
  archive_no: string
  spec_version: string
  result: string
  remark: string
  archived_at: string
  task_id?: number
  batch_no?: string
  residue_count?: number
}

const ENDPOINT = '/api/data_export'

const manifests = ref<ShardManifest[]>([])
const archiveRecords = ref<ArchiveRecord[]>([])
const loading = ref(false)
const errorMessage = ref('')
const lastLoadedAt = ref('')

export function useExportManifests() {
  const summary = computed(() => {
    const base = { 成功: 0, 失败: 0, 已取消: 0, 待处理: 0, 处理中: 0 }
    for (const item of manifests.value) {
      base[item.shard_status as keyof typeof base] =
        (base[item.shard_status as keyof typeof base] ?? 0) + 1
    }
    return base
  })

  async function reloadManifests(taskId?: number) {
    loading.value = true
    errorMessage.value = ''
    try {
      const query = taskId ? `?task_id=${taskId}` : ''
      const response = await request(`${ENDPOINT}/manifests${query}`)
      if (!response.ok) throw new Error('导出文件清单读取失败')
      const payload = await response.json()
      manifests.value = payload.items ?? []
      lastLoadedAt.value = new Date().toLocaleTimeString()
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : '导出文件清单读取失败'
    } finally {
      loading.value = false
    }
  }

  async function reloadArchive() {
    try {
      const response = await request(`${ENDPOINT}/manifests/archive`)
      if (!response.ok) throw new Error('V014 留档读取失败')
      const payload = await response.json()
      archiveRecords.value = payload.items ?? []
    } catch (error) {
      errorMessage.value = error instanceof Error ? error.message : 'V014 留档读取失败'
    }
  }

  async function submitBatch(body: Record<string, unknown>) {
    const response = await request(`${ENDPOINT}/manifests/batch`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '整批提交失败')
    await reloadManifests()
    return payload
  }

  async function shardAction(path: string, body: Record<string, unknown>) {
    const response = await request(`${ENDPOINT}${path}`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '分片操作失败')
    await reloadManifests()
    return payload
  }

  async function retryBatch(taskId: number, batchNo: string) {
    const response = await request(`${ENDPOINT}/tasks/${taskId}/retry`, {
      method: 'POST',
      body: JSON.stringify({ batch_no: batchNo }),
    })
    const payload = await response.json()
    if (!response.ok) throw new Error(payload.detail || '批次重试失败')
    await reloadManifests()
    return payload
  }

  function downloadShard(manifestId: number) {
    window.open(`${ENDPOINT}/manifests/${manifestId}/download`, '_blank')
  }

  return {
    manifests,
    archiveRecords,
    loading,
    errorMessage,
    lastLoadedAt,
    summary,
    reloadManifests,
    reloadArchive,
    submitBatch,
    shardAction,
    retryBatch,
    downloadShard,
  }
}
