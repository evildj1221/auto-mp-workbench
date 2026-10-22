import { useState, type ReactElement } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api, GlassCard, useAction, useSlot } from '../shared'
import type { AgentSnapshot, ChatBrief } from '../../api/client'

/** 中控台 DIY 组件库：每个组件独立取数、独立轮询——组件被删掉再加回来时
 *  重新挂载即拉取最新数据（数据源在服务端持续更新，组件只是视图）。
 *  「概要/运行日志」双 tab 已拆为独立组件，由用户自由编排。 */

export type WidgetId = 'time' | 'pipeline' | 'preview' | 'stats' | 'runtime'
  | 'trace' | 'trends' | 'quotes' | 'links'

export const WIDGET_DEFS: { id: WidgetId; label: string; defH: number; desc: string }[] = [
  { id: 'time', label: '今日时节', defH: 150, desc: '节日/节气/季节语境' },
  { id: 'pipeline', label: '流水线', defH: 210, desc: '10 步进度' },
  { id: 'preview', label: '实机预览', defH: 480, desc: '与真实推送一致的成品预览' },
  { id: 'stats', label: '台务', defH: 130, desc: '连续发布/成本/Agent 一行' },
  { id: 'runtime', label: 'Agent 状态与审批', defH: 230, desc: '运行卡/选题·发布关卡/提额' },
  { id: 'trace', label: '思考与执行时间线', defH: 280, desc: 'trace 实时日志' },
  { id: 'trends', label: '趋势三图', defH: 300, desc: '成本/质量/发布节奏' },
  { id: 'quotes', label: '金句库', defH: 220, desc: '风格参考，可入库' },
  { id: 'links', label: '工作间', defH: 110, desc: '各模块快捷入口' },
]

// 共享数据源：brief（30s 轮询，多个组件订阅同一查询自动合并请求）
function useBrief(date: string) {
  return useQuery({
    queryKey: ['chat-brief', date],
    queryFn: () => api.get<ChatBrief>(`/api/chat/brief?date=${date}`),
    refetchInterval: 30000,
  })
}

function useAgentSnap(date: string) {
  return useQuery({
    queryKey: ['agent', date],
    queryFn: () => api.get<AgentSnapshot>(`/api/agent/${date}`),
    // 运行/等待关卡时 4s 快轮询，空闲 30s 慢轮询——组件存在即持续保鲜
    refetchInterval: q => {
      const d = q.state.data
      return (d?.state?.running || String(d?.state?.status || '').startsWith('waiting'))
        ? 4000 : 30000
    },
  })
}

// ===== 各组件 =====

export function TimeWidget({ date }: { date: string }) {
  const { data: brief } = useBrief(date)
  const tc = brief?.time_ctx
  return (
    <>
      <div className="flex flex-wrap gap-1.5 mb-2">
        {(tc?.festivals || []).map(f => <span key={f} className="pill pill-wait">{f}</span>)}
        {(tc?.solar_terms || []).map(f => <span key={f} className="pill">{f}</span>)}
        {tc?.month_position && <span className="pill">{tc.month_position}</span>}
      </div>
      <p className="ink2 text-[13px]">
        {tc?.weekday_note}{tc?.season_scene ? ` · ${tc.season_scene}` : ''}
      </p>
    </>
  )
}

export function PipelineWidget({ date }: { date: string }) {
  const { data: brief } = useBrief(date)
  return (
    <>
      <div className="h-2 rounded-full bg-white/50 overflow-hidden mb-2.5">
        <div className="h-full rounded-full transition-all"
             style={{ width: `${(brief?.done_count || 0) * 10}%`,
                      background: 'linear-gradient(90deg,#4a6fbf,#4caf88)' }} />
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-1">
        {(brief?.steps || []).map(s => (
          <div key={s.name} className="flex items-center gap-1.5 text-[12px] min-w-0">
            <span className={`lamp ${s.done ? 'ok' : ''} flex-none`} />
            <span className={`truncate ${s.done ? 'ink2' : 'muted'}`}>{s.name}</span>
          </div>
        ))}
      </div>
    </>
  )
}

