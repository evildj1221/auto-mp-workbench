import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, useAction } from '../../components/shared'

interface TypoCtx {
  themes: { id: string; name: string }[]
  theme_id: string; active_id: string; has_overrides: boolean
  src: string; draft_date: string | null; series: string; series_label: string
  preview_html: string
  checks_passed: number; checks_total: number; check_fails: string[]
  component_groups: Record<string, { name: string; label: string; marker: boolean; description: string; html: string; enabled: boolean }[]>
  color_tokens: [string, string][]
  config_fields: { label: string; name: string; fields: [string, Record<string, any>][]; values: Record<string, any> }[]
  load_errors: unknown[]
}

/** 排版中心：主题画廊 · 微调面板 · 组件画廊 · 真实渲染管线预览。 */
export default function TypographyPage() {
  const [src, setSrc] = useState('sample')
  const { data, refetch } = useQuery({
    queryKey: ['typography', src],
    queryFn: () => api.get<TypoCtx>(`/api/typography?src=${src}`),
  })
  const themeId = data?.theme_id || ''
  const [overrideKey, setOverrideKey] = useState('')
  const [overrideValue, setOverrideValue] = useState('')

  const act = useAction((body: Record<string, unknown>) => api.post('/api/typography/activate', { theme_id: themeId, ...body }), ['typography'])
  const override = useAction(() => api.post('/api/typography/override', { theme_id: themeId, key: overrideKey, value: overrideValue }), ['typography'])
  const reset = useAction(() => api.post('/api/typography/reset', { theme_id: themeId }), ['typography'])
  const save = useAction((name: string) => api.post('/api/typography/save', { theme_id: themeId, name }), ['typography'])
  const del = useAction((tid: string) => api.post('/api/typography/delete', { theme_id: tid }), ['typography'])
  const [saveName, setSaveName] = useState('')

  return (
    <div className="p-6">
      <PageHeader title="排版中心" subtitle="主题系统 · 规范色板 · 组件画廊（真实渲染管线）"
        right={
          <div className="flex gap-2">
            <select className="select" style={{ width: 130 }} value={src} onChange={e => setSrc(e.target.value)}>
              <option value="sample">示例信笺</option><option value="draft">最新存量稿</option>
            </select>
            {data?.has_overrides && <button className="btn" onClick={() => reset.mutate()}>重置微调</button>}
          </div>
        } />

      <div className="grid grid-cols-[1fr_360px] gap-5 items-start">
        <GlassCard title="预览" className="!mb-0"
          extra={<span className={`pill ${data && data.checks_passed === data.checks_total ? 'pill-ok' : 'pill-wait'}`}>
            自检 {data?.checks_passed}/{data?.checks_total}
          </span>}>
          <div className="rounded-2xl bg-white p-5 overflow-auto" style={{ maxHeight: 720 }}
               dangerouslySetInnerHTML={{ __html: data?.preview_html || '' }} />
          {!!data?.check_fails?.length && (
            <p className="muted mt-2">未过：{data.check_fails.join('、')}</p>
          )}
        </GlassCard>

        <div className="flex flex-col gap-4">
          <GlassCard title="主题">
            <div className="grid gap-2">
              {(data?.themes || []).map(t => (
                <div key={t.id} className="flex items-center gap-2">
                  <button className={`btn btn-sm flex-1 justify-between ${t.id === themeId ? 'btn-primary' : ''}`}
                          onClick={() => act.mutate({ theme_id: t.id })}>
                    {t.name}
                    {t.id === data?.active_id && <span className="pill pill-ok">激活</span>}
                  </button>
                  {t.id !== data?.active_id &&
                    <button className="btn btn-sm btn-danger" onClick={() => del.mutate(t.id)}>删</button>}
                </div>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              <input className="input" placeholder="另存为用户主题…" value={saveName}
                     onChange={e => setSaveName(e.target.value)} />
              <button className="btn btn-sm" disabled={!saveName.trim()}
                      onClick={() => save.mutate(saveName.trim())}>另存</button>
            </div>
          </GlassCard>

          <GlassCard title="微调">
            <div className="flex gap-2 mb-2">
              <select className="select" style={{ width: 170 }} value={overrideKey}
                      onChange={e => setOverrideKey(e.target.value)}>
                <option value="">选择要改的字段…</option>
                {(data?.config_fields || []).flatMap(f =>
                  f.fields.map(([k, s]) => (
                    <option key={`${f.name}.${k}`} value={`${f.name}.${k}`}>
                      {f.label} · {s.label || k}
                    </option>
                  )))}
              </select>
              <button className="btn btn-sm" disabled={!overrideKey}
                      onClick={() => { override.mutate(); setOverrideValue('') }}>应用</button>
            </div>
            <textarea className="textarea" rows={3} placeholder="新值（列表类型按行填写）"
                      value={overrideValue} onChange={e => setOverrideValue(e.target.value)} />
            <div className="mt-3 flex flex-wrap gap-1.5">
              {(data?.color_tokens || []).map(([k, v]) => (
                <span key={k} className="pill" title={`${k}: ${v}`}>
                  <span className="inline-block w-3 h-3 rounded-full border border-white/60"
                        style={{ background: v }} /> {k}
                </span>
              ))}
            </div>
          </GlassCard>

          <GlassCard title="组件画廊">
            {(['block', 'scaffold', 'meta'] as const).map(kind => (
              <div key={kind} className="mb-2">
                <div className="muted mb-1">{kind}</div>
                <div className="flex flex-wrap gap-1.5">
                  {(data?.component_groups?.[kind] || []).map(c => (
                    <span key={c.name} className={`pill ${c.enabled ? 'pill-ok' : ''}`}
                          title={c.description}>{c.label}</span>
                  ))}
                </div>
              </div>
            ))}
          </GlassCard>
        </div>
      </div>
    </div>
  )
}
