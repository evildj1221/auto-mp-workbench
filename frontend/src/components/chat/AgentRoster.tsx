import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import type { PersonaInfo } from '../../api/client'

/** 同事栏数据（8s 轻轮询；忙碌灯来自后端任务/Agent 状态映射） */
export function usePersonas() {
  return useQuery({
    queryKey: ['chat-personas'],
    queryFn: () => api.get<{ date: string; personas: PersonaInfo[] }>('/api/chat/personas'),
    refetchInterval: 8000,
  })
}

/** 编辑部同事栏：点花名 = 在输入框 @点名 */
export default function AgentRoster({ onMention }: { onMention: (name: string) => void }) {
  const { data } = usePersonas()
  return (
    <aside className="w-44 flex-none glass card-pad flex flex-col gap-1.5 overflow-auto">
      <h3 className="section-title mb-1">编辑部</h3>
      {(data?.personas || []).map(p => (
        <button key={p.id} onClick={() => onMention(p.name)} title={p.duty}
                className="flex items-center gap-2 px-2 py-1.5 rounded-xl text-left hover:bg-white/40 transition-all">
          <span className="chat-avatar flex-none" style={{ background: p.color }}>{p.name[0]}</span>
          <span className="min-w-0">
            <span className="flex items-center gap-1.5">
              <span className="text-[13px] font-medium">{p.name}</span>
              {p.busy && <span className="lamp run" title="正在干活" />}
            </span>
            <span className="muted block truncate">{p.role}</span>
          </span>
        </button>
      ))}
    </aside>
  )
}
