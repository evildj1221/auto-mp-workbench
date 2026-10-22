import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { ChatCard, ChatMsg, ChatHistoryResp } from '../../api/client'
import { api, DateBar, useDate } from '../../components/shared'
import AgentRoster, { usePersonas } from '../../components/chat/AgentRoster'
import ChatStream from '../../components/chat/ChatStream'
import { WIDGET_COMPONENTS, WIDGET_DEFS, type WidgetId } from '../../components/chat/widgets'

/** 22:00 发车倒计时（电视台中控台的挂钟） */
function broadcastClock(now: Date): string {
  const p = (n: number) => String(n).padStart(2, '0')
  const target = new Date(now)
  target.setHours(22, 0, 0, 0)
  if (now < target) {
    const diff = target.getTime() - now.getTime()
    return `距 22:00 发车 ${Math.floor(diff / 3600000)}:${p(Math.floor((diff % 3600000) / 60000))}`
  }
  return '今日已发车 · 明晚见'
}

const STATUS_LABEL: Record<string, string> = {
  planning: '运行中', acting: '运行中', waiting_topic: '等你选题',
  waiting_publish: '等你批准', paused_budget: '预算暂停', done: '今日完工',
  interrupted: '已中断', cancelled: '已取消', error: '出错', none: '未运行',
}

/** 快捷指令：单击即发给对应同事 */
/** 快捷指令：单击即发给对应同事 */
const QUICK_CHIPS = [
  '@主编 开始今天的全套活儿',
  '生成一批今天的候选',
  '@撰稿人 写 v1',
  '今天进度怎么样？',
  '@发行 排版导出',
  '@档案员 讲讲最近攒的经验',
]

// ===== DIY 布局（localStorage 持久化；中间对话框固定不可删） =====
type PanelCfg = { id: WidgetId; h: number }
type Layout = { sidebar: number; roster: boolean; panels: PanelCfg[] }

const LAYOUT_KEY = 'console-layout-v1'
const DEFAULT_LAYOUT: Layout = {
  sidebar: 340, roster: true,
  panels: [
    { id: 'time', h: 150 }, { id: 'pipeline', h: 210 }, { id: 'preview', h: 480 },
    { id: 'stats', h: 130 }, { id: 'runtime', h: 230 }, { id: 'trace', h: 280 },
    { id: 'links', h: 110 },
  ],
}

function loadLayout(): Layout {
  try {
    const raw = localStorage.getItem(LAYOUT_KEY)
    if (!raw) return DEFAULT_LAYOUT
    const v = JSON.parse(raw) as Layout
    if (!Array.isArray(v.panels)) return DEFAULT_LAYOUT
    v.panels = v.panels.filter(p => WIDGET_DEFS.some(d => d.id === p.id))
    return { ...DEFAULT_LAYOUT, ...v }
  } catch {
    return DEFAULT_LAYOUT
  }
}

const persistLayout = (l: Layout) => {
  try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(l)) } catch { /* 隐私模式/配额 */ }
}

