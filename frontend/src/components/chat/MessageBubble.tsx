import { useState, type CSSProperties } from 'react'
import type { ChatCard, ChatMsg } from '../../api/client'

/** 单条消息气泡：用户（右）/ 猫咪同事（左带头像）/ 系统事件（居中灰字）。
 *  卡片（确认卡 / Top5 点选卡 / 标题点选卡 / 检查单卡 / 下一步按钮）内嵌在
 *  气泡里——方案 A：对话里直接拍板，不跳页。 */
export default function MessageBubble({ msg, personaColor, personaName, stamp,
                                        onConfirm, onChoose, onChooseAgent,
                                        onTitleChoose, onRegister, onSendText }: {
  msg: ChatMsg
  personaColor?: string
  personaName?: string
  stamp?: string
  onConfirm?: (msgId: string, card: ChatCard, approved: boolean) => void
  onChoose?: (msgId: string, rank: number, round: number) => void
  onChooseAgent?: (msgId: string, rank: number) => void
  onTitleChoose?: (msgId: string, slot: string, title: string) => void
  onRegister?: (msgId: string, slot: string, checklist: Record<string, boolean>) => void
  onSendText?: (text: string) => void
}) {
  if (msg.role === 'system')
    return <div className="text-center muted text-[12px] my-0.5">{msg.text}</div>

  if (msg.role === 'user')
    return (
      <div className="flex justify-end">
        <div className="bubble bubble-user">{msg.text}</div>
      </div>
    )

  const color = personaColor || '#e0a03c'
  const cards = msg.cards || []
  return (
    <div className="flex gap-2 items-start">
      <span className="chat-avatar flex-none" style={{ background: color }}>
        {(personaName || '猫')[0]}
      </span>
      <div className="min-w-0">
        <div className="muted mb-0.5">
          {personaName || '编辑部'}{msg.mode === 'act' ? ' · 干活回来了' : ''}
          {stamp && <span className="stamp">{stamp}</span>}
        </div>
        <div className="bubble bubble-agent" style={{ '--pc': color } as CSSProperties}>
          {msg.text}
          {cards.map((c, i) => (
            <CardView key={i} card={c} msgId={msg.id} handled={!!stamp}
                      onConfirm={onConfirm} onChoose={onChoose} onChooseAgent={onChooseAgent}
                      onTitleChoose={onTitleChoose} onRegister={onRegister} onSendText={onSendText} />
          ))}
        </div>
      </div>
    </div>
  )
}

function CardView({ card, msgId, handled, onConfirm, onChoose, onChooseAgent,
                    onTitleChoose, onRegister, onSendText }: {
  card: ChatCard; msgId: string; handled?: boolean
  onConfirm?: (msgId: string, card: ChatCard, approved: boolean) => void
  onChoose?: (msgId: string, rank: number, round: number) => void
  onChooseAgent?: (msgId: string, rank: number) => void
  onTitleChoose?: (msgId: string, slot: string, title: string) => void
  onRegister?: (msgId: string, slot: string, checklist: Record<string, boolean>) => void
  onSendText?: (text: string) => void
}) {
  if (card.type === 'confirm')
    return (
      <div className="flex gap-2 mt-2.5 flex-wrap">
        <button className="btn btn-primary btn-sm" disabled={handled}
                onClick={() => onConfirm?.(msgId, card, true)}>
          ▶ {card.label || '交给我'}
        </button>
        <button className="btn btn-sm" disabled={handled}
                onClick={() => onConfirm?.(msgId, card, false)}>先不用</button>
      </div>
    )
  if (card.type === 'top5') {
    // channel=agent：Agent 主图的选题关卡，点选走 /api/agent/approve
    const pick = card.channel === 'agent'
      ? (rank: number) => onChooseAgent?.(msgId, rank)
      : (rank: number) => onChoose?.(msgId, rank, card.round || 1)
    return (
      <div className="grid gap-1.5 mt-2.5">
        {(card.top5 || []).map(t => (
          <button key={t.rank} className="btn btn-sm justify-between text-left"
                  disabled={handled || !t.topic}
                  onClick={() => pick(t.rank)}>
            <span>#{t.rank} {t.topic}</span>
            <span className="pill">综合 {t.total}</span>
          </button>
        ))}
        {!(card.top5 || []).length && <p className="muted">（这批没有可展示的 Top5）</p>}
      </div>
    )
  }
  if (card.type === 'titles') {
    // 方案 A：标题点选卡——点一条即定标题（与写稿页同源 API）
    if (!handled && onTitleChoose && card.titles?.length) {
      return (
        <div className="grid gap-1.5 mt-2.5">
          {card.titles.map((t, i) => (
            <button key={i} className={`btn btn-sm text-left ${card.selected === t ? 'btn-primary' : ''}`}
                    disabled={!!card.selected}
                    onClick={() => onTitleChoose(msgId, card.slot || '', t)}>
              <span>{t}{card.selected === t ? ' ✅ 已定' : ''}</span>
            </button>
          ))}
        </div>
      )
    }
    return (
      <div className="grid gap-1 mt-2.5">
        {(card.titles || []).map((t, i) => (
          <p key={i} className="text-[13px] ink2">{t}{card.selected === t ? ' ✅' : ''}</p>
        ))}
      </div>
    )
  }
  if (card.type === 'draft')
    return (
      <div className="mt-2">
        <p className="text-[13px] ink2">
          📄 v1 已生成{card.words ? ` · ${card.words} 字` : ''}{card.slot ? ` · 槽位 ${card.slot}` : ''}
        </p>
        {card.next && !handled && (
          <button className="btn btn-sm btn-gold mt-1.5"
                  onClick={() => onSendText?.(card.next!.send)}>
            {card.next.label} →
          </button>
        )}
      </div>
    )
  if (card.type === 'exported')
    return <ChecklistCard card={card} msgId={msgId} handled={handled} onRegister={onRegister} />
  return card.text ? <p className="text-[13px] ink2 mt-2">{card.text}</p> : null
}

/** 方案 A：发布检查单卡——对话里勾满 7 项直接登记（与写稿页同源 API） */
function ChecklistCard({ card, msgId, handled, onRegister }: {
  card: ChatCard; msgId: string; handled?: boolean
  onRegister?: (msgId: string, slot: string, checklist: Record<string, boolean>) => void
}) {
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const items = card.checklist || []
  const all = items.length > 0 && items.every(([k]) => checked[k])
  if (!items.length)
    return <p className="text-[13px] ink2 mt-2">🖨️ 排版稿已出{card.slot ? ` · 槽位 ${card.slot}` : ''}</p>
  return (
    <div className="mt-2.5 p-2.5 rounded-xl bg-white/45">
      <p className="text-[12.5px] muted mb-1.5">发布检查单（推送草稿箱后勾满 7 项登记）</p>
      <div className="grid grid-cols-1 gap-y-1">
        {items.map(([k, label]) => (
          <label key={k} className="flex items-center gap-2 text-[12.5px] cursor-pointer">
            <input type="checkbox" disabled={handled}
                   checked={!!checked[k]}
                   onChange={e => setChecked(s => ({ ...s, [k]: e.target.checked }))} />
            {label}
          </label>
        ))}
      </div>
      <button className="btn btn-sm btn-primary mt-2" disabled={handled || !all}
              onClick={() => onRegister?.(msgId, card.slot || '', checked)}>
        {all ? '登记已发布' : '勾满后可登记'}
      </button>
    </div>
  )
}
