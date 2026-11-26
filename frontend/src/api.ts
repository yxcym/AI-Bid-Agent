/** 薄 API 层：统一解包 {success, message, data}，非 2xx 或 success=false 一律抛错。 */

import type { FileItem, Health, Job, ParsedIntent, PipelineResult } from './types'

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, init)
  let body: { success?: boolean; message?: string; data?: T } | null = null
  try {
    body = await resp.json()
  } catch {
    // 非 JSON 响应（如网关错误页），落到下面的统一报错
  }
  if (!resp.ok || !body?.success) {
    throw new Error(body?.message || `请求失败（HTTP ${resp.status}）`)
  }
  return body.data as T
}

function post<T>(url: string, payload: unknown): Promise<T> {
  return request<T>(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
}

export const api = {
  health: () => request<Health>('/api/health'),

  parse: (query: string) => post<ParsedIntent>('/api/intent/parse', { query }),

  run: (query: string, writeDoc: boolean, schedule: boolean) =>
    post<PipelineResult>('/api/pipeline/run', { query, write_doc: writeDoc, schedule }),

  tasks: () => request<Job[]>('/api/tasks'),

  removeTask: (id: string) => request<null>(`/api/tasks/${encodeURIComponent(id)}`, { method: 'DELETE' }),

  files: () => request<FileItem[]>('/api/files'),

  clearFingerprints: () => post<{ removed: number }>('/api/fingerprints/clear', {}),
}
