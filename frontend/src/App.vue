<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import echarts from './echarts'
import type { EChartsType } from 'echarts/core'
import { api } from './api'
import type { FileItem, Health, HistoryDaily, Job, ParsedIntent, PipelineResult } from './types'

const EXAMPLES = [
  '最近3个月的上海充电桩招标信息都有哪些，请汇总后每天9:00发送给我',
  '最近1周的合肥服务器采购公告',
  '上个月北京的无人机采购招标信息',
]

const health = ref<Health | null>(null)
const backendDown = ref(false)
const query = ref('')
const intent = ref<ParsedIntent | null>(null)
const result = ref<ParsedResult | null>(null)
const tasks = ref<Job[]>([])
const files = ref<FileItem[]>([])
const writeDoc = ref(true)
const schedule = ref(true)
const loading = reactive({ parse: false, run: false, meta: false, push: false })
const error = ref('')
const pushMsg = ref('')
const pushOk = ref(false)

type ParsedResult = PipelineResult

const enabledSites = computed(() => health.value?.sites.filter((s) => s.enabled).length ?? 0)

// ---------------- 图表（echarts 按需注册，见 echarts.ts） ----------------

const donutEl = ref<HTMLElement | null>(null)
const barEl = ref<HTMLElement | null>(null)
const trendEl = ref<HTMLElement | null>(null)
let donutIns: EChartsType | null = null
let barIns: EChartsType | null = null
let trendIns: EChartsType | null = null
const trendData = ref<HistoryDaily[]>([])

function renderTrend() {
  if (!trendEl.value || !trendData.value.length) return
  trendIns = trendIns || echarts.init(trendEl.value)
  trendIns.setOption({
    tooltip: { trigger: 'axis' },
    legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: '#64748b', fontSize: 11 } },
    grid: { left: 8, right: 16, top: 16, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: trendData.value.map((d) => d.date.slice(5)),
      axisLine: { lineStyle: { color: '#dbe3ec' } },
      axisTick: { show: false },
      axisLabel: { color: '#47586b', fontSize: 11 },
    },
    yAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef2f7' } } },
    series: [
      {
        name: '新增',
        type: 'line',
        smooth: true,
        symbolSize: 6,
        data: trendData.value.map((d) => d.fresh),
        lineStyle: { width: 2.5, color: '#22a35c' },
        itemStyle: { color: '#22a35c' },
        areaStyle: { color: 'rgba(34,163,92,0.12)' },
      },
      {
        name: '运行轮次',
        type: 'bar',
        barWidth: 10,
        itemStyle: { borderRadius: [4, 4, 0, 0], color: '#9db9d4' },
        data: trendData.value.map((d) => d.runs),
      },
    ],
  })
}

function renderCharts() {
  const r = result.value
  if (!r || !r.items.length) return
  const fresh = r.items.filter((n) => n.is_new).length
  const dup = r.items.length - fresh
  if (donutEl.value) {
    // 图表容器在 v-if="result" 里：每次执行会先把 result 置空，容器随之卸载再重建。
    // 旧实例仍绑在已卸载的节点上，直接复用会把图画到看不见的地方（第二次执行起图表空白）。
    donutIns?.dispose()
    donutIns = echarts.init(donutEl.value)
    donutIns.setOption({
      tooltip: { trigger: 'item' },
      legend: { bottom: 0, icon: 'circle', itemWidth: 8, itemHeight: 8, textStyle: { color: '#64748b', fontSize: 11 } },
      series: [{
        type: 'pie',
        radius: ['52%', '74%'],
        center: ['50%', '44%'],
        label: { show: false },
        itemStyle: { borderRadius: 6, borderColor: '#fff', borderWidth: 2 },
        data: [
          { name: '新增', value: fresh, itemStyle: { color: '#22a35c' } },
          { name: '重复', value: dup, itemStyle: { color: '#b9c6d6' } },
        ],
      }],
    })
  }
  const bySource = new Map<string, number>()
  for (const n of r.items) bySource.set(n.source_name, (bySource.get(n.source_name) || 0) + 1)
  const rows = [...bySource.entries()].sort((a, b) => b[1] - a[1])
  if (barEl.value) {
    barIns?.dispose()
    barIns = echarts.init(barEl.value)
    barIns.setOption({
      tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
      grid: { left: 8, right: 24, top: 10, bottom: 0, containLabel: true },
      xAxis: { type: 'value', minInterval: 1, splitLine: { lineStyle: { color: '#eef2f7' } } },
      yAxis: {
        type: 'category',
        data: rows.map(([k]) => k),
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { color: '#47586b', fontSize: 11 },
      },
      series: [{
        type: 'bar',
        data: rows.map(([, v]) => v),
        barWidth: 14,
        itemStyle: { borderRadius: [0, 7, 7, 0], color: '#2a5d8f' },
        label: { show: true, position: 'right', color: '#1f4e79', fontSize: 11, fontWeight: 600 },
      }],
    })
  }
}

