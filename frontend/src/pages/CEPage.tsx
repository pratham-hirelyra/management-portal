import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

interface CEInfo { id: string; name: string; phone: string | null; tier: string; max_clients_per_day: number; is_active: boolean }
interface Assignment {
  assignment_id: string; client_id: string; call_date: string; status: 'pending' | 'called'
  sentiment: 'positive' | 'negative' | 'wrong_poc' | null; comment: string | null; called_at: string | null; assigned_at: string
  company_name: string; poc_name: string | null; poc_phone: string | null; job_title: string | null
  client_stage: string; segment: string | null; industry: string | null; location: string | null
  last_engagement_at: string | null; no_pickup_count: number; last_attempt_at: string | null
}

function relDays(iso: string | null): string {
  if (!iso) return 'Never'
  const days = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000)
  if (days <= 0) return 'Today'
  if (days === 1) return 'Yesterday'
  return `${days}d ago`
}
interface Dashboard { total_called: number; positive: number; negative: number; wrong_poc: number; onboarded: number; today_pending: number }

const PIN_STORAGE_PREFIX = 'ce_pin_ok_'

// Which pool tier this assignment came from — matches the priority order the
// backend sweep assigns in (job post interested > employer lead interested > job post reachout).
function sourceTag(segment: string | null, stage: string): string {
  if (segment === 'active_job_post' && stage === 'interested') return 'Job Post · Interested'
  if (segment === 'employer_lead' && stage === 'interested') return 'Employer Lead · Interested'
  if (segment === 'active_job_post' && stage === 'reachout_sent') return 'Job Post · Reachout Sent'
  return `${segment || 'Unknown'} · ${stage}`
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-1.5 py-2.5 text-center">
      <p className="text-lg font-bold text-gray-900">{value}</p>
      <p className="text-[9px] text-gray-500 mt-0.5 uppercase tracking-wide leading-tight">{label}</p>
    </div>
  )
}

