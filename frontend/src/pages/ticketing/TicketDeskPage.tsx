import { useState, useEffect, useRef } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import type { CEInfo } from '../CEPage'
import { getTickets, claimTicket } from '../../api/tickets'
import TicketList from './TicketList'
import TicketDetailView from './TicketDetailView'
import NewTicketModal from './NewTicketModal'

type Tab = 'unclaimed' | 'mine' | 'all' | 'resolved'

const TAB_LABELS: Record<Tab, string> = {
  unclaimed: 'Unclaimed', mine: 'My Tickets', all: 'All Visible', resolved: 'Resolved',
}

export default function TicketDeskPage({ ceId, ceInfo }: { ceId: string; ceInfo: CEInfo }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<Tab>('unclaimed')
  const [selectedTicketId, setSelectedTicketId] = useState<string | null>(null)
  const [showNewTicket, setShowNewTicket] = useState(false)
  const [claimingId, setClaimingId] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const { data: listResp, isLoading } = useQuery({
    queryKey: ['tickets', tab, ceId],
    queryFn: () => getTickets({ ce_id: ceId, tab }),
    refetchInterval: 30000,
  })
  const tickets = listResp?.data ?? []

  // In-page-only notification: dedicated poll on unclaimed-ticket ids, always
  // mounted at this shell level so it keeps firing even while a ticket is
  // open in detail view, not just while the list tab is visible.
  const { data: unclaimedIds = [] } = useQuery({
    queryKey: ['tickets-unclaimed-ids', ceId],
    queryFn: () => getTickets({ ce_id: ceId, tab: 'unclaimed', page_size: 200 }).then(r => r.data.map(t => t.id)),
    refetchInterval: 15000,
  })
  const prevIds = useRef<Set<string> | null>(null)
  useEffect(() => {
    const idSet = new Set(unclaimedIds)
    if (prevIds.current) {
      const newOnes = unclaimedIds.filter(id => !prevIds.current!.has(id))
      if (newOnes.length > 0) {
        setToast(`${newOnes.length} new ticket${newOnes.length > 1 ? 's' : ''} in your queues`)
        qc.invalidateQueries({ queryKey: ['tickets'] })
      }
    }
    prevIds.current = idSet
  }, [unclaimedIds, qc])

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 6000)
    return () => clearTimeout(t)
  }, [toast])

  const claimMutation = useMutation({
    mutationFn: (id: string) => claimTicket(id, ceId),
    onMutate: (id: string) => setClaimingId(id),
    onSettled: () => setClaimingId(null),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tickets'] }),
  })

  return (
    <div className="w-full lg:max-w-6xl mx-auto min-h-dvh bg-[#f4f6fb]">
      <div className="bg-[#0d2b5e] px-5 lg:px-8 pt-8 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-white/50 text-xs font-semibold uppercase tracking-widest mb-1">HireLyra</p>
            <h1 className="text-white text-lg font-bold">{ceInfo.name}</h1>
            <p className="text-white/50 text-xs mt-0.5">
              Ticket Desk {unclaimedIds.length > 0 && `· ${unclaimedIds.length} unclaimed`}
            </p>
          </div>
          <button
            onClick={() => setShowNewTicket(true)}
            className="shrink-0 px-3 py-2 rounded-lg border border-white/20 text-white text-xs font-semibold hover:bg-white/10"
          >
            + New Ticket
          </button>
        </div>
      </div>

      {toast && (
        <div className="mx-4 lg:mx-8 -mt-2 relative z-10">
          <div className="bg-amber-100 border border-amber-200 text-amber-800 text-xs font-medium px-3 py-2 rounded-lg flex items-center justify-between">
            <span>{toast}</span>
            <button onClick={() => setToast(null)} className="text-amber-500 hover:text-amber-700">✕</button>
          </div>
        </div>
      )}

      {/* Tab bar: hidden on desktop once a ticket is open in the detail pane
          only for the mobile single-pane flow — on lg+ the list column stays
          visible at all times, so tabs stay visible too. */}
      <div className={`px-4 lg:px-8 mt-4 ${selectedTicketId ? 'lg:block hidden' : ''}`}>
        <div className="flex bg-white rounded-xl border border-gray-200 p-1 gap-1 lg:max-w-md">
          {(Object.keys(TAB_LABELS) as Tab[]).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`flex-1 py-2 rounded-lg text-[11px] font-semibold transition-colors ${tab === t ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
      </div>

      <div className="px-4 lg:px-8 py-4 lg:grid lg:grid-cols-[380px_1fr] lg:gap-4 lg:items-start">
        {/* List pane: on mobile, swaps out entirely once a ticket is selected;
            on desktop it's always visible in its own column. */}
        <div className={selectedTicketId ? 'hidden lg:block' : ''}>
          <TicketList
            tickets={tickets} loading={isLoading} tab={tab}
            onSelect={setSelectedTicketId}
            onClaim={id => claimMutation.mutate(id)}
            claimingId={claimingId}
            selectedId={selectedTicketId}
          />
        </div>

        {/* Detail pane: only rendered once something's selected on mobile;
            on desktop, an empty-state placeholder fills the column instead
            of leaving it blank while nothing's chosen. */}
        {selectedTicketId ? (
          <div className="mt-3 lg:mt-0">
            <TicketDetailView ticketId={selectedTicketId} ceId={ceId} onBack={() => setSelectedTicketId(null)} />
          </div>
        ) : (
          <div className="hidden lg:flex items-center justify-center h-64 rounded-xl border border-dashed border-gray-300 text-sm text-gray-400">
            Select a ticket to view details
          </div>
        )}
      </div>

      {showNewTicket && (
        <NewTicketModal
          ceId={ceId}
          onClose={() => setShowNewTicket(false)}
          onCreated={() => { setShowNewTicket(false); qc.invalidateQueries({ queryKey: ['tickets'] }) }}
        />
      )}
    </div>
  )
}
