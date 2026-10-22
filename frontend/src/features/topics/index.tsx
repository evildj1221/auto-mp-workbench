import { useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, TaskPanel, useAction, useDate, usePageTask } from '../../components/shared'

interface Top5Item { rank: number; topic: string; total: number; avg_scores?: Record<string, number>; compliance_blocked?: boolean }

/** 风向命中 → 展示文本（兼容 对象{hits,total,rate}/数字/空 三种历史数据形态；
 *  直接渲染对象会触发 React #31 白屏崩溃——2026-09-27 选题页卡死事故的根因） */
function hitsText(dh: unknown): string {
  if (dh == null) return ""
  if (typeof dh === "number") return `风向命中 ${dh}`
  if (typeof dh === "object") {
    const o = dh as { hits?: number; total?: number; rate?: number }
    const rate = typeof o.rate === "number" ? ` · ${Math.round(o.rate * 100)}%` : ""
    return `风向命中 ${o.hits ?? 0}/${o.total ?? 0}${rate}`
  }
  return `风向命中 ${String(dh)}`
}

/** 选题中心：批次 Top5 / 风向卡 / 多批生成 / 收藏排除 / 快捷打分。 */
export default function TopicsPage() {
  const [date] = useDate()
  const [note, setNote] = useState('')
  const [directiveText, setDirectiveText] = useState('')
  const [directiveScope, setDirectiveScope] = useState('week')

  const { data, refetch } = useQuery({
    queryKey: ['topics', date],
    queryFn: () => api.get<TopicsCtx>(`/api/topics/${date}`),
    // 来回切页 15s 内直接用缓存（不重查重渲染）；换日期显示旧内容占位不白屏
    staleTime: 15000,
    placeholderData: keepPreviousData,
  })

  const { setTid, task, busy } = usePageTask('topics')

  // 就写这个：必须带真实批次号（曾硬编码 round:1 → 在 r2/r3 点选全被当成
  // r1 同题，去重返回旧槽位，w02 永远不建）
  const choose = useAction(
    (v: { rank: number; round: number }) =>
      api.post<{ task_id: string }>('/api/topics/choose', { date, choice: v.rank, round: v.round, note })
        .then(r => { setTid(r.task_id); return r }),
    ['topics'])
  const newBatch = useAction(
    () => api.post<{ task_id: string }>(`/api/topics/new-batch?date=${date}`).then(r => { setTid(r.task_id); return r }),
    ['topics'])
  const regenBatch = useAction(
    (round: number) => api.post<{ task_id: string }>(`/api/topics/regenerate-batch?round=${round}&date=${date}`)
      .then(r => { setTid(r.task_id); return r }),
    ['topics'])
  const addDirective = useAction(
    () => api.post('/api/topics/directives', { text: directiveText, scope: directiveScope }),
    ['topics'])
  const delDirective = useAction(
    (id: string) => api.post('/api/topics/directives/delete', { id }),
    ['topics'])
  const pref = useAction(
    (p: { rank: number; action: 'like' | 'exclude'; round: number }) => api.post('/api/topics/pref', { ...p, date }),
    ['topics'])
  const quickFb = useAction(
    (p: { rank: string; score: string }) => api.post('/api/topics/feedback', { ...p, date, note: '' }),
    ['topics'])

  return (
    <div className="p-6">
      <PageHeader title="选题中心" subtitle={`${date} · 多批候选共存，点选即建槽位`}
        right={<button className="btn btn-primary" disabled={busy || newBatch.isPending}
                       onClick={() => newBatch.mutate()}>
          {newBatch.isPending ? '启动中…' : busy ? '任务运行中…' : '生成新批次'}
        </button>} />

      <TaskPanel task={task.data} />

      {/* 风向标 */}
      <GlassCard title="选题风向" className="mb-4"
        extra={<div className="flex gap-2">
          <select className="select" style={{ width: 96 }} value={directiveScope} onChange={e => setDirectiveScope(e.target.value)}>
            <option value="today">今日</option><option value="week">本周</option><option value="long">长期</option>
          </select>
          <button className="btn btn-sm" onClick={() => { if (directiveText.trim()) { addDirective.mutate(); setDirectiveText('') } }}>保存风向</button>
        </div>}>
        <div className="flex gap-2 mb-3">
          <input className="input" placeholder="例：多写厨房与食物的细节；少用地铁意象"
                 value={directiveText} onChange={e => setDirectiveText(e.target.value)} />
        </div>
        <div className="flex gap-2 flex-wrap">
          {(data?.directives || []).map(d => (
            <span key={d.id} className={`pill ${d.negative ? 'pill-err' : 'pill-ok'}`}>
              {d.negative ? '避' : '循'} {d.text} · {d.scope}{d.hits_text ? ` · 命中${d.hits_text}` : ''}
              <button className="ml-1 opacity-60 hover:opacity-100" onClick={() => delDirective.mutate(d.id)}>×</button>
            </span>
          ))}
          {!data?.directives?.length && <span className="muted">暂无生效风向</span>}
        </div>
      </GlassCard>

      {/* 各批次 Top5 */}
      {(data?.batch_top5s || []).map(b => {
        const hitsLabel = b.directive_hits_text || hitsText(b.directive_hits)
        return (
        <GlassCard key={b.round} title={`批次 r${b.round} · Top5 候选`} className="mb-4"
          extra={hitsLabel ? <span className="pill pill-ok">{hitsLabel}</span> : null}>
          {!b.top5 && <p className="muted">该批次尚未评分</p>}
          <div className="grid gap-2">
            {(b.top5 || []).map((t: Top5Item) => (
              <div key={t.rank} className="flex items-center gap-3 p-2.5 rounded-xl hover:bg-white/50 transition-all">
                <span className="pill">{t.compliance_blocked ? '合规拦截' : `#${t.rank}`}</span>
                <span className="flex-1 text-[14px]">{t.topic}</span>
                {t.avg_scores &&
                  <span className="muted">{Object.entries(t.avg_scores).slice(0, 3).map(([k, v]) => `${k.slice(0, 4)} ${v}`).join(' · ')}</span>}
                <span className="pill pill-ok">综合 {t.total}</span>
                <button className="btn btn-sm" disabled={busy}
                        onClick={() => pref.mutate({ rank: t.rank, action: 'like', round: b.round })}>收藏</button>
                <button className="btn btn-sm" disabled={busy}
                        onClick={() => pref.mutate({ rank: t.rank, action: 'exclude', round: b.round })}>排除</button>
                <button className="btn btn-sm btn-gold" disabled={busy}
                        onClick={() => choose.mutate({ rank: t.rank, round: b.round })}>{busy ? '运行中…' : '就写这个'}</button>
              </div>
            ))}
          </div>
          {b.round > 0 && <div className="mt-2 text-right">
            <button className="btn btn-sm" disabled={busy || regenBatch.isPending}
                    onClick={() => regenBatch.mutate(b.round)}>
              {regenBatch.isPending ? '启动中…' : busy ? '任务运行中…' : '重新生成本批'}
            </button>
          </div>}
        </GlassCard>
        )
      })}

      {/* 已选与槽位 */}
      <GlassCard title="已选选题与文稿槽位">
        {data?.sel?.user_choice
          ? <p>今日已选：<b>{data.sel.topic}</b>（活动槽位 {data.sel.active_slot || '默认'}）</p>
          : <p className="muted">尚未选定选题</p>}
        <div className="flex gap-2 flex-wrap mt-2">
          {(data?.slots || []).map(s => (
            <span key={s.id} className="pill">{s.id} · {s.words} 字{s.status === 'published' ? ' · 已发布' : ''}</span>
          ))}
        </div>
      </GlassCard>
    </div>
  )
}

interface TopicsCtx {
  batch_top5s: { round: number; top5: Top5Item[] | null; file: string
                 directive_hits?: unknown; directive_hits_text?: string }[]
  slots: { id: string; words: number; status: string; has_draft: boolean }[]
  sel: { user_choice?: number; topic?: string; active_slot?: string }
  pending: Record<string, unknown>
  directives: { id: string; text: string; scope: string; negative: boolean
                days_left: number | null; hits?: unknown; hits_text?: string }[]
  low_hit_streak: number
}
