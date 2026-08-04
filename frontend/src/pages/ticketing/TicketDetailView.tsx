import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/axios'
import { getTicket, claimTicket, replyToTicket, addTicketNote, resolveTicket, reopenTicket, reassignTicket, updateTicket } from '../../api/tickets'
import { ticketStatusBadge, ticketPriorityBadge, stageLabel } from '../../types'
import type { TicketPriority } from '../../types'
import TicketTimeline from './TicketTimeline'

type Panel = 'reply' | 'note' | 'reassign' | 'resolve' | null

const PRIORITIES: TicketPriority[] = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']

// Deterministic "what to do" guidance for the three system-detected sources
// only — these fire off a known DB condition so the right action never
// varies. AI escalations / manual / support-bot tickets are deliberately
// left out: the right action there genuinely varies per ticket.
const WHAT_TO_DO: Record<string, string> = {
  stuck_in_pipeline: 'Call the client and walk them through the candidates awaiting review and/or interview feedback. Once they’ve responded on everything outstanding, resolve this ticket — if anything is still pending, it’ll reopen automatically with the updated count.',
  ready_to_advance: 'The client already said they liked this candidate after the interview, but nothing has moved since. Call the client to confirm next steps (offer / documents) and push the mapping forward.',
  client_closure: 'Client and candidate have both agreed and documents are submitted. Get the client to review the documents and click "Final Hire" in their portal to close out the placement.',
}

function fmt(iso: string | null): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })
}

