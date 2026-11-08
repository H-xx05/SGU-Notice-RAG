<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { ApiError, fetchNotices, type NoticeItem, type NoticeQuery } from '@/api'

const items = ref<NoticeItem[]>([])
const total = ref(0)
const loading = ref(false)
const errorMsg = ref('')

const filters = reactive<NoticeQuery>({
  page: 1,
  page_size: 20,
  q: '',
  year: undefined,
  include_stale: true,
})

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    const page = await fetchNotices({ ...filters })
    items.value = page.items
    total.value = page.total
  } catch (err) {
    errorMsg.value = err instanceof ApiError ? err.message : '加载失败'
    items.value = []
    total.value = 0
  } finally {
    loading.value = false
  }
}

function onPage(p: number) {
  filters.page = p
  void load()
}

watch(
  () => [filters.q, filters.year, filters.include_stale],
  () => {
    filters.page = 1
    void load()
  },
)

onMounted(load)
</script>

<template>
  <div class="notices">
    <header class="head">
      <h1>通知清单</h1>
      <p class="sub">按发布时间排序。同一条政策的多个版本会各自成行，用「现行」标记区分。</p>
    </header>

    <div class="filters">
      <el-input v-model="filters.q" placeholder="按标题搜索" clearable style="max-width: 260px" />
      <el-input-number v-model="filters.year" :min="2000" :max="2100" placeholder="年份" />
      <el-checkbox v-model="filters.include_stale">显示已过期版本</el-checkbox>
      <el-button :loading="loading" @click="load">刷新</el-button>
    </div>

    <el-alert
      v-if="errorMsg"
      type="error"
      :title="errorMsg"
      :closable="false"
      show-icon
      class="block"
    />

    <el-table v-loading="loading" :data="items" class="block" empty-text="没有匹配的通知">
      <el-table-column prop="title" label="标题" min-width="320">
        <template #default="{ row }">
          <a :href="row.source_url" target="_blank" rel="noopener noreferrer">{{ row.title }}</a>
        </template>
      </el-table-column>
      <el-table-column prop="doc_version" label="版本" width="100" />
      <el-table-column prop="publish_date" label="发布" width="120" />
      <el-table-column label="有效期" width="230">
        <template #default="{ row }">
          {{ row.effective_from ?? '—' }} ~ {{ row.effective_to ?? '长期有效' }}
        </template>
      </el-table-column>
      <el-table-column label="状态" width="110">
        <template #default="{ row }">
          <el-tag v-if="!row.is_current" type="danger" size="small">已被替代</el-tag>
          <el-tag v-else type="success" size="small">现行</el-tag>
        </template>
      </el-table-column>
    </el-table>

    <el-pagination
      v-if="total > (filters.page_size ?? 20)"
      class="pager"
      layout="prev, pager, next, total"
      :total="total"
      :page-size="filters.page_size"
      :current-page="filters.page"
      @current-change="onPage"
    />
  </div>
</template>

<style scoped>
.notices {
  max-width: 1100px;
  margin: 0 auto;
  padding: 32px 20px 80px;
}
.head h1 {
  margin: 0 0 8px;
  font-size: 24px;
}
.sub {
  margin: 0 0 24px;
  color: var(--el-text-color-secondary);
}
.filters {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 16px;
}
.block {
  margin-top: 8px;
}
.pager {
  margin-top: 16px;
  justify-content: flex-end;
}
</style>
