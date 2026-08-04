import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'
import TicketDeskPage from './ticketing/TicketDeskPage'

export interface CEInfo { id: string; name: string; phone: string | null; tier: string; daily_target: number; is_active: boolean }
interface PhoneEntry { number: string; source?: string }
interface Assignment {
  assignment_id: string; client_id: string; call_date: string; status: 'pending' | 'called'
  sentiment: 'positive' | 'negative' | 'wrong_poc' | 'wrong_company' | null; comment: string | null; called_at: string | null; assigned_at: string
  ce_id: string | null; claimed_at: string | null
  company_name: string; poc_name: string | null; poc_phone: string | null; job_title: string | null
  client_stage: string; segment: string | null; industry: string | null; location: string | null
  last_engagement_at: string | null; no_pickup_count: number; last_attempt_at: string | null
  phone_numbers: (PhoneEntry | string)[] | null; dnd_phones: string[] | null
}

function phoneEntryNumber(p: PhoneEntry | string): string | null {
  return typeof p === 'string' ? p : p.number ?? null
}
function last10(phone: string): string {
  return phone.replace(/\D/g, '').slice(-10)
}

// All distinct, non-opted-out numbers found for this client — poc_phone first
// (deduped against it), so a CE can see every candidate contact, not just
// whichever one happens to be the current poc_phone (which may have been
// silently overwritten by whoever last pressed "Interested" on WhatsApp).
function otherFoundNumbers(a: Assignment): { number: string; source?: string }[] {
  const dndList = Array.isArray(a.dnd_phones) ? a.dnd_phones : []
  const dnd = new Set(dndList.map(last10))
  const pocLast10 = a.poc_phone ? last10(a.poc_phone) : null
  const seen = new Set(pocLast10 ? [pocLast10] : [])
  const out: { number: string; source?: string }[] = []
  const phoneList = Array.isArray(a.phone_numbers) ? a.phone_numbers : []
  for (const entry of phoneList) {
    const num = phoneEntryNumber(entry)
    if (!num) continue
    const key = last10(num)
    if (!key || seen.has(key) || dnd.has(key)) continue
    seen.add(key)
    out.push({ number: num, source: typeof entry === 'object' ? entry.source : undefined })
  }
  return out
}

function relDays(iso: string | null): string {
  if (!iso) return 'Never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return `${days}d ago`
}
interface Dashboard {
  total_called: number; positive: number; negative: number; wrong_poc: number; wrong_company: number
  onboarded: number; today_pending: number; claimed_today: number; total_claimed: number
}

const PIN_STORAGE_PREFIX = 'ce_pin_ok_'

// Which pool tier this assignment came from — matches the priority order the
// shared unclaimed queue and Claim Batch are sorted by: Job Post Interested >
// Employer Lead Interested > CA Network Interested > Job Post Reachout Sent.
function sourceTag(segment: string | null, stage: string): string {
  if (segment === 'active_job_post' && stage === 'interested') return 'Job Post · Interested'
  if (segment === 'employer_lead' && stage === 'interested') return 'Employer Lead · Interested'
  if (segment === 'ca_network' && stage === 'interested') return 'CA Network · Interested'
  if (segment === 'active_job_post' && stage === 'reachout_sent') return 'Job Post · Reachout Sent'
  return `${segment || 'Unknown'} · ${stage}`
}

function StatCard({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-1.5 py-2.5 text-center">
      <p className="text-lg font-bold text-gray-900">{value}</p>
      <p className="text-[9px] text-gray-500 mt-0.5 uppercase tracking-wide leading-tight">{label}</p>
    </div>
  )
}

