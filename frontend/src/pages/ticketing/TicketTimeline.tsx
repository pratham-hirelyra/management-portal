import { useQuery } from '@tanstack/react-query'
import { getTicketTimeline } from '../../api/tickets'

const TYPE_LABELS: Record<string, string> = {
  created: 'Ticket created',
  claimed: 'Claimed',
  reassigned: 'Reassigned',
  resolved: 'Resolved',
  reopened: 'Reopened',
  customer_replied: 'Customer replied',
  system: 'System',
}

function fmtTime(iso: string): string {
  return new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

export default function TicketTimeline({ ticketId }: { ticketId: string }) {
  const { data: entries = [], isLoading } = useQuery({
    queryKey: ['ticket-timeline', ticketId],
    queryFn: () => getTicketTimeline(ticketId),
    refetchInterval: 10000,
  })

  if (isLoading) {
    return <div className="flex justify-center py-6"><div className="w-5 h-5 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" /></div>
  }

  if (entries.length === 0) {
    return <p className="text-xs text-gray-400 text-center py-6">No activity yet.</p>
  }

  return (
    <div className="flex flex-col gap-2">
      {entries.map(e => {
        if (e.kind === 'whatsapp') {
          const outbound = e.direction === 'outbound'
          return (
            <div key={e.id} className={`flex ${outbound ? 'justify-end' : 'justify-start'}`}>
              <div className={`max-w-[80%] rounded-xl px-3 py-2 text-xs ${outbound ? 'bg-[#0d2b5e] text-white' : 'bg-gray-100 text-gray-800'}`}>
                <p className="whitespace-pre-wrap break-words">{e.body}</p>
                <p className={`text-[9px] mt-1 ${outbound ? 'text-white/60' : 'text-gray-400'}`}>{fmtTime(e.created_at)}</p>
              </div>
            </div>
          )
        }

        if (e.type === 'note') {
          return (
            <div key={e.id} className="bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              <p className="text-[10px] font-semibold text-amber-700 uppercase tracking-wide">Internal note{e.actor_label ? ` · ${e.actor_label}` : ''}</p>
              <p className="text-xs text-amber-900 mt-0.5 whitespace-pre-wrap break-words">{e.body}</p>
              <p className="text-[9px] text-amber-500 mt-1">{fmtTime(e.created_at)}</p>
            </div>
          )
        }

        return (
          <div key={e.id} className="flex items-center gap-2 py-1">
            <span className="text-[10px] text-gray-400 whitespace-nowrap">{fmtTime(e.created_at)}</span>
            <span className="h-px flex-1 bg-gray-100" />
            <span className="text-[11px] text-gray-500">
              {TYPE_LABELS[e.type || ''] || e.type}{e.actor_label ? ` · ${e.actor_label}` : ''}
              {e.body ? ` — ${e.body}` : ''}
            </span>
          </div>
        )
      })}
    </div>
  )
}
