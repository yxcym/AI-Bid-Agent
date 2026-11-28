/** 后端接口的数据形状，与 app/models.py 的 to_dict() 输出一一对应。 */

export interface ParsedIntent {
  raw_query: string
  keyword: string
  region: string | null
  time_range_text: string | null
  start_date: string | null
  end_date: string | null
  cron: string | null
  frequency_text: string | null
  sites: string[]
  parser: string
}

export interface Notice {
  source_code: string
  source_name: string
  title: string
  url: string
  publish_date: string
  content: string
  fingerprint: string
  is_new: boolean
}

export interface SummaryPick {
  index: number
  title: string
  url: string
  source_name: string
  publish_date: string
  why: string
  risk: string
}

/** 模型摘要：未启用/失败/无新增时为 null */
export interface SummaryResult {
  overview: string
  picks: SummaryPick[]
  model: string
  items_considered: number
  items_total: number
  truncated: boolean
}

export interface PipelineResult {
  query: string
  intent: ParsedIntent
  fetched: number
  fresh: number
  deduplicated: number
  detail_fetched: number
  doc_path: string | null
  scheduled_job: string | null
  items: Notice[]
  warnings: string[]
  notes: string[]
  pushed_to?: string[]
  summary?: SummaryResult | null
}

export interface SiteInfo {
  code: string
  name: string
  enabled: boolean
  needs_login: boolean
}

export interface PushStatus {
  enabled: boolean
  ready: boolean
  username: string
  recipients: number
}

/** 群机器人通道状态（飞书 / 钉钉），后端 push_webhook 数组元素 */
export interface WebhookStatus {
  kind: 'feishu' | 'dingtalk'
  label: string
  enabled: boolean
  ready: boolean
  signed: boolean
}

export interface Health {
  status: string
  mock: boolean
  demo_mode: boolean
  parser: string
  sites: SiteInfo[]
  fingerprints: number
  scheduled_jobs: number
  output_dir: string
  push?: PushStatus
  push_webhook?: WebhookStatus[]
}

export interface Job {
  id: string
  query: string
  next_run: string | null
}

export interface FileItem {
  name: string
  size: number
  modified: number
  url: string
}

/** 按日聚合的运行统计（缺数日期由后端补零） */
export interface HistoryDaily {
  date: string
  runs: number
  fetched: number
  fresh: number
  deduplicated: number
  docs: number
}