function CallModal({ assignment, onClose, onSave, saving }: {
  assignment: Assignment; onClose: () => void
  onSave: (sentiment: 'positive' | 'negative' | 'wrong_poc' | 'wrong_company', comment: string) => void
  saving: boolean
}) {
  const [sentiment, setSentiment] = useState<'positive' | 'negative' | 'wrong_poc' | 'wrong_company' | null>(null)
  const [comment, setComment] = useState('')
  const others = otherFoundNumbers(assignment)

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">{assignment.company_name}</p>
        <p className="text-xs text-gray-400 mt-0.5">{assignment.poc_name} · {assignment.poc_phone || 'No phone'}</p>
        {others.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-1">
            Other numbers found: {others.map(o => o.number).join(', ')}
          </p>
        )}

        <div className="flex gap-2 mt-4">
          <button onClick={() => setSentiment('positive')}
            className={`flex-1 py-2.5 rounded-lg text-sm font-semibold border transition-colors ${
              sentiment === 'positive' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}>
            Positive
          </button>
          <button onClick={() => setSentiment('negative')}
            className={`flex-1 py-2.5 rounded-lg text-sm font-semibold border transition-colors ${
              sentiment === 'negative' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}>
            Negative
          </button>
        </div>
        <div className="flex gap-2 mt-2">
          <button onClick={() => setSentiment('wrong_poc')}
            className={`flex-1 py-2.5 rounded-lg text-sm font-semibold border transition-colors ${
              sentiment === 'wrong_poc' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}>
            Wrong POC
          </button>
          <button onClick={() => setSentiment('wrong_company')}
            className={`flex-1 py-2.5 rounded-lg text-sm font-semibold border transition-colors ${
              sentiment === 'wrong_company' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
            }`}>
            Wrong Company
          </button>
        </div>
        <p className="text-[10px] text-gray-400 mt-1">
          Wrong POC — right company, stale contact/number. Wrong Company — scraper matched the wrong business entirely.
        </p>

        <textarea
          className="w-full mt-3 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400"
          rows={3}
          placeholder={sentiment === 'wrong_poc' || sentiment === 'wrong_company' ? "What's actually wrong? e.g. \"disconnected\", \"person no longer here\" (optional)" : "Comment (optional)"}
          value={comment} onChange={e => setComment(e.target.value)}
        />

        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={() => sentiment && onSave(sentiment, comment)} disabled={!sentiment || saving}
            className="flex-1 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-semibold hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400">
            {saving ? 'Saving…' : 'Mark Called'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ShiftModal({ assignment, onClose, onSave, saving }: {
  assignment: Assignment; onClose: () => void; onSave: (date: string) => void; saving: boolean
}) {
  const tomorrow = new Date(Date.now() + 86400000).toISOString().slice(0, 10)
  const [date, setDate] = useState(tomorrow)
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">Shift {assignment.company_name}</p>
        <p className="text-xs text-gray-400 mt-0.5 mb-4">Pick the date to call them instead.</p>
        <input type="date" value={date} min={tomorrow}
          onChange={e => setDate(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400" />
        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={() => onSave(date)} disabled={saving}
            className="flex-1 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-semibold hover:bg-gray-800 disabled:bg-gray-200">
            {saving ? 'Saving…' : 'Shift'}
          </button>
        </div>
      </div>
    </div>
  )
}

function UpdatePocModal({ assignment, onClose, onSave, saving }: {
  assignment: Assignment; onClose: () => void; onSave: (phone: string) => void; saving: boolean
}) {
  const [phone, setPhone] = useState('')
  const others = otherFoundNumbers(assignment)
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">Update POC — {assignment.company_name}</p>
        <p className="text-xs text-gray-400 mt-0.5">
          Current: {assignment.poc_name || '—'} · {assignment.poc_phone || 'No phone'}
        </p>
        {others.length > 0 && (
          <p className="text-[11px] text-gray-400 mt-1">
            Other numbers found: {others.map(o => o.number).join(', ')}
          </p>
        )}
        <input
          type="tel" placeholder="New POC phone number" autoFocus
          value={phone} onChange={e => setPhone(e.target.value)}
          className="w-full mt-3 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400"
        />
        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={() => phone && onSave(phone)} disabled={!phone || saving}
            className="flex-1 py-2.5 rounded-lg bg-gray-900 text-white text-sm font-semibold hover:bg-gray-800 disabled:bg-gray-200 disabled:text-gray-400">
            {saving ? 'Saving…' : 'Save Number'}
          </button>
        </div>
      </div>
    </div>
  )
}

const TERMINAL_LABELS: Record<string, string> = {
  onboarded:    'Already Onboarded',
  disqualified: 'Disqualified',
  churned:      'Churned',
}

function ClaimingOverlay({ message }: { message: string }) {
  return (
    <div className="fixed inset-0 bg-white/90 z-[60] flex flex-col items-center justify-center gap-3">
      <div className="w-10 h-10 border-4 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" />
      <p className="text-sm font-medium text-gray-700">{message}</p>
    </div>
  )
}

function AssignmentRow({ a, unclaimed, onClaim, claiming, onCall, onShift, onNoPickup, onUpdatePoc, onSendReachout, noPickupSaving, reachoutSaving }: {
  a: Assignment; unclaimed: boolean
  onClaim: () => void; claiming: boolean
  onCall: () => void; onShift: () => void; onNoPickup: () => void; onUpdatePoc: () => void; onSendReachout: () => void
  noPickupSaving: boolean; reachoutSaving: boolean
}) {
  const terminal = a.status === 'pending' ? TERMINAL_LABELS[a.client_stage] : undefined
  const others = otherFoundNumbers(a)

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{a.company_name}</p>
          <p className="text-xs text-gray-500 mt-0.5">
            {a.poc_name || '—'} ·{' '}
            {a.poc_phone ? <a href={`tel:${a.poc_phone}`} className="text-gray-900 font-semibold hover:underline">{a.poc_phone}</a> : 'No phone'}
            {!unclaimed && (
              <>
                {' '}
                <button onClick={onUpdatePoc} className="text-[11px] text-blue-600 hover:underline font-medium">Update number</button>
              </>
            )}
          </p>
          {others.length > 0 && (
            <p className="text-[11px] text-gray-400 mt-0.5">
              Also found:{' '}
              {others.map((o, i) => (
                <span key={o.number}>
                  {i > 0 && ', '}
                  <a href={`tel:${o.number}`} className="text-gray-600 hover:underline">{o.number}</a>
                  {o.source && <span className="text-gray-300"> ({o.source})</span>}
                </span>
              ))}
            </p>
          )}
          <p className="text-xs text-gray-400 mt-0.5">{a.job_title || 'Role not set'}</p>
          <p className="text-xs text-gray-400 mt-0.5">{a.location || 'Location not on file'}</p>
          <div className="flex items-center gap-1.5 mt-1.5">
            <span className="inline-block text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
              {sourceTag(a.segment, a.client_stage)}
            </span>
            <span className="text-[10px] text-gray-400">· Last engaged {relDays(a.last_engagement_at)}</span>
          </div>
          {!unclaimed && a.status === 'pending' && a.no_pickup_count > 0 && (
            <span className="inline-block mt-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
              No pickup ×{a.no_pickup_count} — retrying
            </span>
          )}
        </div>
        {terminal ? (
          <span className="shrink-0 text-[10px] font-medium px-2 py-1 rounded-full bg-gray-100 text-gray-500">{terminal}</span>
        ) : a.status === 'called' ? (
          <span className="shrink-0 text-[10px] font-medium px-2 py-1 rounded-full bg-gray-100 text-gray-600">
            {a.sentiment === 'positive' ? 'Called · Positive'
              : a.sentiment === 'wrong_poc' ? 'Called · Wrong POC'
              : a.sentiment === 'wrong_company' ? 'Called · Wrong Company'
              : 'Called · Negative'}
          </span>
        ) : (
          <span className="shrink-0 text-[10px] font-medium px-2 py-1 rounded-full bg-gray-100 text-gray-600">
            {a.call_date < new Date().toISOString().slice(0, 10) ? 'Overdue' : 'Due Today'}
          </span>
        )}
      </div>
      {a.status === 'called' && a.called_at && (
        <p className="text-[10px] text-gray-400 mt-2">
          Called {new Date(a.called_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
        </p>
      )}
      {a.comment && <p className="text-xs text-gray-500 mt-2 bg-gray-50 rounded-lg px-2.5 py-1.5">"{a.comment}"</p>}

      {unclaimed ? (
        <div className="mt-3">
          <button onClick={onClaim} disabled={claiming}
            className="w-full py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
            {claiming ? 'Claiming…' : 'Claim'}
          </button>
        </div>
      ) : a.status === 'pending' && !terminal && (
        <div className="mt-3 flex flex-col gap-2">
          <div className="flex gap-2">
            <button onClick={onCall} className="flex-1 py-2 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800">
              Mark Called
            </button>
            <button onClick={onNoPickup} disabled={noPickupSaving}
              className="flex-1 py-2 rounded-lg border border-gray-300 text-gray-600 text-xs font-medium hover:bg-gray-50 disabled:opacity-50">
              No Pickup
            </button>
            <button onClick={onShift} className="flex-1 py-2 rounded-lg border border-gray-300 text-gray-600 text-xs font-medium hover:bg-gray-50">
              Shift Date
            </button>
          </div>
          <button onClick={onSendReachout} disabled={reachoutSaving}
            className="w-full py-2 rounded-lg border border-blue-200 text-blue-700 bg-blue-50 text-xs font-medium hover:bg-blue-100 disabled:opacity-50">
            {reachoutSaving ? 'Sending…' : 'Send Reachout'}
          </button>
        </div>
      )}
    </div>
  )
}

function CEDashboard({ ceId, ceInfo }: { ceId: string; ceInfo: CEInfo }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'unclaimed' | 'mine' | 'history' | 'search'>('unclaimed')
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [callTarget, setCallTarget] = useState<Assignment | null>(null)
  const [shiftTarget, setShiftTarget] = useState<Assignment | null>(null)
  const [updatePocTarget, setUpdatePocTarget] = useState<Assignment | null>(null)
  const [claimingId, setClaimingId] = useState<string | null>(null)
  const [reachoutId, setReachoutId] = useState<string | null>(null)
  const [reachoutMessage, setReachoutMessage] = useState<string | null>(null)
  const [sweepMessage, setSweepMessage] = useState<string | null>(null)
  const [batchClaiming, setBatchClaiming] = useState(false)
  const [batchMessage, setBatchMessage] = useState<string | null>(null)

  const { data: dashboard } = useQuery({
    queryKey: ['ce-dashboard', ceId],
    queryFn: () => api.get(`/ce/${ceId}/dashboard`).then(r => r.data.data as Dashboard),
  })

  const { data: unclaimed = [], isLoading: unclaimedLoading } = useQuery({
    queryKey: ['ce-queue-unclaimed', ceId],
    queryFn: () => api.get(`/ce/${ceId}/queue`, { params: { view: 'unclaimed' } }).then(r => r.data.data as Assignment[]),
    enabled: tab === 'unclaimed',
    refetchInterval: tab === 'unclaimed' ? 30000 : false,
  })

  const { data: mine = [], isLoading: mineLoading } = useQuery({
    queryKey: ['ce-queue-mine', ceId],
    queryFn: () => api.get(`/ce/${ceId}/queue`, { params: { view: 'mine' } }).then(r => r.data.data as Assignment[]),
    enabled: tab === 'mine',
  })

  const { data: history = [], isLoading: historyLoading } = useQuery({
    queryKey: ['ce-history', ceId],
    queryFn: () => api.get(`/ce/${ceId}/history`).then(r => r.data.data as Assignment[]),
    enabled: tab === 'history',
  })

  const { data: searchResults = [], isFetching: searching } = useQuery({
    queryKey: ['ce-search', ceId, submitted],
    queryFn: () => api.get(`/ce/${ceId}/search`, { params: { q: submitted } }).then(r => r.data.data as Assignment[]),
    enabled: tab === 'search' && submitted.length >= 2,
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['ce-dashboard', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-queue-unclaimed', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-queue-mine', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-history', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-search', ceId] })
  }

  const claimMutation = useMutation({
    mutationFn: (id: string) => api.post(`/ce/${ceId}/assignments/${id}/claim`).then(r => r.data),
    onMutate: (id: string) => setClaimingId(id),
    onSettled: () => setClaimingId(null),
    onSuccess: invalidate,
  })

  const claimBatchMutation = useMutation({
    mutationFn: () => api.post(`/ce/${ceId}/assignments/claim-batch`).then(r => r.data.data as { claimed: Assignment[]; count: number }),
    onMutate: () => { setBatchClaiming(true); setBatchMessage(null) },
    onSettled: () => setBatchClaiming(false),
    onSuccess: (data) => {
      setBatchMessage(
        data.count > 0
          ? `Claimed ${data.count} client${data.count === 1 ? '' : 's'}`
          : 'Nothing left in the shared queue to claim right now'
      )
      invalidate()
    },
    onError: () => setBatchMessage('Could not claim a batch — try again'),
  })

  const reachoutMutation = useMutation({
    mutationFn: (id: string) => api.post(`/ce/${ceId}/assignments/${id}/send-reachout`).then(r => r.data),
    onMutate: (id: string) => { setReachoutId(id); setReachoutMessage(null) },
    onSettled: () => setReachoutId(null),
    onSuccess: () => { setReachoutMessage('Reachout sent'); invalidate() },
    onError: (e: any) => setReachoutMessage(e?.response?.data?.detail ?? 'Send failed'),
  })

  const callMutation = useMutation({
    mutationFn: ({ id, sentiment, comment }: { id: string; sentiment: string; comment: string }) =>
      api.post(`/ce/${ceId}/assignments/${id}/call`, { sentiment, comment: comment || null }).then(r => r.data),
    onSuccess: () => { invalidate(); setCallTarget(null) },
  })

  const shiftMutation = useMutation({
    mutationFn: ({ id, call_date }: { id: string; call_date: string }) =>
      api.post(`/ce/${ceId}/assignments/${id}/shift`, { call_date }).then(r => r.data),
    onSuccess: () => { invalidate(); setShiftTarget(null) },
  })

  const updatePocMutation = useMutation({
    mutationFn: ({ id, newPocPhone }: { id: string; newPocPhone: string }) =>
      api.post(`/ce/${ceId}/assignments/${id}/update-poc-phone`, { new_poc_phone: newPocPhone }).then(r => r.data),
    onSuccess: () => { invalidate(); setUpdatePocTarget(null) },
  })

  const noPickupMutation = useMutation({
    mutationFn: (id: string) => api.post(`/ce/${ceId}/assignments/${id}/no-pickup`).then(r => r.data),
    onSuccess: invalidate,
  })

  const sweepMutation = useMutation({
    mutationFn: () => api.post(`/ce/${ceId}/sweep-assign`).then(r => r.data.data as { added_to_pool: number }),
    onSuccess: (data) => {
      setSweepMessage(
        data.added_to_pool > 0
          ? `${data.added_to_pool} new lead${data.added_to_pool === 1 ? '' : 's'} added to the shared queue`
          : 'No new eligible clients right now'
      )
      invalidate()
    },
    onError: () => setSweepMessage('Could not check for new leads — try again'),
  })

  const list = tab === 'unclaimed' ? unclaimed : tab === 'mine' ? mine : tab === 'history' ? history : searchResults
  const loading = tab === 'unclaimed' ? unclaimedLoading : tab === 'mine' ? mineLoading : tab === 'history' ? historyLoading : searching

  return (
    <div className="w-full lg:max-w-5xl mx-auto min-h-dvh bg-[#f4f6fb]">
      <div className="bg-[#0d2b5e] px-5 lg:px-8 pt-8 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-white/50 text-xs font-semibold uppercase tracking-widest mb-1">HireLyra</p>
            <h1 className="text-white text-lg font-bold">{ceInfo.name}</h1>
            <p className="text-white/50 text-xs mt-0.5">Top End · Daily Target {ceInfo.daily_target}</p>
          </div>
          <button
            onClick={() => { setSweepMessage(null); sweepMutation.mutate() }}
            disabled={sweepMutation.isPending}
            className="shrink-0 px-3 py-2 rounded-lg border border-white/20 text-white text-xs font-semibold hover:bg-white/10 disabled:opacity-50"
          >
            {sweepMutation.isPending ? 'Checking…' : 'Check for New Leads'}
          </button>
        </div>
        {sweepMessage && (
          <p className="text-xs text-white/70 mt-2">{sweepMessage}</p>
        )}
      </div>

      {dashboard && (
        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-7 gap-1.5 px-4 lg:px-8 -mt-3">
          {tab === 'history' ? (
            <StatCard label="Total Claimed" value={dashboard.total_claimed} />
          ) : (
            <StatCard label="Claimed Today" value={`${dashboard.claimed_today}/${ceInfo.daily_target}`} />
          )}
          <StatCard label="Called" value={dashboard.total_called} />
          <StatCard label="Positive" value={dashboard.positive} />
          <StatCard label="Negative" value={dashboard.negative} />
          <StatCard label="Wrong POC" value={dashboard.wrong_poc} />
          <StatCard label="Wrong Company" value={dashboard.wrong_company} />
          <StatCard label="Onboarded" value={dashboard.onboarded} />
        </div>
      )}

      <div className="px-4 lg:px-8 mt-4">
        <div className="flex bg-white rounded-xl border border-gray-200 p-1 gap-1 lg:max-w-md">
          <button onClick={() => setTab('unclaimed')}
            className={`flex-1 py-2 rounded-lg text-[11px] font-semibold transition-colors ${tab === 'unclaimed' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            Unclaimed
          </button>
          <button onClick={() => setTab('mine')}
            className={`flex-1 py-2 rounded-lg text-[11px] font-semibold transition-colors ${tab === 'mine' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            Mine {dashboard ? `(${dashboard.today_pending})` : ''}
          </button>
          <button onClick={() => setTab('history')}
            className={`flex-1 py-2 rounded-lg text-[11px] font-semibold transition-colors ${tab === 'history' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            History
          </button>
          <button onClick={() => setTab('search')}
            className={`flex-1 py-2 rounded-lg text-[11px] font-semibold transition-colors ${tab === 'search' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            Search
          </button>
        </div>
      </div>

      {tab === 'unclaimed' && unclaimed.length > 0 && (
        <div className="px-4 lg:px-8 mt-3">
          <button
            onClick={() => claimBatchMutation.mutate()}
            disabled={batchClaiming}
            className="w-full lg:w-auto lg:px-8 py-2.5 rounded-lg bg-gray-900 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50"
          >
            Claim Batch ({ceInfo.daily_target})
          </button>
          {batchMessage && <p className="text-xs text-gray-500 mt-2">{batchMessage}</p>}
        </div>
      )}

      {tab === 'search' && (
        <div className="px-4 lg:px-8 mt-3">
          <input
            type="text" placeholder="Search by company, name, or phone..."
            value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && setSubmitted(q)}
            className="w-full lg:max-w-md px-3.5 py-2.5 bg-white border border-gray-200 rounded-xl text-sm outline-none focus:border-[#0d2b5e]"
          />
        </div>
      )}

      {reachoutMessage && (
        <div className="px-4 lg:px-8 mt-3">
          <p className="text-xs text-center text-gray-500 bg-white border border-gray-200 rounded-lg py-1.5 lg:max-w-md">{reachoutMessage}</p>
        </div>
      )}

      <div className="px-4 lg:px-8 py-4 grid grid-cols-1 lg:grid-cols-2 gap-3 items-start">
        {loading && <div className="col-span-full flex justify-center py-8"><div className="w-6 h-6 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" /></div>}
        {!loading && list.length === 0 && (
          <div className="col-span-full text-center py-12 text-sm text-gray-400">
            {tab === 'unclaimed' ? 'Nothing unclaimed right now.'
              : tab === 'mine' ? 'Nothing claimed and due today.'
              : tab === 'history' ? 'No calls made yet.'
              : submitted ? `No results for "${submitted}"` : 'Type at least 2 characters to search'}
          </div>
        )}
        {list.map(a => (
          <AssignmentRow
            key={a.assignment_id} a={a} unclaimed={tab === 'unclaimed'}
            onClaim={() => claimMutation.mutate(a.assignment_id)} claiming={claimingId === a.assignment_id}
            onCall={() => setCallTarget(a)} onShift={() => setShiftTarget(a)}
            onNoPickup={() => noPickupMutation.mutate(a.assignment_id)} onUpdatePoc={() => setUpdatePocTarget(a)}
            onSendReachout={() => reachoutMutation.mutate(a.assignment_id)}
            noPickupSaving={noPickupMutation.isPending} reachoutSaving={reachoutId === a.assignment_id}
          />
        ))}
      </div>

      {(claimingId || batchClaiming) && (
        <ClaimingOverlay message={batchClaiming ? `Claiming up to ${ceInfo.daily_target} clients…` : 'Claiming…'} />
      )}

      {callTarget && (
        <CallModal assignment={callTarget} saving={callMutation.isPending} onClose={() => setCallTarget(null)}
          onSave={(sentiment, comment) => callMutation.mutate({ id: callTarget.assignment_id, sentiment, comment })} />
      )}
      {shiftTarget && (
        <ShiftModal assignment={shiftTarget} saving={shiftMutation.isPending} onClose={() => setShiftTarget(null)}
          onSave={(call_date) => shiftMutation.mutate({ id: shiftTarget.assignment_id, call_date })} />
      )}
      {updatePocTarget && (
        <UpdatePocModal assignment={updatePocTarget} saving={updatePocMutation.isPending} onClose={() => setUpdatePocTarget(null)}
          onSave={(newPocPhone) => updatePocMutation.mutate({ id: updatePocTarget.assignment_id, newPocPhone })} />
      )}
    </div>
  )
}

export default function CEPage() {
  const { ceId } = useParams<{ ceId: string }>()
  const [pin, setPin] = useState('')
  const [authed, setAuthed] = useState(() => !!ceId && sessionStorage.getItem(PIN_STORAGE_PREFIX + ceId) === '1')
  const [error, setError] = useState<string | null>(null)

  const { data: ceInfo, isLoading: infoLoading } = useQuery({
    queryKey: ['ce-info', ceId],
    queryFn: () => api.get(`/ce/${ceId}/me`).then(r => r.data.data as CEInfo),
    enabled: !!ceId && authed,
  })

  const verifyMutation = useMutation({
    mutationFn: () => api.post(`/admin/ces/${ceId}/verify-pin`, { pin }).then(r => r.data),
    onSuccess: () => {
      setAuthed(true)
      setError(null)
      if (ceId) sessionStorage.setItem(PIN_STORAGE_PREFIX + ceId, '1')
    },
    onError: (e: any) => setError(e?.response?.data?.detail ?? 'Incorrect PIN'),
  })

  if (!ceId) return null

  if (!authed) {
    return (
      <div className="min-h-dvh flex flex-col items-center justify-center bg-[#f4f6fb] px-6">
        <div className="w-full max-w-xs">
          <p className="text-gray-400 text-xs font-semibold uppercase tracking-widest mb-1 text-center">HireLyra</p>
          <h1 className="text-[#0d2b5e] text-xl font-bold text-center mb-6">Customer Executive Login</h1>
          <input
            type="password" placeholder="Enter PIN" value={pin}
            onChange={e => setPin(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && pin && verifyMutation.mutate()}
            className="w-full px-4 py-3 bg-white border border-gray-200 rounded-xl text-sm outline-none focus:border-[#0d2b5e] text-center tracking-widest"
          />
          {error && <p className="text-xs text-red-600 text-center mt-2">{error}</p>}
          <button onClick={() => verifyMutation.mutate()} disabled={!pin || verifyMutation.isPending}
            className="w-full mt-4 py-3 rounded-xl bg-[#0d2b5e] text-white text-sm font-semibold disabled:opacity-50">
            {verifyMutation.isPending ? 'Checking…' : 'Log In'}
          </button>
        </div>
      </div>
    )
  }

  if (infoLoading || !ceInfo) {
    return (
      <div className="min-h-dvh flex items-center justify-center bg-[#f4f6fb]">
        <div className="w-8 h-8 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (ceInfo.tier === 'bottom_end') return <TicketDeskPage ceId={ceId} ceInfo={ceInfo} />

  return <CEDashboard ceId={ceId} ceInfo={ceInfo} />
}
