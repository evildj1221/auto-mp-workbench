import { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { GlassCard } from './shared'

// ============================================================
// 常量：前端校验与后端保持同一套规则（backend/app/api/uploads.py）
// ============================================================
const ALLOWED_EXT = ['.png', '.jpg', '.jpeg', '.webp', '.md', '.txt', '.docx']
const MAX_FILE_MB = 10
const MAX_FILE_BYTES = MAX_FILE_MB * 1024 * 1024
const ACCEPT_ATTR = ALLOWED_EXT.join(',')

export interface Material {
  name: string; saved_as: string; type: 'image' | 'doc'
  size: number; uploaded_at: string; text?: string
}

type ItemStatus = 'validating' | 'uploading' | 'done' | 'error'

interface UploadItem {
  id: number
  name: string
  size: number
  status: ItemStatus
  progress: number   // 0-100
  error?: string
  kind?: 'image' | 'doc'
}

let _itemId = 0

/** 带进度的单文件上传（XHR 才能拿到 upload.onprogress；fetch 不暴露）。 */
function uploadWithProgress(date: string, slot: string, file: File,
                            onProgress: (pct: number) => void): Promise<any> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    // slot 为空时不拼尾随空段（/api/uploads/{date} 由后端同名路由承接，与 GET 列表对称）
    xhr.open('POST', `/api/uploads/${date}${slot ? `/${slot}` : ''}`)
    xhr.upload.onprogress = e => {
      if (e.lengthComputable) onProgress(Math.min(99, Math.round(e.loaded / e.total * 100)))
    }
    xhr.onload = () => {
      try {
        const body = JSON.parse(xhr.responseText)
        body.ok ? resolve(body.data) : reject(new Error(body.error?.message || '上传失败'))
      } catch {
        reject(new Error(`服务异常 (${xhr.status})`))
      }
    }
    xhr.onerror = () => reject(new Error('网络错误，请检查本地服务'))
    const fd = new FormData()
    fd.set('date', date)
    fd.set('slot', slot)
    fd.append('files', file)
    xhr.send(fd)
  })
}

/**
 * P3 素材上传模块（双入口同管线）：
 *   拖放区（高亮反馈 + 类型校验）与「选择文件」按钮 → 同一条
 *   校验 → 逐文件上传（进度条）→ 成功/失败状态反馈 管线。
 * 图片作生图参考；文稿（md/txt/docx）文本注入写稿上下文。
 */
