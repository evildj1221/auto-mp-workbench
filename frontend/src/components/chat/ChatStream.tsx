import { useEffect, useRef } from 'react'
import MessageBubble from './MessageBubble'
import type { ChatCard, ChatMsg, PersonaInfo } from '../../api/client'

/** 对话流：新消息自动滚底；typing = 正在输入提示；handled = 消息→盖章文案 */
export default function ChatStream({ messages, personas, typing, handled,
                                     onConfirm, onChoose, onChooseAgent,
                                     onTitleChoose, onRegister, onSendText }: {
  messages: ChatMsg[]
  personas: PersonaInfo[]
  typing?: boolean
  handled: Record<string, string>
  onConfirm: (msgId: string, card: ChatCard, approved: boolean) => void
  onChoose: (msgId: string, rank: number, round: number) => void
  onChooseAgent: (msgId: string, rank: number) => void
  onTitleChoose: (msgId: string, slot: string, title: string) => void
  onRegister: (msgId: string, slot: string, checklist: Record<string, boolean>) => void
  onSendText: (text: string) => void
}) {
  const bottom = useRef<HTMLDivElement>(null)
  // 智能滚底：只有用户本来就在底部附近才跟随新消息——上翻阅读时不再被拽底
  const boxRef = useRef<HTMLDivElement>(null)
  const stick = useRef(true)
  const onScroll = () => {
    const el = boxRef.current
    if (!el) return
    stick.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
  }
  useEffect(() => {
    if (stick.current) {
      bottom.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }
  }, [messages.length, typing])

  const byId = new Map(personas.map(p => [p.id, p]))
  return (
    <div ref={boxRef} onScroll={onScroll} className="flex-1 min-h-0 overflow-auto flex flex-col gap-3 pr-1">
      {messages.length === 0 && !typing && (
        <div className="muted text-center mt-12">
          和编辑部的猫咪们打个招呼吧——她们认识今天日历上的每个节日。
        </div>
      )}
      {messages.map(m => {
        const p = m.persona ? byId.get(m.persona) : undefined
        return (
          <MessageBubble key={m.id} msg={m} personaColor={p?.color} personaName={p?.name}
                         stamp={handled[m.id]}
                         onConfirm={onConfirm} onChoose={onChoose} onChooseAgent={onChooseAgent}
                         onTitleChoose={onTitleChoose} onRegister={onRegister}
                         onSendText={onSendText} />
        )
      })}
      {typing && (
        <div className="flex gap-2 items-center">
          <span className="chat-avatar flex-none" style={{ background: '#f0b95c' }}>猫</span>
          <div className="bubble bubble-agent typing">
            <span className="typing-dot" /><span className="typing-dot" /><span className="typing-dot" />
            <span className="muted ml-2 text-[12px]">编辑部正在传阅你的话…</span>
          </div>
        </div>
      )}
      <div ref={bottom} />
    </div>
  )
}
