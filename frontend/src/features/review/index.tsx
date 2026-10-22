import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, useAction, useDate } from '../../components/shared'

interface ReviewCtx {
  date: string
  stats: Record<string, any>[]
  total_reads: number; total_shares: number; total_likes: number; avg_reads: number
  by_series: Record<string, { n: number; reads: number; shares: number; avg_reads: number }>
  health_ok: boolean; health_msg: string
  entries: { rank: number; score?: number; feedback?: string }[]
  analysis: Record<string, any> | null
  fb_stats: Record<string, any>[]
}

/** 数据复盘：真实发布数据回流（手工 / 微信 API）× 评分权重校准 × 评论画像。 */
export default function ReviewPage() {
  const [date] = useDate()
  const { data, refetch } = useQuery({
    queryKey: ['review', date],
    queryFn: () => api.get<ReviewCtx>(`/api/review/${date}`),
  })
  const [form, setForm] = useState({ reads: 0, likes: 0, shares: 0, fans_delta: 0, note: '' })
  const [msg, setMsg] = useState('')

  const save = useAction(
    () => api.post('/api/review/save', form).then(() => { setMsg('已录入'); refetch() }),
    ['review'])
  const fetchApi = useAction(
    () => api.post('/api/review/fetch').then(() => { setMsg('已从微信 API 拉取'); refetch() }),
    ['review'])
  const calibrate = useAction(
    () => api.post<{ message: string }>('/api/review/calibrate').then(r => { setMsg(r.message); refetch() }),
    ['review'])

  return (
    <div className="p-6">
      <PageHeader title="数据复盘" subtitle={`${date} · 发布数据回流，驱动选题评分进化`}
        right={<span className={`pill ${data?.health_ok ? 'pill-ok' : 'pill-err'}`}>
          微信 API：{data?.health_ok ? '正常' : data?.health_msg || '未接通'}
        </span>} />

      {msg && <GlassCard className="mb-4"><p className="ink2 text-[13px]">{msg}</p></GlassCard>}

      <div className="grid grid-cols-4 gap-4 mb-4">
        <Stat label="累计阅读" v={data?.total_reads || 0} />
        <Stat label="累计在看" v={data?.total_likes || 0} />
        <Stat label="累计分享" v={data?.total_shares || 0} />
        <Stat label="篇均阅读" v={data?.avg_reads || 0} />
      </div>

      <div className="grid grid-cols-[1fr_380px] gap-5 items-start">
        <GlassCard title="发布记录">
          {!data?.stats?.length && <p className="muted">暂无数据——发布 3 天后回填，或在微信后台开好接口权限后自动拉取。</p>}
          <table className="w-full text-[13px]" style={{ borderCollapse: 'collapse' }}>
            <thead><tr className="muted text-left">
              <th className="py-1.5">日期</th><th>标题</th><th>阅读</th><th>在看</th><th>分享</th><th>粉丝变动</th><th>来源</th>
            </tr></thead>
            <tbody>
              {(data?.stats || []).map((s, i) => (
                <tr key={i} className="border-t" style={{ borderColor: 'rgba(138,152,173,.18)' }}>
                  <td className="py-1.5 ink2">{s.date}</td>
                  <td className="ink2">{(s.title || '').slice(0, 18)}</td>
                  <td>{s.reads}</td><td>{s.likes}</td><td>{s.shares}</td>
                  <td>{s.fans_delta >= 0 ? `+${s.fans_delta}` : s.fans_delta}</td>
                  <td className="muted">{s.source}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </GlassCard>

        <div className="flex flex-col gap-4">
          <GlassCard title="录入 / 拉取">
            <div className="grid grid-cols-2 gap-2">
              {(['reads', 'likes', 'shares', 'fans_delta'] as const).map(k => (
                <label key={k} className="text-[12.5px] muted">
                  {{ reads: '阅读', likes: '在看', shares: '分享', fans_delta: '粉丝变动' }[k]}
                  <input type="number" className="input mt-0.5" value={form[k]}
                         onChange={e => setForm(f => ({ ...f, [k]: Number(e.target.value) }))} />
                </label>
              ))}
            </div>
            <input className="input mt-2" placeholder="备注（可选）" value={form.note}
                   onChange={e => setForm(f => ({ ...f, note: e.target.value }))} />
            <div className="flex gap-2 mt-3">
              <button className="btn btn-primary" onClick={() => save.mutate()}>录入</button>
              <button className="btn" onClick={() => fetchApi.mutate()}>微信 API 拉取</button>
              <button className="btn btn-gold" onClick={() => calibrate.mutate()}>校准评分权重</button>
            </div>
          </GlassCard>

          <GlassCard title="系列表现">
            {Object.entries(data?.by_series || {}).map(([k, v]) => (
              <div key={k} className="flex items-center justify-between text-[13px] mb-1">
                <span className="ink2">系列 {k}</span>
                <span className="muted">{v.n} 篇 · 篇均 {v.avg_reads} 阅读</span>
              </div>
            ))}
          </GlassCard>

          <GlassCard title="评论与画像" extra={<span className="muted">只读</span>}>
            {!data?.entries?.length && <p className="muted">暂无用户评论。</p>}
            {(data?.entries || []).map((e, i) => (
              <p key={i} className="ink2 text-[13px] mb-1">#{e.rank} {e.score ? `评 ${e.score} 分 · ` : ''}{e.feedback || ''}</p>
            ))}
          </GlassCard>
        </div>
      </div>
    </div>
  )
}

function Stat({ label, v }: { label: string; v: number }) {
  return (
    <div className="glass card-pad">
      <div className="muted">{label}</div>
      <div className="text-[24px] font-bold" style={{ color: 'var(--accent)' }}>{v}</div>
    </div>
  )
}
