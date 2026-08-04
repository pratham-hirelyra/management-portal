import type { Ticket } from '../../types'
import { ticketStatusBadge, ticketPriorityBadge, stageLabel } from '../../types'

function relTime(iso: string): string {
  const mins = Math.floor((Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

export default function TicketRow({ ticket, onClick, onClaim, claiming, selected }: {
  ticket: Ticket; onClick: () => void; onClaim: () => void; claiming: boolean; selected?: boolean
}) {
  const title = ticket.company_name || ticket.candidate_name || ticket.subject || (ticket.channel === 'internal' ? 'Engineering alert' : ticket.phone) || 'Ticket'

  return (
    <div
      className={`bg-white rounded-xl border p-4 cursor-pointer transition-colors ${
        selected ? 'border-gray-900 ring-1 ring-gray-900' : 'border-gray-200 hover:border-gray-300'
      }`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{title}</p>
          <p className="text-xs text-gray-400 mt-0.5">
            {ticket.subject && (ticket.company_name || ticket.candidate_name) ? ticket.subject + ' · ' : ''}
            {ticket.phone || 'No phone'}
          </p>
          <div className="flex items-center gap-1.5 mt-1.5 flex-wrap">
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${ticketStatusBadge(ticket.status)}`}>
              {stageLabel(ticket.status)}
            </span>
            <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full ${ticketPriorityBadge(ticket.priority)}`}>
              {ticket.priority}
            </span>
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
              {ticket.queue_name}
            </span>
            {ticket.occurrence_count > 1 && (
              <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-red-100 text-red-600">
                ×{ticket.occurrence_count}
              </span>
            )}
          </div>
          <p className="text-[10px] text-gray-400 mt-1">
            {ticket.category_name} · {ticket.claimed_by_name ? `Claimed by ${ticket.claimed_by_name}` : 'Unclaimed'} · {relTime(ticket.last_activity_at)}
          </p>
        </div>
        {!ticket.claimed_by && (
          <button
            onClick={e => { e.stopPropagation(); onClaim() }}
            disabled={claiming}
            className="shrink-0 px-3 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50"
          >
            Claim
          </button>
        )}
      </div>
    </div>
  )
}
