<script setup lang="ts">
import { BarChart, RadarChart } from 'echarts/charts'
import { GridComponent, RadarComponent, TitleComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { ApiError, emptyEvalSummary, fetchEvalSummary, type EvalSummary } from '@/api'

// 按需注册：整包 echarts 有 1MB，这里只要柱图 + 雷达图
echarts.use([
  BarChart,
  RadarChart,
  GridComponent,
  RadarComponent,
  TitleComponent,
  TooltipComponent,
  CanvasRenderer,
])

const summary = ref<EvalSummary>(emptyEvalSummary)
const loading = ref(false)
const errorMsg = ref('')

const staleChartEl = ref<HTMLDivElement>()
const metricChartEl = ref<HTMLDivElement>()
const staleChart = shallowRef<echarts.ECharts>()
const metricChart = shallowRef<echarts.ECharts>()

const METRIC_LABELS: Record<string, string> = {
  faithfulness: '忠实度',
  answer_relevancy: '答案相关性',
  context_precision: '上下文精确率',
  context_recall: '上下文召回率',
  stale_recall_rate: '过期误召回率',
  refusal_rate: '拒答准确率',
}

function render() {
  if (staleChartEl.value) {
    staleChart.value ??= echarts.init(staleChartEl.value)
    staleChart.value.setOption({
      title: { text: '各年度过期误召回率', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: { trigger: 'axis' },
      grid: { left: 56, right: 24, top: 56, bottom: 40 },
      xAxis: { type: 'category', data: summary.value.by_year.map((y) => String(y.year)) },
      yAxis: {
        type: 'value',
        max: 0.5,
        axisLabel: { formatter: (v: number) => `${(v * 100).toFixed(0)}%` },
      },
      series: [
        {
          type: 'bar',
          data: summary.value.by_year.map((y) => y.stale_recall_rate),
          itemStyle: { color: '#e6a23c' },
          label: {
            show: true,
            position: 'top',
            formatter: (p: { value: number }) => `${(p.value * 100).toFixed(1)}%`,
          },
        },
      ],
    })
  }

  if (metricChartEl.value) {
    metricChart.value ??= echarts.init(metricChartEl.value)
    const entries = Object.entries(summary.value.metrics).filter(([, v]) => v !== null)
    metricChart.value.setOption({
      title: { text: '核心指标', left: 'center', textStyle: { fontSize: 14 } },
      tooltip: {},
      radar: {
        indicator: entries.map(([k]) => ({ name: METRIC_LABELS[k] ?? k, max: 1 })),
        radius: '62%',
      },
      series: [
        {
          type: 'radar',
          data: [{ value: entries.map(([, v]) => v), name: '本批次' }],
          areaStyle: { opacity: 0.25 },
        },
      ],
    })
  }
}

async function load() {
  loading.value = true
  errorMsg.value = ''
  try {
    summary.value = await fetchEvalSummary()
    render()
  } catch (err) {
    errorMsg.value = err instanceof ApiError ? err.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function onResize() {
  staleChart.value?.resize()
  metricChart.value?.resize()
}

onMounted(() => {
  void load()
  window.addEventListener('resize', onResize)
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  staleChart.value?.dispose()
  metricChart.value?.dispose()
})
</script>

<template>
  <div v-loading="loading" class="eval">
    <header class="head">
      <h1>评估看板</h1>
      <p class="sub">
        数据由 E 组跑批写入 Postgres。<strong>过期误召回率</strong>是本项目的第一指标 ——
        它衡量有多少回答引用了已失效的旧版本通知。
      </p>
    </header>

    <el-alert
      v-if="errorMsg"
      type="error"
      :title="errorMsg"
      :closable="false"
      show-icon
      class="block"
    />

    <el-empty
      v-if="!summary.run_id && !loading"
      description="还没有评估数据。E 组跑一次 `python -m eval.run --write` 后这里会出现。"
    />

    <template v-else>
      <div class="kpis">
        <el-card v-for="(v, k) in summary.metrics" :key="k" shadow="never" class="kpi">
          <div class="kpi-label">{{ METRIC_LABELS[k] ?? k }}</div>
          <div class="kpi-value">{{ v === null ? '—' : (v * 100).toFixed(1) + '%' }}</div>
        </el-card>
      </div>

      <div class="charts">
        <div ref="staleChartEl" class="chart" />
        <div ref="metricChartEl" class="chart" />
      </div>

      <el-table v-if="summary.regressions.length" :data="summary.regressions" class="block">
        <el-table-column prop="metric" label="指标" />
        <el-table-column label="对比上批次">
          <template #default="{ row }">
            <span :class="row.delta < 0 ? 'down' : 'up'">
              {{ row.delta > 0 ? '+' : '' }}{{ (row.delta * 100).toFixed(1) }}%
            </span>
          </template>
        </el-table-column>
        <el-table-column prop="vs_run" label="基准批次" />
      </el-table>

      <p class="foot">
        批次 {{ summary.run_id }} · 数据集 {{ summary.dataset_size }} 条 ·
        {{ summary.created_at ?? '—' }}
      </p>
    </template>
  </div>
</template>

<style scoped>
.eval {
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
  line-height: 1.7;
}
.kpis {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
  gap: 12px;
}
.kpi-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.kpi-value {
  font-size: 22px;
  font-weight: 600;
  margin-top: 4px;
}
.charts {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 24px;
}
@media (max-width: 800px) {
  .charts {
    grid-template-columns: 1fr;
  }
}
.chart {
  height: 280px;
}
.block {
  margin-top: 16px;
}
.down {
  color: var(--el-color-danger);
}
.up {
  color: var(--el-color-success);
}
.foot {
  margin-top: 24px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