function CallModal({ assignment, onClose, onSave, saving }: {
  assignment: Assignment; onClose: () => void
  onSave: (sentiment: 'positive' | 'negative' | 'wrong_poc', comment: string) => void; saving: boolean
}) {
  const [sentiment, setSentiment] = useState<'positive' | 'negative' | 'wrong_poc' | null>(null)
  const [comment, setComment] = useState('')
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">{assignment.company_name}</p>
        <p className="text-xs text-gray-400 mt-0.5">{assignment.poc_name} · {assignment.poc_phone || 'No phone'}</p>

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
        <button onClick={() => setSentiment('wrong_poc')}
          className={`w-full mt-2 py-2.5 rounded-lg text-sm font-semibold border transition-colors ${
            sentiment === 'wrong_poc' ? 'bg-gray-900 text-white border-gray-900' : 'border-gray-300 text-gray-600 hover:bg-gray-50'
          }`}>
          Wrong POC — not the right person/number
        </button>

        <textarea
          className="w-full mt-3 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-gray-400"
          rows={3}
          placeholder={sentiment === 'wrong_poc' ? "What's actually wrong? e.g. \"disconnected\", \"wrong company\", \"person no longer here\" (optional)" : "Comment (optional)"}
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

const TERMINAL_LABELS: Record<string, string> = {
  onboarded:    'Already Onboarded',
  disqualified: 'Disqualified',
  churned:      'Churned',
}

function AssignmentRow({ a, onCall, onShift, onNoPickup, noPickupSaving }: {
  a: Assignment; onCall: () => void; onShift: () => void; onNoPickup: () => void; noPickupSaving: boolean
}) {
  const terminal = a.status === 'pending' ? TERMINAL_LABELS[a.client_stage] : undefined

  return (
    <div className="bg-white rounded-xl border border-gray-200 p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-semibold text-gray-900 truncate">{a.company_name}</p>
          <p className="text-xs text-gray-500 mt-0.5">
            {a.poc_name || '—'} ·{' '}
            {a.poc_phone ? <a href={`tel:${a.poc_phone}`} className="text-gray-900 font-semibold hover:underline">{a.poc_phone}</a> : 'No phone'}
          </p>
          <p className="text-xs text-gray-400 mt-0.5">{a.job_title || 'Role not set'}</p>
          <p className="text-xs text-gray-400 mt-0.5">{a.location || 'Location not on file'}</p>
          <div className="flex items-center gap-1.5 mt-1.5">
            <span className="inline-block text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
              {sourceTag(a.segment, a.client_stage)}
            </span>
            <span className="text-[10px] text-gray-400">· Last engaged {relDays(a.last_engagement_at)}</span>
          </div>
          {a.status === 'pending' && a.no_pickup_count > 0 && (
            <span className="inline-block mt-1 text-[10px] font-medium px-2 py-0.5 rounded-full bg-gray-100 text-gray-600">
              No pickup ×{a.no_pickup_count} — retrying
            </span>
          )}
        </div>
        {terminal ? (
          <span className="shrink-0 text-[10px] font-medium px-2 py-1 rounded-full bg-gray-100 text-gray-500">{terminal}</span>
        ) : a.status === 'called' ? (
          <span className="shrink-0 text-[10px] font-medium px-2 py-1 rounded-full bg-gray-100 text-gray-600">
            {a.sentiment === 'positive' ? 'Called · Positive' : a.sentiment === 'wrong_poc' ? 'Called · Wrong POC' : 'Called · Negative'}
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
      {a.status === 'pending' && !terminal && (
        <div className="flex gap-2 mt-3">
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
      )}
    </div>
  )
}

function CEDashboard({ ceId, ceInfo }: { ceId: string; ceInfo: CEInfo }) {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'queue' | 'history' | 'search'>('queue')
  const [q, setQ] = useState('')
  const [submitted, setSubmitted] = useState('')
  const [callTarget, setCallTarget] = useState<Assignment | null>(null)
  const [shiftTarget, setShiftTarget] = useState<Assignment | null>(null)
  const [sweepMessage, setSweepMessage] = useState<string | null>(null)

  const { data: dashboard } = useQuery({
    queryKey: ['ce-dashboard', ceId],
    queryFn: () => api.get(`/ce/${ceId}/dashboard`).then(r => r.data.data as Dashboard),
  })

  const { data: queue = [], isLoading: queueLoading } = useQuery({
    queryKey: ['ce-queue', ceId],
    queryFn: () => api.get(`/ce/${ceId}/queue`).then(r => r.data.data as Assignment[]),
    enabled: tab === 'queue',
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
    qc.invalidateQueries({ queryKey: ['ce-queue', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-history', ceId] })
    qc.invalidateQueries({ queryKey: ['ce-search', ceId] })
  }

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

  const noPickupMutation = useMutation({
    mutationFn: (id: string) => api.post(`/ce/${ceId}/assignments/${id}/no-pickup`).then(r => r.data),
    onSuccess: invalidate,
  })

  const sweepMutation = useMutation({
    mutationFn: () => api.post(`/ce/${ceId}/sweep-assign`).then(r => r.data.data as { assigned: number; skipped_no_capacity: number }),
    onSuccess: (data) => {
      setSweepMessage(
        data.assigned > 0
          ? `${data.assigned} new client${data.assigned === 1 ? '' : 's'} assigned across the team`
          : 'No new eligible clients right now'
      )
      invalidate()
    },
    onError: () => setSweepMessage('Could not run assignment — try again'),
  })

  const list = tab === 'queue' ? queue : tab === 'history' ? history : searchResults
  const loading = tab === 'queue' ? queueLoading : tab === 'history' ? historyLoading : searching

  return (
    <div className="max-w-md mx-auto min-h-dvh bg-[#f4f6fb]">
      <div className="bg-[#0d2b5e] px-5 pt-8 pb-5">
        <div className="flex items-start justify-between gap-3">
          <div>
            <p className="text-white/50 text-xs font-semibold uppercase tracking-widest mb-1">HireLyra</p>
            <h1 className="text-white text-lg font-bold">{ceInfo.name}</h1>
            <p className="text-white/50 text-xs mt-0.5">{ceInfo.tier === 'top_end' ? 'Top End' : 'Bottom End'} · Cap {ceInfo.max_clients_per_day}/day</p>
          </div>
          <button
            onClick={() => { setSweepMessage(null); sweepMutation.mutate() }}
            disabled={sweepMutation.isPending}
            className="shrink-0 px-3 py-2 rounded-lg border border-white/20 text-white text-xs font-semibold hover:bg-white/10 disabled:opacity-50"
          >
            {sweepMutation.isPending ? 'Checking…' : 'Get New Batch'}
          </button>
        </div>
        {sweepMessage && (
          <p className="text-xs text-white/70 mt-2">{sweepMessage}</p>
        )}
      </div>

      {dashboard && (
        <div className="grid grid-cols-5 gap-1.5 px-4 -mt-3">
          <StatCard label="Called" value={dashboard.total_called} />
          <StatCard label="Positive" value={dashboard.positive} />
          <StatCard label="Negative" value={dashboard.negative} />
          <StatCard label="Wrong POC" value={dashboard.wrong_poc} />
          <StatCard label="Onboarded" value={dashboard.onboarded} />
        </div>
      )}

      <div className="px-4 mt-4">
        <div className="flex bg-white rounded-xl border border-gray-200 p-1 gap-1">
          <button onClick={() => setTab('queue')}
            className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-colors ${tab === 'queue' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            Today {dashboard ? `(${dashboard.today_pending})` : ''}
          </button>
          <button onClick={() => setTab('history')}
            className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-colors ${tab === 'history' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            History {dashboard ? `(${dashboard.total_called})` : ''}
          </button>
          <button onClick={() => setTab('search')}
            className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-colors ${tab === 'search' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
            Search
          </button>
        </div>
      </div>

      {tab === 'search' && (
        <div className="px-4 mt-3">
          <input
            type="text" placeholder="Search by company, name, or phone..."
            value={q} onChange={e => setQ(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && setSubmitted(q)}
            className="w-full px-3.5 py-2.5 bg-white border border-gray-200 rounded-xl text-sm outline-none focus:border-[#0d2b5e]"
          />
        </div>
      )}

      <div className="px-4 py-4 flex flex-col gap-3">
        {loading && <div className="flex justify-center py-8"><div className="w-6 h-6 border-2 border-[#0d2b5e] border-t-transparent rounded-full animate-spin" /></div>}
        {!loading && list.length === 0 && (
          <div className="text-center py-12 text-sm text-gray-400">
            {tab === 'queue' ? 'Nothing due today.'
              : tab === 'history' ? 'No calls made yet.'
              : submitted ? `No results for "${submitted}"` : 'Type at least 2 characters to search'}
          </div>
        )}
        {list.map(a => (
          <AssignmentRow key={a.assignment_id} a={a} onCall={() => setCallTarget(a)} onShift={() => setShiftTarget(a)}
            onNoPickup={() => noPickupMutation.mutate(a.assignment_id)} noPickupSaving={noPickupMutation.isPending} />
        ))}
      </div>

      {callTarget && (
        <CallModal assignment={callTarget} saving={callMutation.isPending} onClose={() => setCallTarget(null)}
          onSave={(sentiment, comment) => callMutation.mutate({ id: callTarget.assignment_id, sentiment, comment })} />
      )}
      {shiftTarget && (
        <ShiftModal assignment={shiftTarget} saving={shiftMutation.isPending} onClose={() => setShiftTarget(null)}
          onSave={(call_date) => shiftMutation.mutate({ id: shiftTarget.assignment_id, call_date })} />
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

  return <CEDashboard ceId={ceId} ceInfo={ceInfo} />
}
