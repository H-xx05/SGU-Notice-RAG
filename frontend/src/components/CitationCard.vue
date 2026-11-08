<script setup lang="ts">
import TimeBadge from './TimeBadge.vue'
import type { Citation } from '@/api'

defineProps<{ citation: Citation; index: number }>()
</script>

<template>
  <div class="citation">
    <div class="head">
      <span class="idx">[{{ index + 1 }}]</span>
      <a :href="citation.source_url" target="_blank" rel="noopener noreferrer" class="title">
        {{ citation.title }}
      </a>
      <el-tag size="small" effect="plain" disable-transitions>{{ citation.doc_version }}</el-tag>
      <TimeBadge
        :bucket="citation.recency_bucket"
        :effective-from="citation.effective_from"
        :effective-to="citation.effective_to"
      />
    </div>
    <div class="path">{{ citation.section_path }}</div>
    <p v-if="citation.snippet" class="snippet">{{ citation.snippet }}</p>
    <div class="meta">
      <span>发布 {{ citation.publish_date }}</span>
      <span>相关度 {{ citation.score.toFixed(4) }}</span>
    </div>
  </div>
</template>

<style scoped>
.citation {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.citation:last-child {
  border-bottom: none;
}
.head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.idx {
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, monospace;
}
.title {
  font-weight: 600;
  color: var(--el-color-primary);
  text-decoration: none;
}
.title:hover {
  text-decoration: underline;
}
.path,
.meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  margin-top: 4px;
}
.meta {
  display: flex;
  gap: 16px;
}
.snippet {
  margin: 8px 0 0;
  line-height: 1.7;
  color: var(--el-text-color-regular);
}
</style>
