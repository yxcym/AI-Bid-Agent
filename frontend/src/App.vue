<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { api } from './api'
import type { FileItem, Health, Job, ParsedIntent, PipelineResult } from './types'

const EXAMPLES = [
  '最近3个月的上海充电桩招标信息都有哪些，请汇总后每天9:00发送给我',
  '最近1周的合肥服务器采购公告',
  '上个月北京的无人机采购招标信息',
]

const health = ref<Health | null>(null)
const backendDown = ref(false)
const query = ref('')
const intent = ref<ParsedIntent | null>(null)
const result = ref<PipelineResult | null>(null)
const tasks = ref<Job[]>([])
const files = ref<FileItem[]>([])
const writeDoc = ref(true)
const schedule = ref(true)
const loading = reactive({ parse: false, run: false, meta: false })
const error = ref('')

const enabledSites = computed(() => health.value?.sites.filter((s) => s.enabled).length ?? 0)

onMounted(refreshMeta)

async function refreshMeta() {
  loading.meta = true
  try {
    health.value = await api.health()
    backendDown.value = false
    tasks.value = await api.tasks()
    files.value = await api.files()
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
</script>

<template>
  <div class="page">
    <header class="topbar">
      <div>
        <h1>AI-Bid-Agent</h1>
        <p class="sub">招投标信息智能聚合 · 自然语言进，结构化 Word 出</p>
      </div>
      <div class="health" v-if="health">
        <span class="pill" :class="health.mock ? 'pill-warn' : 'pill-ok'">
          {{ health.mock ? 'MOCK 演示数据' : '真实抓取' }}
        </span>
        <span class="pill">解析器 {{ health.parser }}</span>
        <span class="pill">启用站点 {{ enabledSites }}/{{ health.sites.length }}</span>
        <span class="pill">指纹 {{ health.fingerprints }}</span>
        <span class="pill">定时任务 {{ health.scheduled_jobs }}</span>
        <button class="btn btn-ghost btn-sm" @click="clearFingerprints">清空指纹</button>
        <button class="btn btn-ghost btn-sm" :disabled="loading.meta" @click="refreshMeta">刷新</button>
      </div>
      <div class="health" v-else>
        <span class="pill pill-warn">{{ backendDown ? '后端未连接，请先运行 run.ps1' : '加载中…' }}</span>
      </div>
    </header>

    <section class="card">
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
          {{ loading.parse ? '解析中…' : '仅解析意图' }}
        </button>
        <button class="btn btn-primary" :disabled="loading.run" @click="doRun">
          {{ loading.run ? '执行中…' : '执行全链路' }}
        </button>
      </div>
      <p v-if="error" class="error">{{ error }}</p>
    </section>

    <section class="card" v-if="intent">
      <h2>意图解析</h2>
      <dl class="kv">
        <div><dt>关键词</dt><dd>{{ intent.keyword || '—' }}</dd></div>
        <div><dt>区域</dt><dd>{{ intent.region || '不限' }}</dd></div>
        <div>
          <dt>时间范围</dt>
          <dd>{{ intent.time_range_text || '—' }}（{{ intent.start_date }} ~ {{ intent.end_date }}）</dd>
        </div>
        <div>
          <dt>定时</dt>
          <dd>{{ intent.cron ? intent.frequency_text + ' → cron ' + intent.cron : '未要求定时' }}</dd>
        </div>
        <div><dt>解析器</dt><dd>{{ intent.parser }}</dd></div>
        <div>
          <dt>指定站点</dt>
          <dd>{{ intent.sites.length ? intent.sites.join('、') : '全部启用站点' }}</dd>
        </div>
      </dl>
    </section>

    <section class="card" v-if="result">
      <h2>执行结果</h2>
      <div class="stats">
        <div class="stat"><b>{{ result.fetched }}</b><span>抓取</span></div>
        <div class="stat"><b class="good">{{ result.fresh }}</b><span>新增</span></div>
        <div class="stat"><b class="muted">{{ result.deduplicated }}</b><span>去重</span></div>
        <div class="stat"><b>{{ result.items.length }}</b><span>本轮条目</span></div>
      </div>
      <p v-if="result.scheduled_job" class="note">
        已注册定时任务 <code>{{ result.scheduled_job }}</code>（可在下方任务表删除）
      </p>
      <p v-if="result.doc_path" class="note">文档已生成 <code>{{ result.doc_path }}</code></p>
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
              <span class="pill" :class="n.is_new ? 'pill-ok' : 'pill-muted'">{{ n.is_new ? '新增' : '重复' }}</span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-else class="note">本轮无新增条目——增量推送语义：见过的不再推，也不产空文档。</p>
    </section>

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
      <p v-else class="note">暂无定时任务——原话里带上“每天9:00”这类频率就会自动注册。</p>
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
      <p v-else class="note">还没有文档——执行一次全链路就有了。</p>
    </section>

    <footer class="foot">v1 骨架版 · 前后端同源单进程 · 设计与决策见 docs/</footer>
  </div>
</template>