function onResize() {
  donutIns?.resize()
  barIns?.resize()
  trendIns?.resize()
}

watch(result, () => nextTick(renderCharts))

onMounted(() => {
  window.addEventListener('resize', onResize)
  refreshMeta()
})

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  donutIns?.dispose()
  barIns?.dispose()
  trendIns?.dispose()
})

async function refreshMeta() {
  loading.meta = true
  try {
    health.value = await api.health()
    backendDown.value = false
    tasks.value = await api.tasks()
    files.value = await api.files()
    trendData.value = await api.historyDaily()
    nextTick(renderTrend)
  } catch (e) {
    backendDown.value = true
  } finally {
    loading.meta = false
  }
}

async function doParse() {
  error.value = ''
  intent.value = null
  if (!query.value.trim()) {
    error.value = '先输入一句话需求'
    return
  }
  loading.parse = true
  try {
    intent.value = await api.parse(query.value)
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.parse = false
  }
}

async function doRun() {
  error.value = ''
  result.value = null
  if (!query.value.trim()) {
    error.value = '先输入一句话需求'
    return
  }
  loading.run = true
  try {
    result.value = await api.run(query.value, writeDoc.value, schedule.value)
    intent.value = result.value.intent
    await refreshMeta()
  } catch (e) {
    error.value = (e as Error).message
  } finally {
    loading.run = false
  }
}

