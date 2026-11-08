<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import {
  ApiError,
  askChat,
  streamChat,
  type Citation,
  type RefuseReason,
  type RetrievalMeta,
} from '@/api'
import type { StreamHandle } from '@/api'
import CitationCard from '@/components/CitationCard.vue'

type Phase = 'idle' | 'retrieving' | 'streaming' | 'done' | 'error' | 'refused'

const query = ref('')
const answer = ref('')
const citations = ref<Citation[]>([])
const retrieval = ref<RetrievalMeta | null>(null)
const phase = ref<Phase>('idle')
const errorMsg = ref('')
const refuseReason = ref<RefuseReason | null>(null)
const useStream = ref(true)

let handle: StreamHandle | null = null

const REFUSE_TEXT: Record<RefuseReason, string> = {
  no_hit: '没有召回到任何相关通知',
  all_stale: '召回的通知都已过有效期',
  low_score: '召回结果相关度过低',
}

function reset() {
  answer.value = ''
  citations.value = []
  retrieval.value = null
  refuseReason.value = null
  errorMsg.value = ''
}

function stop() {
  handle?.close()
  handle = null
}

async function submit() {
  const q = query.value.trim()
  if (!q || phase.value === 'retrieving' || phase.value === 'streaming') return

  stop()
  reset()
  phase.value = 'retrieving'

  if (!useStream.value) {
    try {
      const res = await askChat({ query: q })
      answer.value = res.answer
      citations.value = res.citations
      retrieval.value = res.retrieval
      refuseReason.value = res.refuse_reason
      phase.value = res.refused ? 'refused' : 'done'
    } catch (err) {
      errorMsg.value = err instanceof ApiError ? err.message : '请求失败'
      phase.value = 'error'
    }
    return
  }

  handle = streamChat(q, {
    onMeta: (e) => {
      retrieval.value = e.retrieval
      phase.value = 'streaming'
    },
    onSources: (e) => {
      citations.value = e.citations
    },
    onDelta: (text) => {
      answer.value += text
    },
    onDone: (e) => {
      refuseReason.value = e.refuse_reason ?? null
      phase.value = e.refused ? 'refused' : 'done'
      handle = null
    },
    onError: (e) => {
      errorMsg.value = e instanceof ApiError ? e.message : e.message
      phase.value = 'error'
      handle = null
    },
  })
}

function onEnter(ev: KeyboardEvent) {
  if (ev.shiftKey) return
  ev.preventDefault()
  void submit()
}

onBeforeUnmount(stop)
</script>

<template>
  <div class="chat">
    <header class="head">
      <h1>教学通知问答</h1>
      <p class="sub">
        回答只依据韶关学院教务部「教学通知」栏目，并标注每条依据的
        <strong>有效期</strong> —— 过期版本会被显式标出，不会当作现行规定。
      </p>
    </header>

    <div class="ask">
      <el-input
        v-model="query"
        type="textarea"
        :rows="3"
        resize="none"
        placeholder="例如：2026年转专业报名需要什么条件？（Enter 发送，Shift+Enter 换行）"
        :disabled="phase === 'retrieving' || phase === 'streaming'"
        @keydown.enter="onEnter"
      />
      <div class="actions">
        <el-switch v-model="useStream" active-text="流式输出" :disabled="phase === 'streaming'" />
        <el-button v-if="phase === 'streaming'" type="danger" plain @click="stop"> 停止 </el-button>
        <el-button
          type="primary"
          :loading="phase === 'retrieving'"
          :disabled="!query.trim() || phase === 'streaming'"
          @click="submit"
        >
          提问
        </el-button>
      </div>
    </div>

    <el-alert
      v-if="phase === 'error'"
      type="error"
      :closable="false"
      show-icon
      :title="errorMsg"
      class="block"
    >
      <template #default>
        <el-button size="small" @click="submit">重试</el-button>
      </template>
    </el-alert>

    <el-alert
      v-if="phase === 'refused'"
      type="warning"
      :closable="false"
      show-icon
      class="block"
      :title="`未找到可依据的现行通知${refuseReason ? '（' + REFUSE_TEXT[refuseReason] + '）' : ''}`"
    >
      <p class="refuse">{{ answer }}</p>
    </el-alert>

    <div v-if="answer && phase !== 'refused'" class="answer block">
      <p>{{ answer }}<span v-if="phase === 'streaming'" class="caret">▍</span></p>
    </div>

    <div v-if="citations.length" class="sources block">
      <div class="sources-head">
        <h2>依据（{{ citations.length }}）</h2>
        <span v-if="retrieval" class="filters">
          {{ retrieval.mode }} · 候选 {{ retrieval.candidates }} ·
          {{ retrieval.applied_filters.length ? retrieval.applied_filters.join(' / ') : '无过滤' }}
        </span>
      </div>
      <CitationCard
        v-for="(c, i) in citations"
        :key="c.doc_id + '#' + i"
        :citation="c"
        :index="i"
      />
    </div>
  </div>
</template>

<style scoped>
.chat {
  max-width: 860px;
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
  line-height: 1.7;
}
.actions {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
  margin-top: 12px;
}
.block {
  margin-top: 20px;
}
.refuse {
  margin: 8px 0 0;
  line-height: 1.7;
}
.answer p {
  margin: 0;
  line-height: 1.8;
  white-space: pre-wrap;
}
.caret {
  animation: blink 1s step-end infinite;
  color: var(--el-color-primary);
}
@keyframes blink {
  50% {
    opacity: 0;
  }
}
.sources-head {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  border-bottom: 1px solid var(--el-border-color);
  padding-bottom: 8px;
}
.sources-head h2 {
  margin: 0;
  font-size: 16px;
}
.filters {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  font-family: ui-monospace, monospace;
}
</style>