export default function TicketDetailView({ ticketId, ceId, onBack }: { ticketId: string; ceId: string; onBack: () => void }) {
  const qc = useQueryClient()
  const [panel, setPanel] = useState<Panel>(null)
  const [replyText, setReplyText] = useState('')
  const [noteText, setNoteText] = useState('')
  const [resolutionNote, setResolutionNote] = useState('')
  const [reassignTo, setReassignTo] = useState('')

  const { data: ticket, isLoading } = useQuery({
    queryKey: ['ticket', ticketId],
    queryFn: () => getTicket(ticketId),
    refetchInterval: 15000,
  })

  const { data: executives = [] } = useQuery({
    queryKey: ['ces-for-reassign'],
    queryFn: () => api.get('/admin/ces').then(r => r.data.data as { id: string; name: string; tier: string; is_active: boolean }[]),
    enabled: panel === 'reassign',
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['ticket', ticketId] })
    qc.invalidateQueries({ queryKey: ['ticket-timeline', ticketId] })
    qc.invalidateQueries({ queryKey: ['tickets'] })
    setPanel(null)
  }

  const claimMutation = useMutation({ mutationFn: () => claimTicket(ticketId, ceId), onSuccess: invalidate })
  const replyMutation = useMutation({
    mutationFn: () => replyToTicket(ticketId, ceId, replyText),
    onSuccess: () => { setReplyText(''); invalidate() },
  })
  const noteMutation = useMutation({
    mutationFn: () => addTicketNote(ticketId, ceId, noteText),
    onSuccess: () => { setNoteText(''); invalidate() },
  })
  const resolveMutation = useMutation({
    mutationFn: () => resolveTicket(ticketId, ceId, resolutionNote || undefined),
    onSuccess: () => { setResolutionNote(''); invalidate() },
  })
  const reopenMutation = useMutation({ mutationFn: () => reopenTicket(ticketId, ceId), onSuccess: invalidate })
  const reassignMutation = useMutation({
    mutationFn: () => reassignTicket(ticketId, reassignTo),
    onSuccess: () => { setReassignTo(''); invalidate() },
  })
  const priorityMutation = useMutation({
    mutationFn: (priority: string) => updateTicket(ticketId, { priority }),
    onSuccess: invalidate,
  })

  if (isLoading || !ticket) {
    return (
      <div className="flex justify-center py-12">
        <div className="w-6 h-6 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  const isMine = ticket.claimed_by === ceId
  const isInternal = ticket.channel === 'internal'
  const anyMutationPending = claimMutation.isPending || replyMutation.isPending || noteMutation.isPending
    || resolveMutation.isPending || reopenMutation.isPending || reassignMutation.isPending

  return (
    <div className="flex flex-col gap-3">
      <button onClick={onBack} className="lg:hidden text-xs text-gray-500 hover:text-gray-800 self-start">← Back to list</button>

      {/* Customer / alert snapshot */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        {isInternal ? (
          <>
            <p className="text-sm font-bold text-gray-900">{ticket.subject || 'Engineering alert'}</p>
            <p className="text-xs text-gray-400 mt-0.5">
              Fired {ticket.occurrence_count} time{ticket.occurrence_count === 1 ? '' : 's'} · {ticket.category_name}
            </p>
          </>
        ) : ticket.channel === 'client' ? (
          <>
            <p className="text-sm font-bold text-gray-900">{ticket.company_name || ticket.phone}</p>
            <p className="text-xs text-gray-400 mt-0.5">
              {ticket.client_job_title || 'Role not set'}
              {ticket.client_stage && <> · {stageLabel(ticket.client_stage)}</>}
            </p>
            <p className="text-xs text-gray-500 mt-1">
              {ticket.phone && <a href={`tel:${ticket.phone}`} className="text-gray-900 font-semibold hover:underline">{ticket.phone}</a>}
            </p>
          </>
        ) : (
          <>
            <p className="text-sm font-bold text-gray-900">{ticket.candidate_name || ticket.phone}</p>
            <p className="text-xs text-gray-500 mt-1">
              {ticket.phone && <a href={`tel:${ticket.phone}`} className="text-gray-900 font-semibold hover:underline">{ticket.phone}</a>}
            </p>
          </>
        )}

        {ticket.subject && !isInternal && <p className="text-xs text-gray-500 mt-2 bg-gray-50 rounded-lg px-2.5 py-1.5">{ticket.subject}</p>}

        {ticket.related_tickets.length > 0 && (
          <div className="mt-3 pt-3 border-t border-gray-100">
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-1">Previous tickets</p>
            {ticket.related_tickets.map(r => (
              <p key={r.id} className="text-[11px] text-gray-500">
                {stageLabel(r.status)} · {r.subject || r.source} · {fmt(r.created_at)}
              </p>
            ))}
          </div>
        )}
      </div>

      {/* What to do — deterministic guidance for system-detected tickets only */}
      {WHAT_TO_DO[ticket.source] && (
        <div className="bg-blue-50 border border-blue-200 rounded-xl p-4">
          <p className="text-[10px] font-semibold text-blue-500 uppercase tracking-wide mb-1">What to do</p>
          <p className="text-xs text-blue-900 leading-relaxed">{WHAT_TO_DO[ticket.source]}</p>
        </div>
      )}

      {/* Ticket meta */}
      <div className="bg-white rounded-xl border border-gray-200 p-4 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
        <div>
          <p className="text-gray-400">Status</p>
          <span className={`inline-block mt-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full ${ticketStatusBadge(ticket.status)}`}>
            {stageLabel(ticket.status)}
          </span>
        </div>
        <div>
          <p className="text-gray-400">Priority</p>
          <select
            value={ticket.priority}
            disabled={priorityMutation.isPending}
            onChange={e => priorityMutation.mutate(e.target.value)}
            className={`mt-0.5 text-[10px] font-medium px-2 py-0.5 rounded-full border-0 ${ticketPriorityBadge(ticket.priority)}`}
          >
            {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
        <div><p className="text-gray-400">Queue</p><p className="mt-0.5 text-gray-700">{ticket.queue_name}</p></div>
        <div><p className="text-gray-400">Category</p><p className="mt-0.5 text-gray-700">{ticket.category_name}</p></div>
        <div><p className="text-gray-400">Assigned to</p><p className="mt-0.5 text-gray-700">{ticket.claimed_by_name || 'Unclaimed'}</p></div>
        <div><p className="text-gray-400">Created</p><p className="mt-0.5 text-gray-700">{fmt(ticket.created_at)}</p></div>
      </div>

      {/* Timeline */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wide mb-2">Conversation</p>
        <TicketTimeline ticketId={ticketId} />
      </div>

      {/* Actions */}
      <div className="bg-white rounded-xl border border-gray-200 p-4">
        <div className="flex flex-wrap gap-2">
          {!ticket.claimed_by && (
            <button onClick={() => claimMutation.mutate()} disabled={anyMutationPending}
              className="px-3 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
              Claim
            </button>
          )}
          {isMine && !isInternal && (
            <button onClick={() => setPanel(panel === 'reply' ? null : 'reply')}
              className={`px-3 py-2 rounded-lg text-xs font-medium border ${panel === 'reply' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600'}`}>
              Reply
            </button>
          )}
          {isMine && (
            <button onClick={() => setPanel(panel === 'note' ? null : 'note')}
              className={`px-3 py-2 rounded-lg text-xs font-medium border ${panel === 'note' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600'}`}>
              Add Note
            </button>
          )}
          {isMine && ticket.status !== 'RESOLVED' && (
            <button onClick={() => setPanel(panel === 'resolve' ? null : 'resolve')}
              className={`px-3 py-2 rounded-lg text-xs font-medium border ${panel === 'resolve' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600'}`}>
              Resolve
            </button>
          )}
          {isMine && ticket.status === 'RESOLVED' && (
            <button onClick={() => reopenMutation.mutate()} disabled={anyMutationPending}
              className="px-3 py-2 rounded-lg border border-gray-300 text-gray-600 text-xs font-medium hover:bg-gray-50 disabled:opacity-50">
              Reopen
            </button>
          )}
          {ticket.claimed_by && (
            <button onClick={() => setPanel(panel === 'reassign' ? null : 'reassign')}
              className={`px-3 py-2 rounded-lg text-xs font-medium border ${panel === 'reassign' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600'}`}>
              Reassign
            </button>
          )}
        </div>

        {panel === 'reply' && (
          <div className="mt-3">
            <textarea rows={3} value={replyText} onChange={e => setReplyText(e.target.value)}
              placeholder="Message the customer over WhatsApp…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
            <button onClick={() => replyMutation.mutate()} disabled={!replyText.trim() || replyMutation.isPending}
              className="mt-2 px-4 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
              {replyMutation.isPending ? 'Sending…' : 'Send Reply'}
            </button>
          </div>
        )}

        {panel === 'note' && (
          <div className="mt-3">
            <textarea rows={3} value={noteText} onChange={e => setNoteText(e.target.value)}
              placeholder="Internal note — never visible to the customer…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
            <button onClick={() => noteMutation.mutate()} disabled={!noteText.trim() || noteMutation.isPending}
              className="mt-2 px-4 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
              {noteMutation.isPending ? 'Saving…' : 'Save Note'}
            </button>
          </div>
        )}

        {panel === 'resolve' && (
          <div className="mt-3">
            <textarea rows={2} value={resolutionNote} onChange={e => setResolutionNote(e.target.value)}
              placeholder="What was done? (optional)"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
            <button onClick={() => resolveMutation.mutate()} disabled={resolveMutation.isPending}
              className="mt-2 px-4 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
              {resolveMutation.isPending ? 'Resolving…' : 'Mark Resolved'}
            </button>
          </div>
        )}

        {panel === 'reassign' && (
          <div className="mt-3">
            <select value={reassignTo} onChange={e => setReassignTo(e.target.value)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400">
              <option value="">Select an executive…</option>
              {executives.filter(e => e.tier === 'bottom_end' && e.is_active && e.id !== ceId).map(e => (
                <option key={e.id} value={e.id}>{e.name}</option>
              ))}
            </select>
            <button onClick={() => reassignMutation.mutate()} disabled={!reassignTo || reassignMutation.isPending}
              className="mt-2 px-4 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
              {reassignMutation.isPending ? 'Reassigning…' : 'Reassign'}
            </button>
            {reassignMutation.isError && (
              <p className="text-xs text-red-600 mt-2">
                {(reassignMutation.error as any)?.response?.data?.detail ?? 'Could not reassign'}
              </p>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