export function MaterialsPanel({ date, slot }: { date: string; slot: string }) {
  const qc = useQueryClient()
  const fileRef = useRef<HTMLInputElement>(null)
  const dragCounter = useRef(0)
  const [dragActive, setDragActive] = useState(false)
  const [items, setItems] = useState<UploadItem[]>([])
  const [summary, setSummary] = useState('')

  const { data } = useQuery({
    queryKey: ['uploads', date, slot],
    queryFn: () => api.get<{ materials: Material[] }>(`/api/uploads/${date}?slot=${slot}`),
  })
  const materials = data?.materials || []

  const patchItem = (id: number, patch: Partial<UploadItem>) =>
    setItems(list => list.map(it => (it.id === id ? { ...it, ...patch } : it)))

  /** 双入口共用管线：校验 → 逐文件上传 → 汇总反馈。 */
  async function handleFiles(fileList: FileList | File[] | null) {
    const files = Array.from(fileList || [])
    if (!files.length) return
    setSummary('')

    // ① 前置校验（类型 + 大小），不合格直接入列标红
    const queue: { id: number; file: File }[] = []
    const fresh: UploadItem[] = files.map(f => {
      const id = ++_itemId
      const ext = f.name.slice(f.name.lastIndexOf('.')).toLowerCase()
      if (!ALLOWED_EXT.includes(ext)) {
        return { id, name: f.name, size: f.size, status: 'error', progress: 0,
                 error: `不支持的文件类型 ${ext || '（无后缀）'}` }
      }
      if (f.size === 0) {
        return { id, name: f.name, size: 0, status: 'error', progress: 0, error: '空文件' }
      }
      if (f.size > MAX_FILE_BYTES) {
        return { id, name: f.name, size: f.size, status: 'error', progress: 0,
                 error: `超过 ${MAX_FILE_MB}MB 限制` }
      }
      queue.push({ id, file: f })
      return { id, name: f.name, size: f.size, status: 'validating', progress: 0,
               kind: ext === '.png' || ext === '.jpg' || ext === '.jpeg' || ext === '.webp'
                     ? 'image' : 'doc' }
    })
    setItems(list => [...fresh, ...list])

    // ② 逐文件顺序上传（同一进度管线，失败互不影响）
    let ok = 0, bad = 0
    for (const { id, file } of queue) {
      patchItem(id, { status: 'uploading', progress: 1 })
      try {
        const res = await uploadWithProgress(date, slot, file, p => patchItem(id, { progress: p }))
        patchItem(id, { status: 'done', progress: 100,
                        error: res.skipped?.[0]?.reason })
        res.skipped?.[0]?.reason ? bad++ : ok++
      } catch (e: any) {
        patchItem(id, { status: 'error', progress: 100, error: e.message })
        bad++
      }
    }

    // ③ 汇总反馈 + 刷新素材列表
    const rejected = fresh.length - queue.length
    const parts: string[] = []
    if (ok) parts.push(`成功 ${ok}`)
    if (bad) parts.push(`失败 ${bad}`)
    if (rejected) parts.push(`本地校验拦截 ${rejected}`)
    setSummary(parts.join(' · '))
    qc.invalidateQueries({ queryKey: ['uploads'] })
    if (fileRef.current) fileRef.current.value = ''
  }

  async function remove(savedAs: string) {
    await api.post('/api/uploads/delete', { date, slot, saved_as: savedAs })
    qc.invalidateQueries({ queryKey: ['uploads'] })
  }

  return (
    <GlassCard title="我的素材" extra={<span className="muted">图片作生图参考 · 文稿注入写作</span>}>
      {/* ===== 入口一：拖放区（拖入高亮 + 类型/大小校验） ===== */}
      <div
        onDragEnter={e => { e.preventDefault(); dragCounter.current++; setDragActive(true) }}
        onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'copy' }}
        onDragLeave={e => { e.preventDefault(); if (--dragCounter.current <= 0) { dragCounter.current = 0; setDragActive(false) } }}
        onDrop={e => {
          e.preventDefault()
          dragCounter.current = 0
          setDragActive(false)
          handleFiles(e.dataTransfer.files)
        }}
        className="rounded-xl border-2 border-dashed p-5 text-center cursor-pointer select-none"
        style={{
          borderColor: dragActive ? 'var(--accent)' : 'rgba(138,152,173,.45)',
          background: dragActive ? 'var(--accent-soft)' : 'transparent',
          transform: dragActive ? 'scale(1.015)' : 'none',
          transition: 'all .18s var(--ease)',
        }}
        onClick={() => fileRef.current?.click()}>
        <input ref={fileRef} type="file" multiple hidden accept={ACCEPT_ATTR}
               onChange={e => handleFiles(e.target.files)} />
        <div style={{ fontSize: 26, transform: dragActive ? 'scale(1.15)' : 'none',
                      transition: 'transform .18s var(--ease)' }}>📥</div>
        {dragActive
          ? <p className="text-[13.5px] font-medium" style={{ color: 'var(--accent)' }}>松开即可上传</p>
          : <p className="ink2 text-[13.5px]">把素材拖到这里，或点击选择文件</p>}
        <p className="muted mt-1">支持 {ALLOWED_EXT.join(' / ')} · 单个不超过 {MAX_FILE_MB}MB</p>
      </div>

      {/* ===== 入口二：上传按钮（系统文件选择对话框） ===== */}
      <div className="flex items-center gap-2 mt-3">
        <button className="btn btn-primary btn-sm" disabled={items.some(i => i.status === 'uploading')}
                onClick={() => fileRef.current?.click()}>
          {items.some(i => i.status === 'uploading') ? '上传中…' : '选择文件上传'}
        </button>
        {summary && <span className="ink2 text-[12.5px]">{summary}</span>}
      </div>

      {/* ===== 状态反馈：逐文件进度 / 结果 ===== */}
      {!!items.length && (
        <div className="mt-3 grid gap-1.5">
          {items.slice(0, 8).map(it => (
            <div key={it.id} className="rounded-xl bg-white/50 px-3 py-2">
              <div className="flex items-center gap-2 text-[12.5px]">
                <span className={`lamp ${it.status === 'done' ? 'ok' : it.status === 'error' ? 'err' : it.status === 'uploading' ? 'run' : ''}`} />
                <span className={`flex-1 truncate ${it.status === 'error' ? '' : 'ink2'}`}
                      style={{ color: it.status === 'error' ? 'var(--c-err)' : undefined }}>
                  {it.name}{it.status === 'error' ? ` · ${it.error}` :
                            it.status === 'done' && it.error ? ` · ⚠ ${it.error}` : ''}
                </span>
                <span className="muted">{(it.size / 1024).toFixed(0)} KB</span>
              </div>
              {it.status === 'uploading' && (
                <div className="h-1.5 mt-1.5 rounded-full bg-white/70 overflow-hidden">
                  <div className="h-full rounded-full transition-all"
                       style={{ width: `${it.progress}%`,
                                background: 'linear-gradient(90deg,#4a6fbf,#e0a03c)' }} />
                </div>
              )}
            </div>
          ))}
          {items.length > 8 && <p className="muted text-[12px]">仅显示最近 8 条</p>}
        </div>
      )}

      {/* ===== 已入库素材（可删除） ===== */}
      <div className="mt-2 flex flex-wrap gap-1.5">
        {materials.map(m => (
          <span key={m.saved_as} className={`pill ${m.type === 'image' ? 'pill-ok' : ''}`}>
            {m.type === 'image' ? '🖼' : '📄'} {m.name}
            <button className="ml-1 opacity-60 hover:opacity-100"
                    onClick={() => remove(m.saved_as)}>×</button>
          </span>
        ))}
      </div>
    </GlassCard>
  )
}
