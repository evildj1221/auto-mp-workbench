import { useEffect, useMemo, useRef, useState } from 'react'
import { keepPreviousData, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, SlotTabs, TaskPanel, useAction, useDate, usePageTask, useSlot } from '../../components/shared'

interface ExportCtx {
  date: string; version: string; html: string; slot: string
  report: { engine: string; theme: { name: string }; checks: { name: string; ok: boolean; detail?: string }[]; summary: { total: number; passed: number; all_ok: boolean }; word_count: number; inline_images?: { count: number } | null } | null
  md_exists: Record<string, boolean>
  pub: Record<string, any>
  push_log: Record<string, any> | null
  slots_meta: { id: string; selected_title?: string; status?: string }[]
  feishu_sync: boolean
}

/** 导出页：槽位独立导出 · 手机预览（与真实推送一致） · 草稿箱推送 · 自检清单。 */
export default function ExportPage() {
  const [date] = useDate()
  const [slot, setSlot] = useSlot()
  const qc = useQueryClient()
  const coverRef = useRef<HTMLInputElement>(null)
  const [pushMsg, setPushMsg] = useState('')

  const { data, refetch } = useQuery({
    queryKey: ['export', date, slot],
    queryFn: () => api.get<ExportCtx>(`/api/export/${date}?slot=${slot}`),
    staleTime: 15000,
    placeholderData: keepPreviousData,
  })

  const { tid, setTid, task, busy } = usePageTask('export')
  // 渲染任务进行中时自动刷新导出结果
  const running = task?.data?.status === 'running'
  useEffect(() => {
    if (tid && !running) refetch()
  }, [tid, running, refetch])

  // withCover=false → 无配图渲染（纯文字排版，头图/文中图都不加）
  const runExport = useAction(
    (v: { version: 'v1' | 'v2'; withCover?: boolean }) =>
      api.post<{ task_id: string }>('/api/export/run', { slot, version: v.version, with_cover: v.withCover ?? true })
        .then(r => { setTid(r.task_id); return r }),
    ['export'])
  const pushAll = useAction(
    () => api.post<{ ok_count: number; total: number }>('/api/export/push-all'),
    ['export'])
  const planReset = useAction(
    () => api.post<{ removed: number }>(`/api/export/plan-reset?slot=${slot}`).then(() => {
      runExport.mutate({ version: (data?.version || 'v1') as 'v1' })
    }),
    ['export'])

  const push = useAction(async () => {
    setPushMsg('推送中…')
    const fd = new FormData()
    fd.set('date', date)
    fd.set('slot', slot)
    const f = coverRef.current?.files?.[0]
    if (f) fd.set('cover', f)
    const res = await fetch('/api/export/push', {
      method: 'POST', body: fd,
      // 推送含封面图上传微信端，放宽超时；仍兜底防永久 pending
      signal: AbortSignal.timeout(60000),
    })
    const body = await res.json()
    if (!body.ok) {
      setPushMsg(`失败：${body.error?.message || '未知错误'}`)
      return
    }
    setPushMsg(`${body.data.action}：${body.data.title}（封面：${body.data.cover_note}）`)
    qc.invalidateQueries({ queryKey: ['export'] })
  }, ['export'])

  const checks = data?.report?.checks || []
  const fails = useMemo(() => checks.filter(c => !c.ok), [checks])

  return (
    <div className="p-6">
      <PageHeader title="导出 · 规范版排版" subtitle="所见即所得 · 写出去前的最后一眼"
        right={
          <div className="flex items-center gap-2">
            <SlotTabs slots={data?.slots_meta || []} value={slot || data?.slot || ''} onChange={setSlot} />
            <button className="btn" onClick={() => pushAll.mutate()}>批量写入（全部文稿）</button>
          </div>
        } />

      <TaskPanel task={task.data} />

      <div className="grid grid-cols-[1fr_420px] gap-5 items-start">
        <div className="flex flex-col gap-4">
          <GlassCard title="生成导出" className="!mb-0"
            extra={data?.report &&
              <span className="flex items-center gap-2">
                {!!data.report.inline_images &&
                  <span className="pill pill-ok">文中配图 {data.report.inline_images.count} 张</span>}
                <span className="pill pill-ok">已生成 · {data.report.engine} 引擎 · {data.report.theme?.name}</span>
              </span>}>
            <div className="flex items-center gap-2 flex-wrap">
              <select className="select" style={{ width: 110 }} value={data?.version || 'v1'}
                      onChange={e => runExport.mutate({ version: e.target.value as 'v1' | 'v2' })}>
                <option value="v1">v1 文稿</option><option value="v2">v2 文稿</option>
              </select>
              <button className="btn btn-gold" disabled={busy || runExport.isPending}
                      onClick={() => runExport.mutate({ version: (data?.version || 'v1') as 'v1' })}>
                {runExport.isPending ? '启动中…' : busy ? '渲染中…' : '重新渲染'}
              </button>
              <button className="btn" disabled={busy || runExport.isPending}
                      title="不加头图/文中图，直接出纯文字排版"
                      onClick={() => runExport.mutate({ version: (data?.version || 'v1') as 'v1', withCover: false })}>
                无配图渲染
              </button>
              {!!data?.report?.inline_images &&
                <button className="btn" disabled={busy} onClick={() => planReset.mutate()}>换一批配图</button>}
              {task.data && task.data.status !== 'running' &&
                <button className="btn btn-sm" onClick={() => refetch()}>刷新结果</button>}
            </div>
            <p className="muted mt-2">
              全内联样式 · section 容器 · 规范色板与 13 档字号。粘贴后核对三件事：信件块底色、竖线、金句深蓝加粗。
            </p>
          </GlassCard>

          <GlassCard title="推送到草稿箱" className="!mb-0"
            extra={<span className="muted">重写 · 无需手动复制粘贴</span>}>
            <div className="flex items-center gap-2 flex-wrap">
              <input ref={coverRef} type="file" accept="image/*" className="input" style={{ width: 220 }} />
              <button className="btn btn-primary" onClick={() => push.mutate()}
                      disabled={!data?.html || push.isPending}>
                写入草稿箱{slot ? `（${slot.toUpperCase()}）` : ''}
              </button>
              <button className="btn" onClick={() => window.open(`/api/export/${date}/raw?slot=${slot}`, '_blank')}>
                原始 HTML
              </button>
            </div>
            {pushMsg && <p className="ink2 text-[13px] mt-2">{pushMsg}</p>}
            <p className="muted mt-2">
              封面优先级：生成头图（自动换正文 URL）→ 本地上传 → 素材库第一张；可重复推送，微信侧自动删除旧草稿（草稿箱只留最新一篇）。
            </p>
            {data?.push_log && (
              <div className="mt-3 p-2.5 rounded-xl bg-white/45 text-[12.5px]">
                <b>最近批量推送</b> {data.push_log.pushed_at}：成功 {data.push_log.ok_count}/{data.push_log.total}
                {(data.push_log.results || []).map((r: any, i: number) => (
                  <div key={i} className="ink2">{r.slot} · {r.status}{r.media_id ? ` · media_id ${r.media_id}` : ''}{r.reason ? ` · ${r.reason}` : ''}</div>
                ))}
              </div>
            )}
          </GlassCard>

          <GlassCard title="自检清单"
            extra={data?.report &&
              <span className={`pill ${data.report.summary.all_ok ? 'pill-ok' : 'pill-wait'}`}>
                {data.report.summary.passed}/{data.report.summary.total} 项通过 · 字数 {data.report.word_count}
              </span>}>
            {!data?.report && <p className="muted">尚未导出——点上方「重新渲染」生成规范版排版。</p>}
            <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
              {checks.map(c => (
                <div key={c.name} className="flex items-center gap-2 text-[13px]">
                  <span className={`lamp ${c.ok ? 'ok' : 'err'}`} />
                  <span className={c.ok ? 'ink2' : ''} style={{ color: c.ok ? undefined : 'var(--c-err)' }}>{c.name}</span>
                  {c.detail && <span className="muted">{c.detail}</span>}
                </div>
              ))}
            </div>
            {fails.length > 0 && <p className="muted mt-2">未通过项仅供参考（LLM 渲染稿存在存量稿回退），红线类不通过会被推送环节拦截。</p>}
          </GlassCard>
        </div>

        {/* 手机预览 */}
        <GlassCard title="手机预览" extra={<span className="muted">与真实推送一致</span>}
                   className="sticky top-6">
          <div className="phone-shell mx-auto">
            <div className="phone-screen">
              {data?.html
                ? <iframe title="mp-preview" sandbox="" srcDoc={data.html} className="w-full h-full border-0" />
                : <div className="h-full flex items-center justify-center muted p-6 text-center">导出后在此预览真实排版效果</div>}
            </div>
          </div>
        </GlassCard>
      </div>
    </div>
  )
}
