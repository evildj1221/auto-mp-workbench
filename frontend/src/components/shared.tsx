import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useQuery, useQueryClient, useMutation } from '@tanstack/react-query'
import { api } from '../api/client'
import type { ReactNode } from 'react'

/** 全站共用的日期/槽位状态：直接落在 URL（可分享、可后退），无全局 store。 */
export function useDate(): [string, (d: string) => void] {
  const [sp, setSp] = useSearchParams()
  const today = todayStr()
  const date = sp.get('date') || today
  return [date, d => {
    const next = new URLSearchParams(sp)
    next.set('date', d)
    setSp(next, { replace: true })
  }]
}

export function useSlot(): [string, (s: string) => void] {
  const [sp, setSp] = useSearchParams()
  return [sp.get('slot') || '', s => {
    const next = new URLSearchParams(sp)
    if (s) next.set('slot', s); else next.delete('slot')
    setSp(next, { replace: true })
  }]
}

export function todayStr() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

export function GlassCard({ title, extra, children, className = '' }: {
  title?: string; extra?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section className={`glass card-pad ${className}`}>
      {(title || extra) && (
        <div className="flex items-center justify-between mb-3">
          {title && <h3 className="section-title">{title}</h3>}
          {extra}
        </div>
      )}
      {children}
    </section>
  )
}

export function PageHeader({ title, subtitle, right }: { title: string; subtitle?: string; right?: ReactNode }) {
  return (
    <div className="flex items-end justify-between mb-5">
      <div>
        <h1 className="text-[22px] font-bold" style={{ color: 'var(--ink-1)' }}>{title}</h1>
        {subtitle && <p className="muted mt-0.5">{subtitle}</p>}
      </div>
      {right}
    </div>
  )
}

/** 槽位切换 tab（W01/W02/…，多文稿共存） */
export function SlotTabs({ slots, value, onChange }: {
  slots: { id: string; selected_title?: string; status?: string }[]
  value: string; onChange: (s: string) => void
}) {
  if (!slots.length) return null
  return (
    <div className="flex items-center gap-2 flex-wrap">
      {slots.map(s => (
        <button key={s.id}
          onClick={() => onChange(s.id)}
          className={`btn btn-sm ${value === s.id ? 'btn-primary' : ''}`}>
          {s.id.toUpperCase()}
          {s.status === 'published' && <span className="pill pill-ok ml-1">已发布</span>}
          {s.selected_title && <span className="muted" style={{ fontSize: 11 }}>{s.selected_title.slice(0, 14)}</span>}
        </button>
      ))}
    </div>
  )
}

/** 后台任务轮询：返回任务的实时状态与输出 */
export function useTask(tid: string | null) {
  return useQuery({
    queryKey: ['task', tid],
    queryFn: () => api.get<{ status: string; output: string; label: string }>(`/api/tasks/${tid}`),
    enabled: !!tid,
    refetchInterval: q => (q.state.data?.status === 'running' ? 2500 : false),
  })
}

/** 任务是否占线：已发起但还没结果（含运行中）——按钮禁用依据 */
export function taskBusy(task: { data?: { status: string } | undefined } | undefined, tid: string | null): boolean {
  if (!tid) return false
  return !task?.data || task.data.status === 'running'
}

// 跨页面任务句柄：路由切换卸载组件不丢 tid——去别的页转一圈回来，
// 运行中/刚完成的任务面板还在（旧版 tid 存组件 state，切页即失联）
const _pageTids: Record<string, string | null> = {}

export function usePageTask(key: string) {
  const [tid, setTidState] = useState<string | null>(_pageTids[key] ?? null)
  const setTid = useCallback((t: string | null) => {
    _pageTids[key] = t
    setTidState(t)
  }, [key])
  const task = useTask(tid)
  return { tid, setTid, task, busy: taskBusy(task, tid) }
}

/** 实时任务面板：点击后立刻出现，运行中每 2.5s 流式刷新输出——
 *  旧版只在任务完成后渲染（运行中的几分钟里页面毫无反馈，「不知道有没有在跑」）。 */
export function TaskPanel({ task }: { task?: { status: string; output: string; label: string } | null }) {
  if (!task) return null
  const running = task.status === 'running'
  const tone = running ? 'run' : task.status === 'done' ? 'ok' : 'err'
  const label = running ? '后台运行中 · 每 2.5 秒自动刷新'
    : task.status === 'done' ? '已完成' : '运行出错'
  return (
    <GlassCard className="mb-4">
      <div className="flex items-center gap-2 mb-2">
        <span className={`lamp ${tone}`} />
        <span className="text-[13px] font-medium">{label}</span>
        <span className="muted truncate">{task.label}</span>
      </div>
      <pre className="text-[12px] whitespace-pre-wrap ink2"
           style={{ maxHeight: 200, overflow: 'auto', margin: 0 }}>
        {(task.output || '正在启动，稍候…').slice(-900)}
      </pre>
    </GlassCard>
  )
}

/** 通用动作 mutation：完成后按 keyList 失效缓存；失败统一 toast（根治「点了没反应」） */
export function useAction<TVars = void, TData = unknown>(
  fn: (vars: TVars) => Promise<TData>, invalidates: string[] = []) {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: fn,
    onSuccess: () => invalidates.forEach(k => qc.invalidateQueries({ queryKey: [k] })),
    onError: e => toast(`操作失败：${e instanceof Error ? e.message : '网络异常'}`),
  })
}

// ===== 全局 toast（模块级总线，App 挂 <ToastHost/> 渲染） =====
let _toastPush: ((msg: string) => void) | null = null
export function toast(msg: string) {
  _toastPush?.(msg)
}

export function ToastHost() {
  const [items, setItems] = useState<{ id: number; msg: string }[]>([])
  useEffect(() => {
    _toastPush = (msg: string) => {
      const id = Date.now() + Math.random()
      setItems(v => [...v.slice(-3), { id, msg }])
      setTimeout(() => setItems(v => v.filter(x => x.id !== id)), 4200)
    }
    return () => { _toastPush = null }
  }, [])
  return (
    <div className="fixed left-1/2 bottom-6 z-50 flex flex-col gap-2 items-center pointer-events-none">
      {items.map(t => (
        <div key={t.id} className="toast-pill">{t.msg}</div>
      ))}
    </div>
  )
}

/** 日期选择器（最近 7 天 + 自定义） */
export function DateBar({ date, onChange }: { date: string; onChange: (d: string) => void }) {
  return (
    <input type="date" className="input" style={{ width: 170 }} value={date}
           max={todayStr()} onChange={e => onChange(e.target.value)} />
  )
}

// 全站统一从 shared 取 api（qs 死导出已移除：全前端无调用点）
export { api }
