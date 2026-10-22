import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, DateBar, GlassCard, PageHeader, useDate } from '../../components/shared'

interface Artifact { folder: string; name: string; size: number; rel: string }

/** 档案库：按日期回看全部产物（candidates/selections/drafts/feedback）。 */
export default function ArchivePage() {
  const [date, setDate] = useDate()
  const [datesList, setDatesList] = useState<string[]>([])
  const [picked, setPicked] = useState<Artifact | null>(null)
  const [view, setView] = useState<{ rel: string; content: string; is_html: boolean } | null>(null)

  // 日期索引
  useQuery({
    queryKey: ['archive-dates'],
    queryFn: async () => {
      const d = await api.get<{ dates: string[] }>('/api/archive')
      setDatesList(d.dates)
      if (!date && d.dates.length) setDate(d.dates[0])
      return d
    },
  })

  const { data } = useQuery({
    queryKey: ['archive', date],
    queryFn: () => api.get<{ artifacts: Artifact[]; pub: Record<string, any> }>(`/api/archive/${date}`),
    enabled: !!date,
  })

  const open = async (a: Artifact) => {
    setPicked(a)
    setView(null)
    const r = await api.get<{ content?: string; is_html?: boolean }>(
      `/api/archive/file?path=${encodeURIComponent(a.rel)}`)
    setView({ rel: a.rel, content: r.content || '', is_html: !!r.is_html })
  }

  return (
    <div className="p-6">
      <PageHeader title="档案库" subtitle="每一篇稿子、每一次决策的完整留痕"
        right={<DateBar date={date} onChange={setDate} />} />

      <div className="flex gap-1.5 flex-wrap mb-4">
        {datesList.slice(0, 20).map(d => (
          <button key={d} className={`btn btn-sm ${d === date ? 'btn-primary' : ''}`}
                  onClick={() => setDate(d)}>{d}</button>
        ))}
      </div>

      <div className="grid grid-cols-[340px_1fr] gap-5 items-start">
        <GlassCard title="产物清单" className="!mb-0">
          {!data?.artifacts?.length && <p className="muted">该日期暂无产物。</p>}
          <div className="grid gap-1">
            {(data?.artifacts || []).map(a => (
              <button key={a.rel}
                className={`text-left px-3 py-2 rounded-xl text-[13px] transition-all hover:bg-white/50 ${picked?.rel === a.rel ? 'bg-white/60 font-medium' : ''}`}
                onClick={() => open(a)}>
                <span className="pill mr-2" style={{ fontSize: 10.5 }}>{a.folder}</span>
                {a.name}
                <span className="muted ml-2">{(a.size / 1024).toFixed(1)} KB</span>
              </button>
            ))}
          </div>
        </GlassCard>

        <GlassCard title={view ? view.rel : '查看器'} className="!mb-0 min-h-[400px]">
          {!view && <p className="muted">左侧选择一个产物查看内容。</p>}
          {view?.is_html
            ? <iframe title="archive-html" sandbox="" srcDoc={view.content}
                      className="w-full rounded-xl bg-white" style={{ height: 620, border: '1px solid var(--glass-border)' }} />
            : <pre className="text-[12.5px] whitespace-pre-wrap ink2 max-h-[620px] overflow-auto">{view?.content}</pre>}
        </GlassCard>
      </div>
    </div>
  )
}
