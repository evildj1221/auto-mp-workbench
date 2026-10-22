import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, useAction } from '../../components/shared'

interface SettingsCtx {
  env_status: Record<string, any>
  redlines: Record<string, { level: string; phrases: string[] }>
  effective: Record<string, number>
  levels: string[]
  external_redlines: boolean
  default_w: Record<string, number>
  profile: Record<string, any>
  spec_exists: boolean
  feishu_sync: boolean
  cost_summary: { n: number; today: number; total: number; tokens: number; ok: number; fail: number; by_module: [string, { n: number; cost: number }][] }
  redline_log: Record<string, any>[]
  workbench: { autonomy: 'assisted' | 'semi' | 'auto'; max_steps: number; max_cost_cny: number
               wechat_ip_whitelist?: string }
}

interface IpInfo {
  ip: string; ip_error: string
  whitelist: string[]; configured: boolean; in_whitelist: boolean
}

const AUTONOMY_META = {
  assisted: { label: '陪伴模式', hint: '选题与发布都需要你确认（最稳）' },
  semi: { label: '半自动', hint: '自动选最高分选题，发布前仍需你批准' },
  auto: { label: '全自动', hint: '选题与推送全自动（质检全过才放行），最终群发仍在公众号后台手动完成' },
} as const

/** 系统设置：Agent 自动化档位 · 环境 · 成本 · 红线 · 飞书开关 · 出口 IP 白名单。 */
export default function SettingsPage() {
  const { data, refetch } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<SettingsCtx>('/api/settings'),
  })
  const [checkText, setCheckText] = useState('')
  const [checkResult, setCheckResult] = useState('')
  const [wlDraft, setWlDraft] = useState<string | null>(null)  // null=未编辑，显示已存值

  const ipq = useQuery({
    queryKey: ['ip-info'],
    queryFn: () => api.get<IpInfo>('/api/settings/ip-info'),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: true,
  })

  const saveWb = useAction(
    (patch: Record<string, unknown>) => api.put('/api/settings', patch).then(() => refetch()),
    ['settings'])
  const toggleFeishu = useAction(
    (v: boolean) => api.post('/api/settings/feishu-toggle', { feishu_sync: v }).then(() => refetch()),
    ['settings'])
  const resetRedlines = useAction(() => api.post('/api/settings/redlines/reset').then(() => refetch()), ['settings'])
  const doCheck = useAction(
    (mode: 'phrase' | 'semantic') =>
      api.post<{ result: any }>('/api/settings/redline-check', { text: checkText, mode })
        .then(r => { setCheckResult(`${r.result.risk_level} · 命中 ${r.result.hits?.length || 0} 条`); return r }),
    [])

  const cost = data?.cost_summary

  return (
    <div className="p-6">
      <PageHeader title="系统设置" subtitle="Agent 档位 · 环境 · 成本 · 红线与归档" />

      <div className="grid grid-cols-2 gap-4 items-start">
        <GlassCard title="公众号出口 IP 与白名单" className="!mb-0 col-span-2"
          extra={<button className="btn btn-sm" onClick={() => ipq.refetch()}>
            {ipq.isFetching ? '查询中…' : '刷新 IP'}
          </button>}>
          <div className="flex items-center gap-3 flex-wrap mb-2">
            <span className="text-[15px] font-bold" style={{ fontVariantNumeric: 'tabular-nums' }}>
              {ipq.isFetching && !ipq.data ? '查询中…' : (ipq.data?.ip || ipq.data?.ip_error || '—')}
            </span>
            {ipq.data?.ip && !ipq.data.configured &&
              <span className="pill pill-wait">白名单未配置</span>}
            {ipq.data?.ip && ipq.data.configured && ipq.data.in_whitelist &&
              <span className="pill pill-ok">✓ 在白名单内，可以推送</span>}
            {ipq.data?.ip && ipq.data.configured && !ipq.data.in_whitelist &&
              <span className="pill pill-err">不在白名单——去公众号后台加这个 IP</span>}
            <button className="btn btn-sm" disabled={!ipq.data?.ip}
                    onClick={() => navigator.clipboard?.writeText(ipq.data!.ip)}>
              复制 IP
            </button>
          </div>
          <p className="muted mb-2">
            宿舍网络重启后公网 IP 常变，导致草稿箱推送报 40164（IP 不在白名单）。把常用的几个
            IP 填在下面（逗号或换行分隔，与公众号后台「安全中心 → IP 白名单」保持一致），打开
            本页即可一眼看出当前 IP 是否要更新。
          </p>
          <div className="flex gap-2">
            <textarea className="textarea flex-1" rows={2}
                      placeholder="如 112.10.3.4, 112.10.3.9"
                      value={wlDraft ?? (data?.workbench.wechat_ip_whitelist || '')}
                      onChange={e => setWlDraft(e.target.value)} />
            <div className="flex flex-col gap-2 justify-center">
              <button className="btn btn-primary btn-sm" disabled={wlDraft === null}
                      onClick={() => saveWb.mutate({ wechat_ip_whitelist: wlDraft },
                        { onSuccess: () => setWlDraft(null) })}>保存白名单</button>
              {ipq.data?.whitelist?.length ? <span className="muted text-[11.5px]">已存 {ipq.data.whitelist.length} 个</span> : null}
            </div>
          </div>
        </GlassCard>

        <GlassCard title="Agent 自动化档位" className="!mb-0"
          extra={<span className={`pill ${data?.workbench.autonomy === 'auto' ? 'pill-ok' : 'pill-wait'}`}>
            {AUTONOMY_META[(data?.workbench.autonomy || '') as keyof typeof AUTONOMY_META in AUTONOMY_META ? (data!.workbench.autonomy as keyof typeof AUTONOMY_META) : 'assisted'].label}
          </span>}>
          <div className="grid gap-2">
            {(Object.keys(AUTONOMY_META) as (keyof typeof AUTONOMY_META)[]).map(k => (
              <button key={k}
                className={`btn justify-between text-left ${data?.workbench.autonomy === k ? 'btn-primary' : ''}`}
                onClick={() => saveWb.mutate({ autonomy: k })}>
                <span>
                  {AUTONOMY_META[k].label}
                  <span className="muted ml-2">{AUTONOMY_META[k].hint}</span>
                </span>
                {data?.workbench.autonomy === k && <span className="pill pill-ok">当前</span>}
              </button>
            ))}
          </div>
          <div className="flex items-center gap-3 mt-3 text-[13px] ink2">
            <label className="flex items-center gap-1.5" title="标准全流程（选题→写稿→标题→头图→导出）最少 6 步，建议 ≥8；下限 8">
              步数上限 <input type="number" className="input" style={{ width: 80 }}
                defaultValue={data?.workbench.max_steps}
                onBlur={e => saveWb.mutate({ max_steps: Number(e.target.value) })} />
            </label>
            <label className="flex items-center gap-1.5">
              成本上限 ¥ <input type="number" step="0.5" className="input" style={{ width: 90 }}
                defaultValue={data?.workbench.max_cost_cny}
                onBlur={e => saveWb.mutate({ max_cost_cny: Number(e.target.value) })} />
            </label>
          </div>
        </GlassCard>

        <GlassCard title="成本仪表盘" className="!mb-0" extra={<span className="muted">llm_cost.jsonl</span>}>
          <div className="grid grid-cols-3 gap-3 text-center">
            <div><div className="text-[22px] font-bold" style={{ color: 'var(--accent)' }}>¥{(cost?.today || 0).toFixed(2)}</div><div className="muted">今日</div></div>
            <div><div className="text-[22px] font-bold" style={{ color: 'var(--accent)' }}>¥{(cost?.total || 0).toFixed(2)}</div><div className="muted">累计</div></div>
            <div><div className="text-[22px] font-bold" style={{ color: 'var(--accent)' }}>{cost?.n || 0}</div><div className="muted">调用 {cost?.fail || 0} 失败</div></div>
          </div>
          <div className="mt-3">
            {(cost?.by_module || []).slice(0, 6).map(([m, v]) => (
              <div key={m} className="flex items-center gap-2 text-[12.5px] mb-1">
                <span className="pill" style={{ minWidth: 110 }}>{m}</span>
                <div className="flex-1 h-2 rounded-full bg-white/50 overflow-hidden">
                  <div className="h-full rounded-full" style={{ width: `${Math.min(100, v.cost / (cost?.total || 1) * 100)}%`, background: 'linear-gradient(90deg,#4a6fbf,#e0a03c)' }} />
                </div>
                <span className="muted">¥{v.cost.toFixed(3)}</span>
              </div>
            ))}
          </div>
        </GlassCard>

        <GlassCard title="环境状态" className="!mb-0">
          <ul className="text-[13.5px]" style={{ lineHeight: 2.1 }}>
            <EnvRow ok={data?.env_status?.LLM_API_KEY} label="LLM Key" detail={data?.env_status?.LLM_MODEL} />
            <EnvRow ok={data?.env_status?.IMAGE_API_KEY} label="生图 Key" detail={data?.env_status?.IMAGE_PROVIDER || '未配置'} />
            <EnvRow ok={data?.env_status?.FEISHU_DOC_FOLDER_TOKEN} label="飞书归档" detail={data?.feishu_sync ? '自动归档开' : '自动归档关'} />
            <EnvRow ok={data?.spec_exists} label="排版视觉规范" detail="排版视觉规范.md" />
          </ul>
          <div className="flex items-center gap-2 mt-2">
            <button className={`btn btn-sm ${data?.feishu_sync ? '' : 'btn-primary'}`}
                    onClick={() => toggleFeishu.mutate(!data?.feishu_sync)}>
              飞书自动归档：{data?.feishu_sync ? '开' : '关'}
            </button>
          </div>
        </GlassCard>

        <GlassCard title="红线质检" className="!mb-0"
          extra={<button className="btn btn-sm" onClick={() => resetRedlines.mutate()}>恢复默认规则</button>}>
          <textarea className="textarea" rows={3} placeholder="粘贴一段文字，检测红线命中（短语/语义）"
                    value={checkText} onChange={e => setCheckText(e.target.value)} />
          <div className="flex items-center gap-2 mt-2">
            <button className="btn btn-sm" onClick={() => doCheck.mutate('phrase')}>短语检测</button>
            <button className="btn btn-sm" onClick={() => doCheck.mutate('semantic')}>语义检测（LLM）</button>
            {checkResult && <span className={`pill ${checkResult.startsWith('safe') ? 'pill-ok' : 'pill-err'}`}>{checkResult}</span>}
          </div>
          <div className="mt-3 max-h-44 overflow-auto">
            {(data?.redline_log || []).map((e, i) => (
              <p key={i} className="text-[12px] ink2 mb-1">
                <span className="muted">{(e.ts || '').slice(5, 19)}</span> · {e.step} · <b style={{ color: 'var(--c-err)' }}>{e.risk_level}</b> {e.hit_counts ? JSON.stringify(e.hit_counts) : ''}
              </p>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {Object.entries(data?.redlines || {}).slice(0, 8).map(([id, r]) => (
              <span key={id} className={`pill ${r.level === 'critical' ? 'pill-err' : r.level === 'high' ? 'pill-wait' : ''}`}>
                {id} · {r.level}
              </span>
            ))}
          </div>
        </GlassCard>

        <GlassCard title="评分权重（生效中）" className="!mb-0 col-span-2">
          <div className="grid grid-cols-5 gap-3 text-center">
            {Object.entries(data?.effective || {}).map(([k, v]) => (
              <div key={k}>
                <div className="text-[20px] font-bold" style={{ color: 'var(--accent)' }}>{v}</div>
                <div className="muted">{k}{data?.default_w?.[k] !== v ? `（默认 ${data?.default_w?.[k]}）` : ''}</div>
              </div>
            ))}
          </div>
          {data?.profile?.ai_notes && <p className="muted mt-2">{data.profile.ai_notes}</p>}
        </GlassCard>
      </div>
    </div>
  )
}

function EnvRow({ ok, label, detail }: { ok?: boolean; label: string; detail?: string }) {
  return (
    <li className="flex items-center gap-2">
      <span className={`lamp ${ok ? 'ok' : 'err'}`} />
      <b>{label}</b>
      <span className="muted">{ok ? '已配置' : '未配置'}{detail ? ` · ${detail}` : ''}</span>
    </li>
  )
}
