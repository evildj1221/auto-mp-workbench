import { useEffect, useState } from 'react'
import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { api, GlassCard, PageHeader, SlotTabs, TaskPanel, useAction, useDate, usePageTask, useSlot } from '../../components/shared'
import { MaterialsPanel } from '../../components/Materials'

interface DraftsCtx {
  date: string; slot: string
  pub: Record<string, any>
  sel: Record<string, any>
  drafts: Record<'v1' | 'v2', { exists: boolean; words: number }>
  slots_meta: { id: string; selected_title?: string; status?: string }[]
  slot_meta: Record<string, any> | null
  outline_text: string
  quotes: { text: string }[]
  similar_sents: string[]
  html_v1: boolean; html_v2: boolean
  v2_angles: string[]
  mp_exists: boolean
  titles_data: { selected?: string; titles?: { title: string; hook_type?: string; reason?: string }[] } | null
  draft_text: string
  checklist: [string, string][]
}

/** 写稿与发布：编辑器 · 标题方案 · 一键出稿 · 审校与发布登记。 */
export default function WritePage() {
  const [date] = useDate()
  const [slot, setSlot] = useSlot()
  const [content, setContent] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saveMsg, setSaveMsg] = useState('')
  const { tid, setTid, task, busy } = usePageTask('writing')

  const { data, refetch } = useQuery({
    queryKey: ['drafts', date, slot],
    queryFn: () => api.get<DraftsCtx>(`/api/drafts/${date}?slot=${slot}`),
    staleTime: 8000,
    placeholderData: keepPreviousData,
  })
  // 切换日期/槽位时先清脏标记（否则下方守卫会挡住新槽位内容的显示）
  useEffect(() => { setDirty(false) }, [date, slot])
  // 有未保存编辑时，refetch 不得用服务端文本覆盖输入框——
  // 旧版任何触发 refetch 的动作都会把正在打字的内容重置（输入「卡死」感的来源）
  useEffect(() => {
    if (dirty) return
    setContent(data?.draft_text || '')
  }, [data?.draft_text, dirty])
  // 主动切走（切槽位）的丢稿守卫 + 关窗提醒：dirty 只防后台覆盖不防主动切，
  // 旧版切一下 W02 回来几百字修改就没了
  const guardDirty = () =>
    !dirty || window.confirm('编辑器里有未保存的修改，切换后将丢失。确定切换吗？')
  useEffect(() => {
    const h = (e: BeforeUnloadEvent) => {
      if (dirty) { e.preventDefault(); e.returnValue = '' }
    }
    window.addEventListener('beforeunload', h)
    return () => window.removeEventListener('beforeunload', h)
  }, [dirty])

  // 槽位为空且已有文稿槽位时自动选中第一个——素材上传/编辑器/标题方案与活动槽位保持一致
  useEffect(() => {
    if (!slot && data?.slots_meta?.length) setSlot(data.slots_meta[0].id)
  }, [data?.slots_meta, slot, setSlot])

  useEffect(() => { if (tid && task.data && task.data.status !== 'running') refetch() },
           [tid, task.data, refetch])

  const save = useAction(
    () => api.post<{ message: string }>('/api/drafts/edit', { date, slot, version: 'v1', content })
      .then(r => { setSaveMsg(r.message); setDirty(false); return r }),
    ['drafts'])
  const chooseTitle = useAction(
    (title: string) => api.post('/api/drafts/titles/choose', { date, slot, title }),
    ['drafts'])
  const reviewDone = useAction(() => api.post('/api/drafts/review-done', { date, slot }), ['drafts'])
  const register = useAction(
    (checklist: Record<string, boolean>) => api.post('/api/drafts/publish-register', { date, slot, checklist }),
    ['drafts'])
  const oneClick = useAction(
    () => api.post<{ task_id: string }>('/api/drafts/oneclick', { date, slot }).then(r => { setTid(r.task_id); return r }),
    ['drafts'])
  const runCmd = useAction(
    (vars: { cmd: string; extra?: Record<string, unknown> }) =>
      api.post<{ task_id: string }>('/api/drafts/run', { date, slot, ...vars.extra, cmd: vars.cmd })
        .then(r => { setTid(r.task_id); return r }),
    ['drafts'])
  const addQuote = useAction(
    (text: string) => api.post('/api/drafts/quotes', { date, slot, text }),
    ['drafts'])

  const [checkState, setCheckState] = useState<Record<string, boolean>>({})
  // 空清单 every 恒真曾让「登记已发布」在数据未加载时可点；切换重置防勾选残留带去另一槽位
  const allChecked = (data?.checklist || []).length > 0
    && (data!.checklist).every(([k]) => checkState[k])
  useEffect(() => { setCheckState({}) }, [date, slot])

  return (
    <div className="p-6">
      <PageHeader title="写稿与发布" subtitle={`${date} · 人工审校后登记发布，数据自动回流复盘`}
        right={
          <div className="flex items-center gap-2">
            <SlotTabs slots={data?.slots_meta || []} value={slot}
                      onChange={s => { if (guardDirty()) setSlot(s) }} />
            <button className="btn btn-primary" disabled={busy || oneClick.isPending}
                    onClick={() => oneClick.mutate()}>
              {oneClick.isPending ? '启动中…' : busy ? '任务运行中…' : '一键出稿'}
            </button>
          </div>
        } />

      <TaskPanel task={task.data} />

      <div className="grid grid-cols-[1fr_360px] gap-5 items-start">
        <div className="flex flex-col gap-4">
          <GlassCard title="文稿编辑器" className="!mb-0"
            extra={
              <div className="flex items-center gap-2">
                <span className="pill">{data?.drafts?.v1?.words || 0} 字</span>
                <button className="btn btn-sm" disabled={!dirty} onClick={() => save.mutate()}>保存</button>
              </div>
            }>
            <textarea className="textarea" rows={22} value={content}
                      onChange={e => { setContent(e.target.value); setDirty(true) }} />
            {saveMsg && <p className="ink2 text-[13px] mt-2">{saveMsg}</p>}
            <div className="flex gap-2 mt-3 flex-wrap">
              <button className="btn btn-sm" disabled={busy} onClick={() => runCmd.mutate({ cmd: 'write' })}>重写 v1</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => runCmd.mutate({ cmd: 'outline' })}>生成大纲</button>
              {data?.outline_text && <button className="btn btn-sm" disabled={busy} onClick={() => runCmd.mutate({ cmd: 'write', extra: { use_outline: true } })}>按此大纲写稿</button>}
              <button className="btn btn-sm" disabled={busy} onClick={() => runCmd.mutate({ cmd: 'titles' })}>生成标题方案</button>
              <button className="btn btn-sm" disabled={busy} onClick={() => runCmd.mutate({ cmd: 'format' })}>排版 v1</button>
            </div>
          </GlassCard>

          <GlassCard title="标题方案" className="!mb-0"
            extra={data?.titles_data?.selected && <span className="pill pill-ok">已选定：{data.titles_data.selected}</span>}>
            {!data?.titles_data?.titles?.length && <p className="muted">还没有标题方案——点「生成标题方案」。</p>}
            <div className="grid gap-2">
              {(data?.titles_data?.titles || []).map((t, i) => (
                <button key={i} className="btn justify-between text-left"
                        onClick={() => chooseTitle.mutate(t.title)}>
                  <span>{t.title}{t.reason && <span className="muted">（{t.reason}）</span>}</span>
                  {t.hook_type && <span className="pill">{t.hook_type}</span>}
                </button>
              ))}
            </div>
          </GlassCard>

          <GlassCard title="发布检查单与登记" className="!mb-0">
            <div className="grid grid-cols-2 gap-y-1.5">
              {(data?.checklist || []).map(([k, label]) => (
                <label key={k} className="flex items-center gap-2 text-[13.5px] cursor-pointer">
                  <input type="checkbox" checked={!!checkState[k]}
                         onChange={e => setCheckState(s => ({ ...s, [k]: e.target.checked }))} />
                  {label}
                </label>
              ))}
            </div>
            <div className="flex items-center gap-2 mt-3">
              <button className="btn btn-primary" disabled={!allChecked}
                      onClick={() => register.mutate(checkState)}>
                {allChecked ? '登记已发布' : '勾选全部后可登记'}
              </button>
              <button className="btn" onClick={() => reviewDone.mutate()}>标记审校完成</button>
              {data?.pub?.user_published && <span className="pill pill-ok">已发布（{data.pub.published_at}）</span>}
              {data?.pub?.review_done && <span className="pill pill-ok">审校完成</span>}
            </div>
          </GlassCard>
        </div>

        <div className="flex flex-col gap-4">
          <MaterialsPanel date={date} slot={slot} />

          <GlassCard title="金句库" extra={<span className="muted">风格参考</span>}>
            <div className="flex gap-2 mb-2">
              <input className="input" id="quote-input" placeholder="手动入库一条金句" />
              <button className="btn btn-sm" onClick={() => {
                const el = document.getElementById('quote-input') as HTMLInputElement
                if (el.value.trim()) { addQuote.mutate(el.value.trim()); el.value = '' }
              }}>入库</button>
            </div>
            {(data?.quotes || []).map((q, i) => (
              <p key={i} className="ink2 text-[13px] mb-1.5" style={{ borderLeft: '3px solid var(--accent)', paddingLeft: 8 }}>{q.text}</p>
            ))}
          </GlassCard>

          <GlassCard title="相似句提示" extra={<span className="muted">与近 4 天旧稿</span>}>
            {!data?.similar_sents?.length && <p className="muted">未检测到与近期旧稿高度相似的句子。</p>}
            {(data?.similar_sents || []).map((s, i) => (
              <p key={i} className="text-[12.5px] mb-1.5" style={{ color: 'var(--c-wait)' }}>↺ {s}</p>
            ))}
          </GlassCard>

          <GlassCard title="发布状态">
            <ul className="text-[13px] ink2" style={{ lineHeight: 2 }}>
              <li>v1：{data?.drafts?.v1?.exists ? `✅ ${data.drafts.v1.words} 字` : '未写'}</li>
              <li>v2：{data?.drafts?.v2?.exists ? `✅ ${data.drafts.v2.words} 字` : '未写'}</li>
              <li>排版：{data?.mp_exists ? '✅ 已导出' : '未导出'}</li>
              <li>草稿箱：{data?.pub?.draft_media_id ? `✅ ${data.pub.draft_pushed_at}` : '未推送'}</li>
            </ul>
          </GlassCard>
        </div>
      </div>
    </div>
  )
}
