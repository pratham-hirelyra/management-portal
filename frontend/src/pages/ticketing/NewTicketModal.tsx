import { useState, useEffect } from 'react'
import { useQuery, useMutation } from '@tanstack/react-query'
import { getClients } from '../../api/clients'
import { getCandidates } from '../../api/candidates'
import { getTicketQueues, getTicketCategories, createTicket } from '../../api/tickets'

type Channel = 'client' | 'candidate'
interface Picked { id: string; phone: string; name: string }

export default function NewTicketModal({ ceId, onClose, onCreated }: {
  ceId: string; onClose: () => void; onCreated: () => void
}) {
  const [channel, setChannel] = useState<Channel>('client')
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [picked, setPicked] = useState<Picked | null>(null)
  const [queueId, setQueueId] = useState('')
  const [categoryId, setCategoryId] = useState('')
  const [subject, setSubject] = useState('')
  const [reason, setReason] = useState('')

  const { data: queues = [] } = useQuery({ queryKey: ['ticket-queues'], queryFn: getTicketQueues })
  const { data: categories = [] } = useQuery({
    queryKey: ['ticket-categories', queueId],
    queryFn: () => getTicketCategories(queueId),
    enabled: !!queueId,
  })

  // Auto-search as you type (debounced) — Enter still works too, but nothing
  // should require it since there's no visible cue that it's needed.
  useEffect(() => {
    const t = setTimeout(() => setSubmitted(q), 300)
    return () => clearTimeout(t)
  }, [q])

  const { data: clientResults, isFetching: searchingClients } = useQuery({
    queryKey: ['new-ticket-clients', submitted],
    queryFn: () => getClients(undefined, submitted),
    enabled: channel === 'client' && submitted.length >= 2,
  })
  const { data: candidateResults, isFetching: searchingCandidates } = useQuery({
    queryKey: ['new-ticket-candidates', submitted],
    queryFn: () => getCandidates({ search: submitted }),
    enabled: channel === 'candidate' && submitted.length >= 2,
  })
  const searching = channel === 'client' ? searchingClients : searchingCandidates
  const resultCount = channel === 'client' ? (clientResults?.data?.length ?? 0) : (candidateResults?.candidates?.length ?? 0)

  const createMutation = useMutation({
    mutationFn: () => {
      const queue = queues.find(x => x.id === queueId)
      const category = categories.find(x => x.id === categoryId)
      if (!picked || !queue || !category) throw new Error('Missing required fields')
      if (!picked.phone) throw new Error(`${channel === 'client' ? 'This client' : 'This candidate'} has no phone number on file`)
      return createTicket({
        phone: picked.phone, channel, queue_code: queue.code, category_code: category.code,
        subject: subject || undefined, reason: reason || undefined,
        client_id: channel === 'client' ? picked.id : undefined,
        candidate_id: channel === 'candidate' ? picked.id : undefined,
        created_by_ce_id: ceId,
      })
    },
    onSuccess: onCreated,
  })

  const errorMessage = createMutation.isError
    ? ((createMutation.error as any)?.response?.data?.detail ?? (createMutation.error as Error)?.message ?? 'Could not create the ticket — try again')
    : null

  const canSubmit = !!picked && !!queueId && !!categoryId

  const missingSteps: string[] = []
  if (!picked) missingSteps.push(channel === 'client' ? 'pick a client' : 'pick a candidate')
  if (!queueId) missingSteps.push('select a queue')
  if (!categoryId) missingSteps.push('select a category')

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5 max-h-[85vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900 mb-3">New Ticket</p>

        <div className="flex bg-gray-100 rounded-lg p-1 gap-1 mb-3">
          <button onClick={() => { setChannel('client'); setPicked(null) }}
            className={`flex-1 py-1.5 rounded-md text-xs font-semibold ${channel === 'client' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'}`}>
            Client
          </button>
          <button onClick={() => { setChannel('candidate'); setPicked(null) }}
            className={`flex-1 py-1.5 rounded-md text-xs font-semibold ${channel === 'candidate' ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500'}`}>
            Candidate
          </button>
        </div>

        {!picked ? (
          <>
            <input type="text" placeholder={`Search ${channel === 'client' ? 'company or POC' : 'candidate name'}…`}
              value={q} onChange={e => setQ(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && setSubmitted(q)}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
            <div className="mt-2 flex flex-col gap-1 max-h-40 overflow-y-auto">
              {channel === 'client' && clientResults?.data?.map(c => (
                <button key={c.id} onClick={() => setPicked({ id: c.id, phone: c.poc_phone, name: c.company_name })}
                  className="text-left px-2.5 py-1.5 rounded-lg hover:bg-gray-50 text-xs">
                  <span className="font-medium text-gray-800">{c.company_name}</span>
                  <span className="text-gray-400"> · {c.poc_phone || 'No phone'}</span>
                </button>
              ))}
              {channel === 'candidate' && candidateResults?.candidates?.map(c => (
                <button key={c.id} onClick={() => setPicked({ id: c.id, phone: c.phone, name: c.name })}
                  className="text-left px-2.5 py-1.5 rounded-lg hover:bg-gray-50 text-xs">
                  <span className="font-medium text-gray-800">{c.name}</span>
                  <span className="text-gray-400"> · {c.phone}</span>
                </button>
              ))}
            </div>
            <p className="text-[11px] text-gray-400 mt-1">
              {q.trim().length < 2 ? 'Type at least 2 characters to search'
                : searching ? 'Searching…'
                : resultCount === 0 ? `No ${channel === 'client' ? 'clients' : 'candidates'} found for "${submitted}"`
                : null}
            </p>
          </>
        ) : (
          <div className="bg-gray-50 rounded-lg px-3 py-2 flex items-center justify-between">
            <div>
              <p className="text-xs font-semibold text-gray-800">{picked.name}</p>
              <p className="text-[11px] text-gray-400">{picked.phone}</p>
            </div>
            <button onClick={() => setPicked(null)} className="text-[11px] text-blue-600 hover:underline">Change</button>
          </div>
        )}

        <label className="block text-[11px] font-semibold text-gray-500 mt-3 mb-1">Queue *</label>
        <select value={queueId} onChange={e => { setQueueId(e.target.value); setCategoryId('') }}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400">
          <option value="">Select queue…</option>
          {queues.map(qu => <option key={qu.id} value={qu.id}>{qu.name}</option>)}
        </select>

        <label className="block text-[11px] font-semibold text-gray-500 mt-2 mb-1">Category *</label>
        <select value={categoryId} onChange={e => setCategoryId(e.target.value)} disabled={!queueId}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400 disabled:bg-gray-50">
          <option value="">{queueId ? 'Select category…' : 'Select a queue first'}</option>
          {categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
        </select>

        <input type="text" placeholder="Subject (optional)" value={subject} onChange={e => setSubject(e.target.value)}
          className="w-full mt-2 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />

        <textarea rows={2} placeholder="Details (optional)" value={reason} onChange={e => setReason(e.target.value)}
          className="w-full mt-2 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />

        {errorMessage && (
          <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2 mt-3">{errorMessage}</p>
        )}
        {!errorMessage && missingSteps.length > 0 && (
          <p className="text-xs text-gray-400 mt-3">To create this ticket, {missingSteps.join(', ')}.</p>
        )}

        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={() => createMutation.mutate()} disabled={!canSubmit || createMutation.isPending}
            className="flex-1 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-semibold hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400">
            {createMutation.isPending ? 'Creating…' : 'Create Ticket'}
          </button>
        </div>
      </div>
    </div>
  )
}
