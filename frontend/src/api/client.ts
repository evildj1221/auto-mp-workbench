// 类型化 API client：统一信封解包 + 错误抛出（供 TanStack Query 使用）
export class ApiError extends Error {
  category: string
  hint: string
  constructor(category: string, message: string, hint = '') {
    super(message)
    this.category = category
    this.hint = hint
  }
}

const DEFAULT_TIMEOUT_MS = 15000

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
    // 统一超时：后端被 Agent 任务拖慢时请求不能永久 pending
    //（无超时的 fetch 是「点了没反应、像死机」的直接来源）。
    // 调用方自带 signal 时不覆盖（长任务自行放宽）。
    signal: init?.signal ?? AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
  })
  let body: any = null
  try { body = await res.json() } catch { /* 非 JSON（如 text/html raw）按原样 */ }
  if (body && typeof body === 'object' && 'ok' in body) {
    if (!body.ok) {
      const e = body.error || {}
      throw new ApiError(e.category || 'unknown', e.message || '请求失败', e.hint || '')
    }
    return body.data as T
  }
  return (body ?? res) as T
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', body: body === undefined ? undefined : JSON.stringify(body) }),
}

// ===== 常用类型（与 backend/app/schemas、ctx 字段对应） =====
export interface AgentState {
  date: string; status: string; message?: string
  steps_used: number; cost_cny: number; goal: string
  pending_approval?: { type?: string; top5?: { rank: number; topic: string; total: number }[]; question?: string } | null
  running: boolean
}
export interface TraceEvent { ts: string; node: string; tool?: string; status?: string; message?: string }
export interface AgentSnapshot {
  date: string; state: AgentState; meta: { label: string; tone: string; hint: string }
  node_meta: Record<string, string>; trace: TraceEvent[]
  last_error?: { category?: string; message?: string } | null
  err_meta?: Record<string, { label: string; hint: string }>
  llm_ready: boolean; agent_ready: boolean; default_goal: string
  autonomy: 'assisted' | 'semi' | 'auto'
}

// ===== 中控台对话（console · 猫咪编辑部） =====
export interface ChatCard {
  type: 'confirm' | 'top5' | 'titles' | 'draft' | 'exported' | 'info'
  action?: string; label?: string; persona?: string
  channel?: 'agent'
  round?: number; top5?: { rank: number; topic: string; total: number }[]
  titles?: string[]; selected?: string
  slot?: string; words?: number; text?: string
  next?: { label: string; send: string }
  checklist?: [string, string][]
}
export interface ChatMsg {
  id: string; ts: string
  role: 'user' | 'agent' | 'system'
  persona?: string; text: string
  mode?: 'chat' | 'act'; act_offer?: boolean
  cards?: ChatCard[]; status?: string
}
export interface ChatAction {
  status: 'offer' | 'running'
  action: string; persona?: string; label?: string
  tid?: string; msg_id?: string; slot?: string
}
export interface ChatHistoryResp {
  date: string; messages: ChatMsg[]; action: ChatAction | null
}
export interface PersonaInfo {
  id: string; name: string; role: string; color: string; duty: string; busy: boolean
}
export interface ChatBrief {
  date: string; weekday: string
  time_ctx: { festivals: string[]; solar_terms: string[]; weekday_note: string
              season_scene: string; month_position: string }
  steps: { name: string; done: boolean }[]; done_count: number
  agent: { status: string; running: boolean; steps_used: number; cost_cny: number }
  cost_today: number; streak: number; total_published: number
}
