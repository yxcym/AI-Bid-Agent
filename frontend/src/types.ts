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

export interface PipelineResult {
  query: string
  intent: ParsedIntent
  fetched: number
  fresh: number
  deduplicated: number
  doc_path: string | null
  scheduled_job: string | null
  items: Notice[]
  warnings: string[]
}

export interface SiteInfo {
  code: string
  name: string
  enabled: boolean
  needs_login: boolean
}

export interface Health {
  status: string
  mock: boolean
  parser: string
  sites: SiteInfo[]
  fingerprints: number
  scheduled_jobs: number
  output_dir: string
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