/** 中控台（DIY 版）：左同事栏（可关）· 中对话流（固定）· 右组件区（自由增删/调大小） */
export default function ConsolePage() {
  const [date, setDate] = useDate()
  const [input, setInput] = useState('')
  const [msgs, setMsgs] = useState<ChatMsg[]>([])
  const [action, setAction] = useState<ChatHistoryResp['action']>(null)
  const [handled, setHandled] = useState<Record<string, string>>({})
  const [sending, setSending] = useState(false)
  const [now, setNow] = useState(new Date())
  const [pickerOpen, setPickerOpen] = useState(false)
  const [layout, setLayout] = useState<Layout>(loadLayout)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const { data: personaData } = usePersonas()
  const personas = personaData?.personas || []

  // 与侧栏同键不同参（侧栏固定今天）——同 key 不同 queryFn 曾互相污染缓存
  const { data: agentMini } = useQuery({
    queryKey: ['agent-state-mini', date],
    queryFn: () => api.get<{ status: string; running: boolean; label: string }>(`/api/agent/${date}/status`),
    refetchInterval: 10000,
  })
  // 台头成本电表/预览开关复用 brief（与组件共享同一查询缓存，请求自动合并）
  const { data: brief } = useQuery({
    queryKey: ['chat-brief', date],
    queryFn: () => api.get<import('../../api/client').ChatBrief>(`/api/chat/brief?date=${date}`),
    refetchInterval: 30000,
  })

  // 对话历史 + 动作状态（切日期即切换当日的编辑部屋）
  const refresh = useCallback(async () => {
    try {
      const d = await api.get<ChatHistoryResp>(`/api/chat/${date}`)
      setMsgs(d.messages || [])
      setAction(d.action)
    } catch { /* 服务未就绪时静默，下次轮询再取 */ }
  }, [date])

  useEffect(() => { refresh() }, [refresh])

  // 轮询策略：动作运行期 2.5s 快轮询收进度；空闲 12s 慢轮询——方案 B 的
  // 事件冒泡（其他页面/入口的手动动作）靠慢轮询自动浮进对话流
  const refreshRef = useRef(refresh)
  refreshRef.current = refresh
  const sendingRef = useRef(false)
  sendingRef.current = sending
  useEffect(() => {
    const fast = action?.status === 'running'
    const t = setInterval(() => {
      if (!sendingRef.current) refreshRef.current()  // 发送中暂停轮询，防乐观气泡被服务端列表抹掉闪烁
    }, fast ? 2500 : 12000)
    return () => clearInterval(t)
  }, [action?.status, action?.tid, date])

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  // 组件动作（审批/运行）→ 立即刷新对话流
  useEffect(() => {
    const h = () => refreshRef.current()
    window.addEventListener('wb-chat-refresh', h)
    return () => window.removeEventListener('wb-chat-refresh', h)
  }, [])

  // 天光随时辰走：清晨→午后→黄昏→深夜（设计方案 §四.5）
  useEffect(() => {
    const h = now.getHours()
    document.body.dataset.sky =
      h >= 5 && h < 11 ? 'morning' : h >= 11 && h < 17 ? 'afternoon'
        : h >= 17 && h < 22 ? 'evening' : 'night'
  }, [now])

  const send = async (textOverride?: string) => {
    const text = (textOverride ?? input).trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    const tmpId = `tmp-${Date.now()}`
    setMsgs(m => [...m, { id: tmpId, ts: '', role: 'user', text }])
    try {
      // 注意：确认卡挂起时回「好/算了」，后端返回 {user, executed|declined}——
      // 没有 reply 字段（旧版硬取 r.reply 塞进列表 → React 渲染 undefined 整页崩溃）
      const r = await api.post<{ user: ChatMsg; reply?: ChatMsg }>('/api/chat', { date, text })
      setMsgs(m => [...m.filter(x => x.id !== tmpId), r.user, ...(r.reply ? [r.reply] : [])])
      await refresh() // 同步动作状态（回复可能带确认卡/执行回执）
    } catch (e) {
      const why = e instanceof Error ? e.message : '未知错误'
      setMsgs(m => [
        ...m.filter(x => x.id !== tmpId),
        { id: `tmp2-${Date.now()}`, ts: '', role: 'user', text },
        { id: `err-${Date.now()}`, ts: '', role: 'system',
          text: `没送出去（${why}）。稍后再试一次。` },  // 错误在用户消息之后，因果不倒置
      ])
    } finally {
      setSending(false)
      inputRef.current?.focus()
    }
  }

  // 确认卡 / Top5 卡：处理后盖印章（已安排 / 已拍板 #N / 先不用）
  const markHandled = (id: string, text: string) =>
    setHandled(s => ({ ...s, [id]: text }))

  const onConfirm = async (msgId: string, card: ChatCard, approved: boolean) => {
    markHandled(msgId, approved ? '已安排' : '先不用')
    try {
      await api.post('/api/chat/confirm', { date, msg_id: msgId, approved })
      await refresh()
    } catch (e) {
      setMsgs(m => [...m, { id: `err-${Date.now()}`, ts: '', role: 'system',
        text: `确认没送到（${e instanceof Error ? e.message : '未知'}）。` }])
    }
  }

  // Top5 卡点选 → Step 3.7 在对话里完成，撰稿人接单（msg_id 幂等：卡复活重不重复执行）
  const onChoose = async (msgId: string, rank: number, round: number) => {
    markHandled(msgId, `已拍板 #${rank}`)
    try {
      await api.post('/api/chat/choose', { date, choice: rank, round, msg_id: msgId })
      await refresh()
    } catch (e) {
      setMsgs(m => [...m, { id: `err-${Date.now()}`, ts: '', role: 'system',
        text: `点选没成功（${e instanceof Error ? e.message : '未知'}）。` }])
    }
  }

  // Agent 主图选题关卡的点选（主编全套活儿跑到关卡时）→ 走 agent 审批通道，
  // 并让对话进入跟随模式（下一个关卡继续冒泡）
  const onChooseAgent = async (msgId: string, rank: number) => {
    markHandled(msgId, `已拍板 #${rank}`)
    try {
      await api.post('/api/agent/approve', { date, choice: rank })
      await api.post('/api/chat/follow-agent', { date })
      await refresh()
    } catch (e) {
      setMsgs(m => [...m, { id: `err-${Date.now()}`, ts: '', role: 'system',
        text: `点选没送到（${e instanceof Error ? e.message : '未知'}）。` }])
    }
  }

  // 方案 A：标题点选卡——与写稿页「标题方案」同源 API，选完 B 的事件流会冒泡回执
  const onTitleChoose = async (msgId: string, slot: string, title: string) => {
    markHandled(msgId, `已定标题`)
    try {
      await api.post('/api/drafts/titles/choose', { date, slot, title })
      await refresh()
    } catch (e) {
      setMsgs(m => [...m, { id: `err-${Date.now()}`, ts: '', role: 'system',
        text: `标题没定上（${e instanceof Error ? e.message : '未知'}）。` }])
    }
  }

  // 方案 A：发布检查单——勾满登记（与写稿页发布登记同源 API）
  const onRegister = async (msgId: string, slot: string, checklist: Record<string, boolean>) => {
    markHandled(msgId, '已登记发布')
    try {
      await api.post('/api/drafts/publish-register', { date, slot, checklist })
      await refresh()
    } catch (e) {
      setMsgs(m => [...m, { id: `err-${Date.now()}`, ts: '', role: 'system',
        text: `登记没成功（${e instanceof Error ? e.message : '未知'}）。` }])
    }
  }

  // 卡片里的「下一步」按钮：直接以台长口吻发一条消息（走正常路由/确认卡链路）
  const onSendText = (text: string) => { send(text) }

  const mention = (name: string) => {
    setInput(v => (v ? `${v} @${name} ` : `@${name} `))
    inputRef.current?.focus()
  }

  // ===== 布局操作（改动即持久化到 localStorage） =====
  const update = (fn: (l: Layout) => Layout) => setLayout(prev => {
    const next = fn(prev)
    persistLayout(next)
    return next
  })
  const removePanel = (id: WidgetId) =>
    update(l => ({ ...l, panels: l.panels.filter(p => p.id !== id) }))
  const addPanel = (id: WidgetId) => {
    if (layout.panels.some(p => p.id === id)) return
    const def = WIDGET_DEFS.find(d => d.id === id)!
    update(l => ({ ...l, panels: [...l.panels, { id, h: def.defH }] }))
  }
  const setPanelH = (idx: number, h: number) =>
    setLayout(prev => {
      const next = { ...prev, panels: prev.panels.map((p, i) => i === idx ? { ...p, h } : p) }
      return next
    })

  const status = agentMini?.status || 'none'
  const onAir = !!agentMini?.running || action?.status === 'running'
  return (
    <div className="h-full flex flex-col p-4 gap-4 min-h-0">
      {/* 台头：节目单挂钟 + ON AIR 灯 + 状态 + 成本电表 */}
      <header className="flex-none glass card-pad flex items-center justify-between"
              style={{ padding: '12px 20px' }}>
        <div>
          <h1 className="text-[18px] font-bold">中控台 · 创作编辑部</h1>
          <p className="muted">{date} {agentMini ? STATUS_LABEL[status] : ''}
            {action?.status === 'running' && ' · 编辑部正忙着，好了会在对话里冒泡'}</p>
        </div>
        <div className="flex items-center gap-2.5">
          <DateBar date={date} onChange={setDate} />
          {onAir && <span className="onair">ON AIR</span>}
          <span className={`pill ${agentMini?.running ? 'pill-run' : ''}`}>
            {STATUS_LABEL[status] || status}
          </span>
          <span className="pill" title="今日编辑部电费（LLM + 生图）"
                style={{ fontVariantNumeric: 'tabular-nums' }}>
            ⚡ ¥{Number(brief?.cost_today || 0).toFixed(2)}
          </span>
          <span className="pill">{broadcastClock(now)}</span>
        </div>
      </header>

      <div className="flex-1 min-h-0 flex gap-2">
        {/* 左：编辑部同事栏（DIY 可开关） */}
        {layout.roster && (
          <div className="flex-none" style={{ width: 176 }}>
            <AgentRoster onMention={mention} />
          </div>
        )}

        {/* 中央：对话流 + 快捷指令 + 输入台（固定结构，不可删减） */}
        <section className="flex-1 min-w-0 flex flex-col gap-2.5 min-h-0">
          <div className="flex-1 min-h-0 glass card-pad flex">
            <ChatStream messages={msgs} personas={personas} typing={sending}
                        handled={handled}
                        onConfirm={onConfirm} onChoose={onChoose}
                        onChooseAgent={onChooseAgent}
                        onTitleChoose={onTitleChoose} onRegister={onRegister}
                        onSendText={onSendText} />
          </div>
          <div className="flex-none flex items-center gap-2 flex-wrap px-1">
            {QUICK_CHIPS.map(c => (
              <button key={c} className="btn btn-sm opacity-80 hover:opacity-100"
                      disabled={sending} onClick={() => send(c)}>{c}</button>
            ))}
          </div>
          <div className="flex-none glass card-pad flex items-end gap-2">
            <textarea ref={inputRef} className="textarea flex-1" rows={2} value={input}
                      placeholder="对编辑部说点什么…（Enter 发送 · Shift+Enter 换行 · @花名 点名）"
                      onChange={e => setInput(e.target.value)}
                      onKeyDown={e => {
                        // isComposing：中文输入法组词时按 Enter 是「确认候选词」，
                        // 不是发送（旧版会把半截拼音直接发出去）
                        const composing = (e.nativeEvent as KeyboardEvent).isComposing
                          || e.nativeEvent.keyCode === 229
                        if (e.key === 'Enter' && !e.shiftKey && !composing) {
                          e.preventDefault(); send()
                        }
                      }} />
            <button className="btn btn-primary" disabled={sending || !input.trim()}
                    onClick={() => send()}>发送</button>
          </div>
        </section>

        {/* 拖宽把手：调节右侧组件区宽度 */}
        <div className="flex-none w-1.5 rounded-full cursor-col-resize hover:bg-[var(--accent-soft)] transition-all self-stretch"
             title="拖动调节组件区宽度"
             onPointerDown={e => {
               e.preventDefault()
               try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) } catch { /* 合成事件无激活指针 */ }
               const startX = e.clientX, startW = layout.sidebar
               const snapshot = { ...layout }   // 拖拽期间布局不变，快照即最新
               const move = (ev: PointerEvent) => {
                 const w = Math.max(260, Math.min(640, startW + ev.clientX - startX))
                 setLayout(l => ({ ...l, sidebar: w }))
                 persistLayout({ ...snapshot, sidebar: w })  // move 即落盘
               }
               const up = () => {
                 window.removeEventListener('pointermove', move)
                 window.removeEventListener('pointerup', up)
                 // move 已逐帧落盘，up 无需再存（ref 旧值反而会覆盖回拖前尺寸）
               }
               window.addEventListener('pointermove', move)
               window.addEventListener('pointerup', up)
             }} />

        {/* 右：DIY 组件区（自由增删/调高；组件删掉再加回时重新挂载即拉最新数据） */}
        <aside className="flex-none flex flex-col gap-2.5 min-h-0"
               style={{ width: layout.sidebar }}>
          <div className="flex-none flex items-center gap-2 px-1">
            <button className="btn btn-sm" onClick={() => setPickerOpen(v => !v)}>⚙ 添加组件</button>
            <button className="btn btn-sm" title="恢复默认布局"
                    onClick={() => update(() => JSON.parse(JSON.stringify(DEFAULT_LAYOUT)))}>
              恢复默认
            </button>
            <span className="muted text-[11.5px] ml-auto">{layout.panels.length} 个组件</span>
          </div>

          {pickerOpen && (
            <div className="flex-none glass card-pad grid grid-cols-2 gap-1.5">
              {WIDGET_DEFS.map(d => {
                const on = layout.panels.some(p => p.id === d.id)
                return (
                  <button key={d.id}
                          className={`btn btn-sm justify-start text-left ${on ? 'btn-primary' : ''}`}
                          title={d.desc}
                          onClick={() => on ? removePanel(d.id) : addPanel(d.id)}>
                    <span className="truncate">{on ? '✓ ' : '＋ '}{d.label}</span>
                  </button>
                )
              })}
              <button className={`btn btn-sm justify-start text-left col-span-2 ${layout.roster ? 'btn-primary' : ''}`}
                      onClick={() => update(l => ({ ...l, roster: !l.roster }))}>
                {layout.roster ? '✓ ' : '＋ '}编辑部同事栏（左栏）
              </button>
            </div>
          )}

          <div className="flex-1 min-h-0 overflow-y-auto flex flex-col gap-2.5 pr-0.5">
            {layout.panels.map((p, idx) => {
              const def = WIDGET_DEFS.find(d => d.id === p.id)!
              const Comp = WIDGET_COMPONENTS[p.id]
              return (
                <section key={p.id} className="flex-none glass card-pad relative group flex flex-col"
                         style={{ paddingTop: 14 }}>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="section-title">{def.label}</h3>
                    <button className="opacity-40 hover:opacity-100 text-[14px] leading-none px-1"
                            title="移除组件（可随时加回，数据接着最新状态）"
                            onClick={() => removePanel(p.id)}>×</button>
                  </div>
                  <div className="min-h-0 overflow-auto" style={{ height: Math.max(80, p.h) }}>
                    <Comp date={date} />
                  </div>
                  {/* 拖高把手（悬停可见） */}
                  <div className="absolute left-2 right-2 bottom-0 h-2.5 cursor-row-resize opacity-0 group-hover:opacity-100 transition-opacity flex items-center justify-center"
                       title="拖动调节高度"
                       onPointerDown={e => {
                         e.preventDefault()
                         try { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId) } catch { /* 合成事件无激活指针 */ }
                         const startY = e.clientY, base = p.h
                         const panels0 = layout.panels.map(pp => ({ ...pp }))
                         const move = (ev: PointerEvent) => {
                           const h = Math.max(80, Math.min(900, base + ev.clientY - startY))
                           setPanelH(idx, h)
                           persistLayout({ ...layout, panels: panels0.map((pp, i) => i === idx ? { ...pp, h } : pp) })
                         }
                         const up = () => {
                           window.removeEventListener('pointermove', move)
                           window.removeEventListener('pointerup', up)
                         }
                         window.addEventListener('pointermove', move)
                         window.addEventListener('pointerup', up)
                       }}>
                    <span className="block w-10 h-1 rounded-full bg-[var(--ink-3)]" />
                  </div>
                </section>
              )
            })}
            {!layout.panels.length && (
              <div className="glass card-pad muted text-[13px] text-center">
                组件区空了——点上方「⚙ 添加组件」挑几个回来。对话框始终在这里，不受影响。
              </div>
            )}
          </div>
        </aside>
      </div>
    </div>
  )
}