export function PreviewWidget({ date }: { date: string }) {
  const [slot] = useSlot()
  const { data: brief } = useBrief(date)
  const ready = brief?.agent?.status === 'waiting_publish' || brief?.agent?.status === 'done'
  const { data } = useQuery({
    queryKey: ['export', date, slot],
    queryFn: () => api.get<{ html: string }>(`/api/export/${date}${slot ? `?slot=${slot}` : ''}`),
    enabled: !!ready,
    refetchInterval: 30000,
  })
  if (!ready || !data?.html)
    return <p className="muted text-[13px]">终稿导出后在此预览（Agent 到发布关卡或完工时自动出现）。</p>
  return (
    <div className="phone-shell mx-auto" style={{ width: 250, height: 420, padding: 7 }}>
      <div className="phone-screen">
        <iframe title="preview" sandbox="" srcDoc={data.html} className="w-full h-full border-0" />
      </div>
    </div>
  )
}

export function StatsWidget({ date }: { date: string }) {
  const { data: brief } = useBrief(date)
  return (
    <ul className="text-[13px] ink2" style={{ lineHeight: 2 }}>
      <li>连续发布 {brief?.streak || 0} 天 · 累计 {brief?.total_published || 0} 篇</li>
      <li>今日成本 <b>¥{Number(brief?.cost_today || 0).toFixed(2)}</b></li>
      <li>Agent：{brief?.agent?.running ? '运行中' : (brief?.agent?.status || '未运行')}</li>
    </ul>
  )
}

export function RuntimeWidget({ date }: { date: string }) {
  const { data: snap } = useAgentSnap(date)
  const qc = useQueryClient()
  const after = () => {
    qc.invalidateQueries({ queryKey: ['agent', date] })
    window.dispatchEvent(new Event('wb-chat-refresh'))  // 通知对话流立即拉新
  }
  const start = useAction(() => api.post('/api/agent/start', { date }).then(after), [])
  const approve = useAction(
    (v: { approved: boolean; choice?: number }) =>
      api.post('/api/agent/approve', { date, ...v }).then(after), [])
  const budget = useAction(
    () => api.post('/api/agent/budget', { date, extra_steps: 30, extra_cost: 2 }).then(after), [])

  const st = snap?.state
  return (
    <>
      {snap && !snap.agent_ready && <p className="muted">Agent 依赖未安装（langgraph）</p>}
      <p className="ink2 text-[13px]">{snap?.meta?.hint || '今天还没让编辑部开工。'}</p>
      <div className="flex items-center gap-2 mt-2 text-[12.5px] muted">
        <span>{snap?.meta?.label}</span>
        <span>· {st?.steps_used || 0} 步 · ¥{Number(st?.cost_cny || 0).toFixed(2)}</span>
      </div>
      <div className="flex gap-2 mt-2.5 flex-wrap">
        {!st?.running && !['waiting_topic', 'waiting_publish'].includes(st?.status || '') && (
          <button className="btn btn-primary btn-sm" onClick={() => start.mutate()}>
            {['paused_budget', 'interrupted'].includes(st?.status || '') ? '从断点继续' : '一键运行'}
          </button>)}
        {st?.status === 'paused_budget' && (
          <button className="btn btn-gold btn-sm" onClick={() => budget.mutate()}>提额 +30 步 / ¥2</button>)}
      </div>
      {st?.status === 'waiting_topic' && st.pending_approval?.top5 && (
        <div className="grid gap-1.5 mt-2.5">
          {st.pending_approval.top5.map(t => (
            <button key={t.rank} className="btn btn-sm justify-between text-left"
                    onClick={() => approve.mutate({ approved: true, choice: t.rank })}>
              <span>#{t.rank} {t.topic}</span>
              <span className="pill">综合 {t.total}</span>
            </button>
          ))}
        </div>
      )}
      {st?.status === 'waiting_publish' && (
        <div className="flex gap-2 mt-2.5">
          <button className="btn btn-primary btn-sm" onClick={() => approve.mutate({ approved: true })}>批准推送</button>
          <button className="btn btn-danger btn-sm" onClick={() => approve.mutate({ approved: false })}>暂不批准</button>
        </div>
      )}
    </>
  )
}

