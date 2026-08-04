import type { Ticket } from '../../types'
import TicketRow from './TicketRow'

export default function TicketList({ tickets, loading, tab, onSelect, onClaim, claimingId, selectedId }: {
  tickets: Ticket[]; loading: boolean; tab: string
  onSelect: (id: string) => void; onClaim: (id: string) => void; claimingId: string | null; selectedId?: string | null
}) {
  if (loading) {
    return (
      <div className="flex justify-center py-8">
        <div className="w-6 h-6 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (tickets.length === 0) {
    const empty: Record<string, string> = {
      unclaimed: 'No unclaimed tickets right now.',
      mine: "You haven't claimed any tickets yet.",
      all: 'Nothing in your queues.',
      resolved: 'No resolved tickets yet.',
    }
    return <div className="text-center py-12 text-sm text-gray-400">{empty[tab] || 'Nothing here.'}</div>
  }

  return (
    <div className="flex flex-col gap-3">
      {tickets.map(t => (
        <TicketRow
          key={t.id} ticket={t}
          onClick={() => onSelect(t.id)}
          onClaim={() => onClaim(t.id)}
          claiming={claimingId === t.id}
          selected={selectedId === t.id}
        />
      ))}
    </div>
  )
}