async function deleteTask(id: string) {
  error.value = ''
  try {
    await api.removeTask(id)
    tasks.value = await api.tasks()
    if (health.value) health.value = await api.health()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function clearFingerprints() {
  if (!window.confirm('确认清空指纹库？清空后同一批条目会再次计为新增（演示"首次全量/二次增量"用）。')) return
  error.value = ''
  try {
    await api.clearFingerprints()
    if (health.value) health.value = await api.health()
  } catch (e) {
    error.value = (e as Error).message
  }
}

async function testPush() {
  error.value = ''
  pushMsg.value = ''
  loading.push = true
  try {
    const res = await api.testPush()
    pushOk.value = res.errors.length === 0
    const ok = res.sent.length ? `已发送：${res.sent.join('、')}` : '无渠道发送'
    pushMsg.value = res.errors.length ? `${ok}；失败：${res.errors.join('；')}` : ok
  } catch (e) {
    pushOk.value = false
    pushMsg.value = (e as Error).message
  } finally {
    loading.push = false
  }
}

function fmtTime(v: string | number | null): string {
  if (v === null || v === undefined || v === '') return '—'
  const d = new Date(v)
  if (isNaN(d.getTime())) return String(v)
  return d.toLocaleString('zh-CN', { hour12: false })
}

function fmtSize(n: number): string {
  if (n < 1024) return n + ' B'
  if (n < 1048576) return (n / 1024).toFixed(1) + ' KB'
  return (n / 1048576).toFixed(1) + ' MB'
}

function docUrl(path: string | null): string {
  if (!path) return '#'
  const name = path.split('/').pop() || path
  return '/api/files/' + encodeURIComponent(name)
}
</script>

<template>
  <div class="page">
    <header class="masthead">
      <div class="masthead-inner">
        <div class="brand">
          <div class="logo">AI</div>
          <div>
            <h1>AI-Bid-Agent</h1>
            <p class="sub">招投标信息智能聚合 · 自然语言进，结构化 Word 出</p>
          </div>
        </div>
        <div class="status" v-if="health">
          <span class="pill" :class="health.mock ? 'pill-warn' : 'pill-ok'">
            <i class="dot"></i>{{ health.mock ? 'MOCK 演示数据' : '真实抓取' }}
          </span>
          <span v-if="health.demo_mode" class="pill pill-warn" title="写操作已禁用：推送自测/清指纹/删任务/定时注册"><i class="dot"></i>演示模式·只读</span>
          <span class="pill">解析器 {{ health.parser }}</span>
          <span class="pill">站点 {{ enabledSites }}/{{ health.sites.length }}</span>
          <span class="pill">指纹 {{ health.fingerprints }}</span>
          <span class="pill">定时 {{ health.scheduled_jobs }}</span>
          <span
            class="pill"
            :class="health.push ? (health.push.ready ? 'pill-ok' : health.push.enabled ? 'pill-warn' : 'pill-muted') : 'pill-muted'"
            :title="health.push?.username ? `发件 ${health.push.username}` : '未配置 SMTP 凭据'"
          >
            <i class="dot"></i>{{ health.push ? (health.push.ready ? '邮件就绪' : health.push.enabled ? '邮件缺凭据' : '邮件未启用') : '邮件未启用' }}
          </span>
          <template v-for="w in health.push_webhook || []" :key="w.kind">
            <span
              class="pill"
              :class="w.ready ? 'pill-ok' : w.enabled ? 'pill-warn' : 'pill-muted'"
              :title="w.ready ? `${w.label}webhook 已配置${w.signed ? '（含加签）' : '（未加签）'}` : `未配置 ${w.label} webhook 地址`"
            >
              <i class="dot"></i>{{ w.ready ? `${w.label}就绪` : w.enabled ? `${w.label}缺地址` : `${w.label}未启用` }}
            </span>
          </template>
          <button class="btn btn-dark btn-sm" :disabled="loading.push" @click="testPush">
            {{ loading.push ? '发送中…' : '推送自测' }}
          </button>
          <button class="btn btn-dark btn-sm" @click="clearFingerprints">清空指纹</button>
          <button class="btn btn-dark btn-sm" :disabled="loading.meta" @click="refreshMeta">刷新</button>
        </div>
        <div class="status" v-else>
          <span class="pill pill-warn"><i class="dot"></i>{{ backendDown ? '后端未连接，请确认服务已启动' : '连接中…' }}</span>
        </div>
      </div>
      <div class="flowstrip">
        <span>自然语言</span><i>→</i><span>意图解析</span><i>→</i><span>多源抓取</span><i>→</i>
        <span>清洗去重</span><i>→</i><span>Word 文档</span><i>→</i><span>定时推送</span>
      </div>
      <p v-if="pushMsg" class="push-toast" :class="pushOk ? 'push-ok' : 'push-err'">{{ pushMsg }}</p>
    </header>

    <section class="card card-input">
      <h2>一句话需求</h2>
      <textarea
        v-model="query"
        rows="2"
        placeholder="例如：最近3个月的上海充电桩招标信息都有哪些，请汇总后每天9:00发送给我"
      ></textarea>
      <div class="chips">
        <button v-for="ex in EXAMPLES" :key="ex" class="chip" @click="query = ex">{{ ex }}</button>
      </div>
      <div class="actions">
        <label class="opt"><input type="checkbox" v-model="writeDoc" />生成 Word</label>
        <label class="opt"><input type="checkbox" v-model="schedule" />允许注册定时任务</label>
        <span class="spacer"></span>
        <button class="btn" :disabled="loading.parse" @click="doParse">
          <span v-if="loading.parse" class="spinner"></span>
          {{ loading.parse ? '解析中…' : '仅解析意图' }}
        </button>
        <button class="btn btn-primary" :disabled="loading.run" @click="doRun">
          <span v-if="loading.run" class="spinner spinner-light"></span>
          {{ loading.run ? '执行中…' : '执行全链路' }}
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <section class="card">
      <h2>运行趋势</h2>
      <p class="muted-xs" style="margin: 2px 0 8px">近 14 天每轮流水线的运行次数与新增条目（按天聚合，缺数日期补零）</p>
      <div ref="trendEl" class="chart chart-trend"></div>
    </section>

    <div class="duo" :class="{ 'duo-both': intent && result }">
      <section class="card" v-if="intent">
        <h2>意图解析</h2>
        <dl class="kv">
          <div><dt>关键词</dt><dd class="kw">{{ intent.keyword || '—' }}</dd></div>
          <div><dt>区域</dt><dd>{{ intent.region || '不限' }}</dd></div>
          <div>
            <dt>时间范围</dt>
            <dd>{{ intent.time_range_text || '—' }}<br /><span class="muted-xs">{{ intent.start_date }} ~ {{ intent.end_date }}</span></dd>
          </div>
          <div>
            <dt>定时</dt>
            <dd>
              <template v-if="intent.cron">
                {{ intent.frequency_text }}<br /><code>{{ intent.cron }}</code>
              </template>
              <span v-else class="muted-xs">未要求定时</span>
            </dd>
          </div>
          <div><dt>解析器</dt><dd><span class="pill-parser">{{ intent.parser }}</span></dd></div>
          <div>
            <dt>指定站点</dt>
            <dd>{{ intent.sites.length ? intent.sites.join('、') : '全部启用站点' }}</dd>
          </div>
        </dl>
      </section>

      <section class="card" v-if="result">
        <h2>执行结果</h2>
        <div class="kpis">
          <div class="kpi"><b>{{ result.fetched }}</b><span>抓取</span></div>
          <div class="kpi kpi-good"><b>{{ result.fresh }}</b><span>新增</span></div>
          <div class="kpi kpi-muted"><b>{{ result.deduplicated }}</b><span>去重</span></div>
          <div class="kpi kpi-good"><b>{{ result.detail_fetched ?? 0 }}</b><span>正文已补</span></div>
          <div class="kpi kpi-accent"><b>{{ result.items.length }}</b><span>本轮条目</span></div>
        </div>

        <div class="charts" v-if="result.items.length">
          <div class="chart-box">
            <h3>新增占比</h3>
            <div ref="donutEl" class="chart"></div>
          </div>
          <div class="chart-box">
            <h3>来源站点分布</h3>
            <div ref="barEl" class="chart"></div>
          </div>
        </div>

        <div v-if="result.summary" class="summary-box">
          <div class="summary-head">
            <h3>重点速览</h3>
            <span class="pill2 pill2-accent">模型摘要 · {{ result.summary.model }}</span>
          </div>
          <p v-if="result.summary.overview" class="summary-overview">{{ result.summary.overview }}</p>
          <ol class="summary-picks">
            <li v-for="p in result.summary.picks" :key="p.index">
              <a :href="p.url" target="_blank" rel="noopener">{{ p.title }}</a>
              <p class="summary-why"><span>为什么值得看</span>{{ p.why }}</p>
              <p class="summary-risk"><span>风险提示</span>{{ p.risk }}</p>
            </li>
          </ol>
          <p class="summary-note">
            本卡由模型基于标题与正文节选生成，仅供参考；事实与原文以下方条目为准<template
              v-if="result.summary.truncated"
            >（仅分析前 {{ result.summary.items_considered }} 条 / 共 {{ result.summary.items_total }} 条）</template
            >。
          </p>
        </div>

        <p v-if="result.scheduled_job" class="note">
          已注册定时任务 <code>{{ result.scheduled_job }}</code>（可在下方任务表删除）
        </p>
        <p v-if="result.doc_path" class="note">
          文档已生成 <code>{{ result.doc_path }}</code>
          <a class="btn btn-sm" :href="docUrl(result.doc_path)" target="_blank">下载 Word</a>
        </p>
        <p v-if="result.pushed_to?.length" class="note">
          已推送至 <code>{{ result.pushed_to.join('、') }}</code>（邮件含 Word 附件，群机器人为条目标题速览）
        </p>
        <ul v-if="result.notes?.length" class="notes">
          <li v-for="n in result.notes" :key="n">{{ n }}</li>
        </ul>
        <ul v-if="result.warnings.length" class="warnings">
          <li v-for="w in result.warnings" :key="w">{{ w }}</li>
        </ul>

        <table v-if="result.items.length">
          <thead>
            <tr><th>标题</th><th>来源</th><th>发布日期</th><th>状态</th></tr>
          </thead>
          <tbody>
            <tr v-for="n in result.items" :key="n.fingerprint">
              <td>
                <a :href="n.url" target="_blank" rel="noopener">{{ n.title }}</a>
                <details>
                  <summary>摘要</summary>
                  <p class="content">{{ n.content }}</p>
                </details>
              </td>
              <td>{{ n.source_name }}</td>
              <td>{{ n.publish_date || '—' }}</td>
              <td>
                <span class="pill2" :class="n.is_new ? 'pill2-ok' : 'pill2-muted'">{{ n.is_new ? '新增' : '重复' }}</span>
              </td>
            </tr>
          </tbody>
        </table>
        <div v-else class="empty">
          <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
            <circle cx="24" cy="24" r="20" fill="none" stroke="currentColor" stroke-width="2.5" stroke-dasharray="4 6" />
            <path d="M17 24l5 5 10-11" fill="none" stroke="currentColor" stroke-width="3" stroke-linecap="round" stroke-linejoin="round" />
          </svg>
          <b>本轮无新增条目</b>
          <span>增量推送语义：见过的不再推，也不产空文档。</span>
        </div>
      </section>
    </div>

    <section class="card">
      <h2>定时任务</h2>
      <table v-if="tasks.length">
        <thead>
          <tr><th>任务ID</th><th>查询原话</th><th>下次执行</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="j in tasks" :key="j.id">
            <td><code>{{ j.id }}</code></td>
            <td class="wrap">{{ j.query }}</td>
            <td>{{ fmtTime(j.next_run) }}</td>
            <td><button class="btn btn-danger btn-sm" @click="deleteTask(j.id)">删除</button></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">
        <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
          <circle cx="24" cy="26" r="16" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path d="M24 17v9l6 4" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
          <path d="M18 8l-4 4m20-4l4 4" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
        </svg>
        <b>暂无定时任务</b>
        <span>原话里带上"每天9:00"这类频率就会自动注册。</span>
      </div>
    </section>

    <section class="card">
      <h2>已生成文档</h2>
      <table v-if="files.length">
        <thead>
          <tr><th>文件名</th><th>大小</th><th>生成时间</th><th></th></tr>
        </thead>
        <tbody>
          <tr v-for="f in files" :key="f.name">
            <td class="wrap">{{ f.name }}</td>
            <td>{{ fmtSize(f.size) }}</td>
            <td>{{ fmtTime(f.modified * 1000) }}</td>
            <td><a class="btn btn-sm" :href="f.url" target="_blank">下载</a></td>
          </tr>
        </tbody>
      </table>
      <div v-else class="empty">
        <svg viewBox="0 0 48 48" width="40" height="40" aria-hidden="true">
          <path d="M14 6h14l8 8v28a2 2 0 01-2 2H14a2 2 0 01-2-2V8a2 2 0 012-2z" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path d="M28 6v8h8" fill="none" stroke="currentColor" stroke-width="2.5" />
          <path d="M19 26h10M19 32h10" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" />
        </svg>
        <b>还没有文档</b>
        <span>执行一次全链路就有了。</span>
      </div>
    </section>

    <footer class="foot">AI-Bid-Agent v2 · Vue 3 ＋ ECharts · 设计与决策见 docs/</footer>
  </div>
</template>
