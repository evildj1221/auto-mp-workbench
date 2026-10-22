import { Component, useEffect, useState, type ReactNode } from 'react'
import { NavLink, Navigate, Route, Routes, useSearchParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from './api/client'
import { ToastHost } from './components/shared'
import ConsolePage from './features/console'
import TopicsPage from './features/topics'
import WritePage from './features/writing'
import ExportPage from './features/export'
import TypographyPage from './features/typography'
import ReviewPage from './features/review'
import ArchivePage from './features/archive'
import SettingsPage from './features/settings'

const NAV = [
  { group: '创作', items: [
    { to: '/console', label: '中控台', icon: '◉' },
    { to: '/topics', label: '选题', icon: '✎' },
    { to: '/write', label: '写稿', icon: '✍' },
    { to: '/export', label: '导出', icon: '➤' },
    { to: '/typography', label: '排版', icon: '◈' },
  ]},
  { group: '复盘', items: [
    { to: '/review', label: '复盘', icon: '▦' },
    { to: '/archive', label: '档案', icon: '▤' },
  ]},
  { group: '系统', items: [
    { to: '/settings', label: '设置', icon: '⚙' },
  ]},
]

function Sidebar() {
  // 轻量状态接口：只查状态不读 trace/error 文件；间隔放宽到 10s
  //（旧版每 5s 拉含 60 条时间线的全量快照，是常驻背景负载）
  const [sp] = useSearchParams()
  const dateParam = sp.get('date')
  // key 带 'today' 与 console 的 [key, date] 区分——同 key 不同 queryFn 曾互相污染缓存
  const { data } = useQuery({
    queryKey: ['agent-state-mini', 'today'],
    queryFn: () => api.get<{ status: string; running: boolean }>(`/api/agent/${todayStr()}/status`),
    refetchInterval: 10000,
  })
  const status = data?.status || ''
  const running = !!data?.running && ['planning', 'acting'].includes(status)
  const waiting = ['waiting_topic', 'waiting_publish', 'paused_budget'].includes(status)
  return (
    <aside className="w-[196px] flex-none flex flex-col px-4 py-5 gap-6">
      <div className="flex items-center gap-3 px-2">
        <div className="w-10 h-10 rounded-2xl flex items-center justify-center text-xl"
             style={{ background: 'linear-gradient(135deg,#f0b95c,#e0a03c)', boxShadow: '0 6px 16px rgba(224,160,60,.35)' }}>
          🐱
        </div>
        <div>
          <div className="font-semibold text-[15px]">创作工作台</div>
          <div className="muted">治愈 × 成长 · 文稿工作台</div>
        </div>
      </div>
      <nav className="flex-1 flex flex-col gap-5">
        {NAV.map(g => (
          <div key={g.group}>
            <div className="muted px-3 mb-1.5">{g.group}</div>
            {g.items.map(it => {
              // 跨页日期联动：非今天的日期随导航携带，切页不再弹回今天
              const to = dateParam && dateParam !== todayStr()
                ? `${it.to}?date=${dateParam}` : it.to
              return (
                <NavLink key={it.to} to={to}
                  className={({ isActive }) =>
                    `flex items-center gap-2.5 px-3 py-2 rounded-xl text-[13.5px] mb-0.5 transition-all ${
                      isActive ? 'glass-strong font-semibold' : 'hover:bg-white/40'}`}>
                  <span className="w-4 text-center opacity-70">{it.icon}</span>
                  {it.label}
                  {it.to === '/console' && running && <span className="lamp run ml-auto" />}
                  {it.to === '/console' && waiting && <span className="lamp wait ml-auto" />}
                </NavLink>
              )
            })}
          </div>
        ))}
      </nav>
      <div className="pill mx-1">天光云影 v3</div>
    </aside>
  )
}

function todayStr() {
  const d = new Date()
  const p = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 全局错误边界：任何页面渲染崩溃时显示错误卡与出口，而不是整页白屏假死
 *  （2026-09-27 选题页 directive_hits 对象渲染崩溃事故的兜底） */
class ErrorBoundary extends Component<{ children: ReactNode }, { err: string | null }> {
  state = { err: null as string | null }
  static getDerivedStateFromError(e: unknown) {
    return { err: e instanceof Error ? e.message : String(e) }
  }
  render() {
    if (this.state.err === null) return this.props.children
    return (
      <div className="p-6">
        <div className="glass card-pad" style={{ borderLeft: '4px solid var(--c-err)' }}>
          <h2 className="text-[16px] font-bold mb-2">这个页面渲染出错了</h2>
          <p className="ink2 text-[13px] mb-1">其他页面不受影响。可以回中控台，或把这个错误反馈给维护者：</p>
          <pre className="text-[12px] ink2 whitespace-pre-wrap mb-3"
               style={{ maxHeight: 160, overflow: 'auto', background: 'rgba(255,255,255,.5)', padding: 10, borderRadius: 10 }}>
            {this.state.err}
          </pre>
          <div className="flex gap-2">
            <button className="btn btn-primary" onClick={() => { window.location.href = '/console' }}>回到中控台</button>
            <button className="btn" onClick={() => location.reload()}>重试本页</button>
          </div>
        </div>
      </div>
    )
  }
}

type PywebviewApi = { api?: Record<string, () => void> }

function getPywebview(): PywebviewApi | undefined {
  return (window as unknown as { pywebview?: PywebviewApi }).pywebview
}

/** 自定义标题栏（仅 exe/pywebview 环境渲染；浏览器模式返回 null 零影响）。
 *  无边框窗口的拖拽区 + 最小化/全屏/关闭；pywebview-drag-region 是
 *  pywebview 约定的拖拽类名。 */
function TitleBar() {
  const [embedded, setEmbedded] = useState(false)
  useEffect(() => {
    let tries = 0
    const t = setInterval(() => {
      if (getPywebview()?.api) { setEmbedded(true); clearInterval(t) }
      else if (++tries > 24) clearInterval(t) // ~6s 未注入 → 纯浏览器模式
    }, 250)
    return () => clearInterval(t)
  }, [])
  if (!embedded) return null
  const call = (m: string) => () => getPywebview()?.api?.[m]?.()
  return (
    <div className="flex-none flex items-stretch h-[34px] select-none"
         style={{ background: 'linear-gradient(90deg, var(--sky-from), var(--sky-mid))' }}>
      <div className="pywebview-drag-region flex-1 flex items-center gap-2 px-3">
        <span className="text-[14px] leading-none">🐱</span>
        <span className="text-[12px] font-semibold" style={{ color: 'var(--ink-1)' }}>创作工作台</span>
      </div>
      <button className="titlebar-btn" title="最小化" onClick={call('minimize')}>—</button>
      <button className="titlebar-btn" title="全屏切换" onClick={call('toggle_max')}>□</button>
      <button className="titlebar-btn titlebar-close" title="关闭" onClick={call('close')}>×</button>
    </div>
  )
}

export default function App() {
  return (
    <div className="h-full flex flex-col">
      <TitleBar />
      <div className="flex-1 min-h-0 flex">
        <div className="h-full glass flex" style={{ borderRadius: 0, borderRight: '1px solid var(--glass-border)' }}>
          <Sidebar />
        </div>
        {/* 不按路径强制重挂载：旧版 key={pathname} 让每次导航整页销毁重建，
            在毛玻璃高成本绘制上叠加重挂载，导航体感发肉（查询缓存本就全局共享） */}
        <main className="flex-1 overflow-auto">
          <ErrorBoundary>
            <Routes>
          <Route path="/" element={<Navigate to="/console" replace />} />
          <Route path="/console" element={<ConsolePage />} />
          <Route path="/today" element={<Navigate to="/console" replace />} />
          <Route path="/agent" element={<Navigate to="/console" replace />} />
          <Route path="/topics" element={<TopicsPage />} />
          <Route path="/write" element={<WritePage />} />
          <Route path="/export" element={<ExportPage />} />
          <Route path="/typography" element={<TypographyPage />} />
          <Route path="/review" element={<ReviewPage />} />
          <Route path="/archive" element={<ArchivePage />} />
          <Route path="/settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/console" replace />} />
          </Routes>
        </ErrorBoundary>
      </main>
      </div>
      <ToastHost />
    </div>
  )
}
