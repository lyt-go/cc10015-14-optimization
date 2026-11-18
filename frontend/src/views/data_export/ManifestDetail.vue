<template>
  <div v-if="manifest" class="modal-backdrop" data-testid="manifest-detail" @click.self="$emit('close')">
    <div class="modal manifest-detail">
      <h3>分片清单详情（V014）</h3>
      <p class="page-desc">详情抽屉与工作台读取同一份后端数据，业务字段保持一致。</p>
      <table class="data-table">
        <tbody>
          <tr><th>清单编号</th><td data-testid="detail-id">{{ manifest.id }}</td></tr>
          <tr><th>分片编号</th><td>{{ manifest.shard_no }}</td></tr>
          <tr><th>所属批次</th><td>{{ manifest.batch_no }}</td></tr>
          <tr><th>所属任务</th><td>{{ manifest.task_no }}（#{{ manifest.task_id }}）</td></tr>
          <tr><th>分片序号</th><td>{{ manifest.shard_index + 1 }} / {{ manifest.shard_total }}</td></tr>
          <tr><th>信号编号</th><td>{{ manifest.signal_no }}</td></tr>
          <tr><th>告警范围</th><td>{{ manifest.alert_range }}</td></tr>
          <tr>
            <th>分片状态</th>
            <td><span class="shard-badge" :data-testid="`detail-status-${manifest.id}`" :class="badgeClass">{{ manifest.shard_status }}</span></td>
          </tr>
          <tr><th>文件名</th><td>{{ manifest.file_name || '—' }}</td></tr>
          <tr><th>文件元数据</th><td><pre class="meta-pre">{{ manifest.file_metadata ? JSON.stringify(manifest.file_metadata, null, 2) : '未落库' }}</pre></td></tr>
          <tr><th>错误信息</th><td>{{ manifest.error_message || '—' }}</td></tr>
          <tr><th>是否发布</th><td>{{ manifest.published ? '已发布' : '未发布' }}</td></tr>
          <tr><th>重试次数</th><td>{{ manifest.retry_count }}</td></tr>
          <tr><th>乐观锁版本</th><td>{{ manifest.version }}</td></tr>
          <tr><th>创建时间</th><td>{{ manifest.created_at }}</td></tr>
          <tr><th>完成时间</th><td>{{ manifest.finished_at || '—' }}</td></tr>
        </tbody>
      </table>
      <div class="page-actions" style="margin-top:12px">
        <button
          v-if="manifest.downloadable"
          class="btn primary"
          type="button"
          data-testid="detail-download"
          @click="$emit('download', manifest.id)"
        >下载分片文件</button>
        <button class="btn" type="button" data-testid="detail-close" @click="$emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import type { ShardManifest } from '@/composables/useExportManifests'

const props = defineProps<{ manifest: ShardManifest | null }>()
defineEmits<{ (event: 'close'): void; (event: 'download', manifestId: number): void }>()

const badgeClass = computed(() => {
  if (!props.manifest) return ''
  return `shard-${props.manifest.shard_status}`
})
</script>

<style scoped>
.manifest-detail { max-width: 720px; width: 100%; max-height: 86vh; overflow-y: auto; }
.meta-pre { margin: 0; white-space: pre-wrap; word-break: break-all; font-size: 12px; }
.shard-badge { display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: 12px; color: #fff; }
.shard-成功 { background: #16a34a; }
.shard-失败 { background: #dc2626; }
.shard-已取消 { background: #64748b; }
.shard-待处理, .shard-处理中 { background: #2563eb; }
</style>
