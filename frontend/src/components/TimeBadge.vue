<script setup lang="ts">
import { computed } from 'vue'
import type { RecencyBucket } from '@/api'

/**
 * 时效角标。**这是整个产品的核心 UI 元素** ——
 * 用户必须一眼看出"这条依据现在还有效吗"。
 *
 * 三态语义（与 B 侧 time_filter.judge 的 RecencyBucket 对齐）：
 *   current 现行有效   stale 已过期   unknown 无法判定
 *
 * 注意：effective_to 为 null 表示"长期有效"，是 current，不是 stale。
 * 这个坑在 Mock 里专门放了样例。
 */
const props = defineProps<{
  bucket: RecencyBucket
  effectiveFrom?: string | null
  effectiveTo?: string | null
}>()

const label = computed(() => {
  switch (props.bucket) {
    case 'current':
      return '现行有效'
    case 'stale':
      return '已过期'
    default:
      return '时效未明'
  }
})

const type = computed(() => {
  switch (props.bucket) {
    case 'current':
      return 'success'
    case 'stale':
      return 'danger'
    default:
      return 'info'
  }
})

const range = computed(() => {
  const from = props.effectiveFrom ?? '—'
  const to = props.effectiveTo ?? '长期有效'
  return `${from} ~ ${to}`
})
</script>

<template>
  <span class="time-badge">
    <el-tag :type="type" size="small" effect="dark" disable-transitions>{{ label }}</el-tag>
    <span class="range">{{ range }}</span>
  </span>
</template>

<style scoped>
.time-badge {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}
.range {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