export function TraceWidget({ date }: { date: string }) {
  const { data: snap } = useAgentSnap(date)
  return (
    <>
      {(snap?.trace || []).map((e, i) => (
        <div className="tl-item" key={`${e.ts || ''}-${e.node}-${i}`}>
          <div className="flex items-baseline gap-2">
            <span className="muted">{(e.ts || '').slice(11, 19)}</span>
            <span className="font-medium text-[13px]">{snap?.node_meta[e.node] || e.node}</span>
            {e.tool && <span className="pill">{e.tool}</span>}
            {e.status && <span className={`pill ${e.status === 'error' ? 'pill-err' : ''}`}>{e.status}</span>}
          </div>
          {e.message && <p className="ink2 text-[12px] mt-0.5">{e.message}</p>}
        </div>
      ))}
      {!(snap?.trace || []).length && <p className="muted">还没有运行记录。</p>}
    </>
  )
}

export function TrendsWidget({ date }: { date: string }) {
  const { data } = useQuery({
    queryKey: ['dashboard', date],
    queryFn: () => api.get<{ trend: Record<string, string> }>(`/api/dashboard/${date}`),
    staleTime: 10 * 60 * 1000,
    refetchInterval: 10 * 60 * 1000,
  })
  const t = data?.trend
  if (!t)
    return <p className="muted text-[13px]">加载趋势数据…</p>
  return (
    <div className="flex flex-col gap-2">
      {(['cost_svg', 'quality_svg', 'publish_svg'] as const).map(k => (
        <div key={k} className="[&_svg]:w-full rounded-xl bg-white/40 p-2"
             dangerouslySetInnerHTML={{ __html: t[k] || '' }} />
      ))}
    </div>
  )
}

export function QuotesWidget({ date }: { date: string }) {
  const [text, setText] = useState('')
  const { data, refetch } = useQuery({
    queryKey: ['quotes', date],
    queryFn: () => api.get<{ quotes: { text: string }[] }>(`/api/drafts/${date}`),
    refetchInterval: 60000,
  })
  const add = useAction(
    (t: string) => api.post('/api/drafts/quotes', { date, text: t })
      .then(() => { setText(''); refetch() }),
    [])
  return (
    <>
      <div className="flex gap-2 mb-2">
        <input className="input" placeholder="手动入库一条金句" value={text}
               onChange={e => setText(e.target.value)} />
        <button className="btn btn-sm" disabled={!text.trim()}
                onClick={() => add.mutate(text.trim())}>入库</button>
      </div>
      {(data?.quotes || []).slice(-12).reverse().map((q, i) => (
        <p key={i} className="ink2 text-[13px] mb-1.5"
           style={{ borderLeft: '3px solid var(--accent)', paddingLeft: 8 }}>{q.text}</p>
      ))}
      {!data?.quotes?.length && <p className="muted">金句库还是空的。</p>}
    </>
  )
}

export function LinksWidget({ date }: { date: string }) {
  const qs = date ? `?date=${date}` : ''
  return (
    <div className="flex flex-wrap gap-2">
      {[['/topics', '选题中心'], ['/write', '写稿'], ['/export', '导出'],
        ['/typography', '排版'], ['/review', '复盘'], ['/archive', '档案'],
        ['/settings', '设置']].map(([to, label]) => (
        <Link key={to} to={to + qs} className="btn btn-sm">{label}</Link>
      ))}
    </div>
  )
}

export const WIDGET_COMPONENTS: Record<WidgetId, (p: { date: string }) => ReactElement> = {
  time: TimeWidget, pipeline: PipelineWidget, preview: PreviewWidget,
  stats: StatsWidget, runtime: RuntimeWidget, trace: TraceWidget,
  trends: TrendsWidget, quotes: QuotesWidget, links: LinksWidget,
}
