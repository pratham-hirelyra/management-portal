import { useState, useEffect, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useQueries, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

// ── Types ─────────────────────────────────────────────────────────────────────
interface RMInfo { id: string; name: string; phone: string | null; city: string | null; location: string | null }
interface ClientItem {
  client_id: string; company_name: string; stage: string; job_title: string | null
  location: string | null; poc_name: string | null; poc_phone: string | null
  portal_token: string | null; high_match_count: number; pipeline_count: number
}
interface RMActions { rm: RMInfo; clients: ClientItem[] }

interface CandidateRow {
  id: string; candidate_id: string
  candidate_name?: string; candidate_phone?: string; experience?: string
  current_salary?: number | null; expected_salary?: number | null
  current_location?: string | null
  stage: string; match_score?: number | null
  wa_sent_at?: string | null; interview_slot?: string | null
  rejection_reason?: string | null; decline_reason?: string | null
  notes?: string | null; distance_km?: number | null
  category?: string | null; evaluation_status?: string | null
}

// ── Helpers ───────────────────────────────────────────────────────────────────
const getRMActions = (rmId: string) =>
  api.get<{ data: RMActions }>(`/admin/rms/${rmId}/actions`).then(r => r.data.data)

const getMappings = (clientId: string) =>
  api.get<{ data: CandidateRow[] }>(`/clients/${clientId}/mappings`).then(r => r.data.data)

function relTime(iso: string) {
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 60) return `${m}m ago`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h}h ago`
  return `${Math.floor(h / 24)}d ago`
}

function fmtSalary(n: number | null | undefined) {
  if (!n) return '—'
  return `₹${(n / 1000).toFixed(0)}k`
}

// ── Client status derivation ──────────────────────────────────────────────────
function clientStatusLabel(item: ClientItem): { label: string; cls: string } {
  const s = item.stage
  if (s === 'onboarded')      return { label: 'Active – Matching',      cls: 'bg-blue-100 text-blue-700' }
  if (s === 'agreement_sent') return { label: 'Agreement Pending',       cls: 'bg-purple-100 text-purple-700' }
  if (s === 'interested')     return { label: 'Form Collection',         cls: 'bg-orange-100 text-orange-700' }
  if (s === 'reachout_sent')  return { label: 'Reachout Sent',           cls: 'bg-amber-100 text-amber-700' }
  if (s === 'disqualified')   return { label: 'Closed',                  cls: 'bg-red-100 text-red-600' }
  if (s === 'churned')        return { label: 'Churned',                 cls: 'bg-red-100 text-red-600' }
  if (s === 'deal_won')       return { label: '🏆 Deal Won',             cls: 'bg-emerald-100 text-emerald-700' }
  return { label: s.replace(/_/g, ' '), cls: 'bg-gray-100 text-gray-600' }
}

// ── Candidate status derivation ───────────────────────────────────────────────
function candStatusLabel(stage: string): { label: string; cls: string } {
  const map: Record<string, { label: string; cls: string }> = {
    matched:                  { label: 'Matched',           cls: 'bg-gray-100 text-gray-500' },
    intent_ask:               { label: 'Reached Out',       cls: 'bg-sky-100 text-sky-700' },
    interested:               { label: 'Interested',        cls: 'bg-blue-100 text-blue-700' },
    not_interested:           { label: 'Not Interested',    cls: 'bg-gray-100 text-gray-500' },
    client_approval_pending:  { label: 'Shortlisted',       cls: 'bg-violet-100 text-violet-700' },
    invited_for_interview:    { label: 'Invited',           cls: 'bg-indigo-100 text-indigo-700' },
    slot_sent_candidate:      { label: 'Slot Sent',         cls: 'bg-teal-100 text-teal-700' },
    slot_booked:              { label: 'Slot Booked',       cls: 'bg-teal-200 text-teal-800' },
    interview_done:           { label: 'Interview Done',    cls: 'bg-amber-100 text-amber-700' },
    placed:                   { label: 'Hired ✓',           cls: 'bg-green-100 text-green-700 font-semibold' },
    rejected:                 { label: 'Rejected',          cls: 'bg-red-100 text-red-600' },
    waitlisted:               { label: 'Waitlisted',        cls: 'bg-amber-100 text-amber-600' },
    declined_for_interview:   { label: 'Declined',          cls: 'bg-red-100 text-red-500' },
    client_closed:            { label: 'Position Filled',   cls: 'bg-gray-100 text-gray-500' },
  }
  return map[stage] ?? { label: stage.replace(/_/g, ' '), cls: 'bg-gray-100 text-gray-500' }
}

// Lower number = shown first (more actionable)
const STAGE_PRIORITY: Record<string, number> = {
  invited_for_interview:   1,  // RM must book a slot — highest urgency
  interested:              2,  // candidate interested, slot needed
  client_approval_pending: 3,  // shortlisted, slot needed
  slot_booked:             4,  // interview scheduled — upcoming
  slot_sent_candidate:     5,  // waiting on candidate to pick slot
  interview_done:          6,  // done, may need feedback
  waitlisted:              7,
  intent_ask:              8,  // just reached out — lowest urgency
  matched:                 9,  // not yet contacted
}
const stagePriority = (s: string) => STAGE_PRIORITY[s] ?? 8

// ── Toast ─────────────────────────────────────────────────────────────────────
interface Toast { id: number; type: 'success' | 'info' | 'warn'; msg: string }
let _toastId = 0
function ToastStack({ toasts, remove }: { toasts: Toast[]; remove: (id: number) => void }) {
  return (
    <div className="fixed top-4 right-4 z-[100] flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id} className={`flex items-center gap-2.5 px-4 py-2.5 rounded-xl shadow-lg text-sm font-medium pointer-events-auto ${
          t.type === 'success' ? 'bg-green-600 text-white' :
          t.type === 'warn'    ? 'bg-amber-500 text-white' :
                                 'bg-blue-600 text-white'
        }`}>
          {t.type === 'success' ? '✓' : t.type === 'warn' ? '⚠' : 'ℹ'} {t.msg}
          <button onClick={() => remove(t.id)} className="ml-1 opacity-70 hover:opacity-100">×</button>
        </div>
      ))}
    </div>
  )
}

// ── Modals ────────────────────────────────────────────────────────────────────
function Modal({ title, subtitle, onClose, children }: {
  title: string; subtitle?: string; onClose: () => void; children: React.ReactNode
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-4">
          <div>
            <p className="text-sm font-bold text-gray-900">{title}</p>
            {subtitle && <p className="text-xs text-gray-500 mt-0.5">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none ml-2">×</button>
        </div>
        {children}
      </div>
    </div>
  )
}

function HireModal({ mappingId, candidateName, companyName, rmId, onClose, toast }: {
  mappingId: string; candidateName: string; companyName: string
  rmId: string; onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const mut = useMutation({
    mutationFn: () => api.post(`/admin/rms/hire/${mappingId}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      qc.invalidateQueries({ queryKey: ['rm-mappings'] })
      toast('success', `${candidateName} marked as hired`)
      onClose()
    },
  })
  return (
    <Modal title="Confirm Hire" subtitle={`${candidateName} → ${companyName}`} onClose={onClose}>
      <div className="bg-green-50 border border-green-200 rounded-xl px-3 py-2.5 text-xs text-green-700 mb-4">
        Candidate will be marked as placed and all other active mappings will be closed.
      </div>
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending} className="flex-1 py-2.5 rounded-xl bg-green-600 text-white text-xs font-semibold hover:bg-green-700 disabled:opacity-50">
          {mut.isPending ? '…' : '✅ Confirm Hire'}
        </button>
      </div>
    </Modal>
  )
}

function RejectModal({ mappingId, candidateName, companyName, rmId, onClose, toast }: {
  mappingId: string; candidateName: string; companyName: string
  rmId: string; onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const mut = useMutation({
    mutationFn: () => api.post(`/admin/rms/reject/${mappingId}`, { reason }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      qc.invalidateQueries({ queryKey: ['rm-mappings'] })
      toast('info', `${candidateName} rejected`)
      onClose()
    },
  })
  const presets = ['Skills mismatch', 'Salary too high', 'Experience insufficient', 'Cultural fit issue', 'Position filled']
  return (
    <Modal title="Reject Candidate" subtitle={`${candidateName} → ${companyName}`} onClose={onClose}>
      <p className="text-xs text-gray-500 mb-2">Select or type a reason</p>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {presets.map(p => (
          <button key={p} onClick={() => setReason(p)}
            className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${reason === p ? 'bg-red-600 border-red-600 text-white' : 'border-gray-200 text-gray-600 hover:border-red-300'}`}>
            {p}
          </button>
        ))}
      </div>
      <textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Or type a custom reason…"
        rows={2} className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-red-300 resize-none mb-3" />
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending || !reason.trim()} className="flex-1 py-2.5 rounded-xl bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50">
          {mut.isPending ? '…' : 'Reject & Notify'}
        </button>
      </div>
    </Modal>
  )
}

function PortalModal({ clientId, companyName, portalToken, rmId, onClose, toast }: {
  clientId: string; companyName: string; portalToken: string | null
  rmId: string; onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const portalUrl = portalToken ? `${window.location.origin}/client/${portalToken}` : null
  const mut = useMutation({
    mutationFn: () => api.post(`/admin/rms/send-portal-link/${clientId}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      toast('success', 'Portal link sent')
      onClose()
    },
  })
  return (
    <Modal title="Send Client Portal" subtitle={companyName} onClose={onClose}>
      {portalUrl && (
        <div className="bg-indigo-50 border border-indigo-100 rounded-xl px-3 py-2.5 mb-3">
          <p className="text-[10px] font-semibold text-indigo-400 uppercase tracking-wide mb-1">Portal URL</p>
          <p className="text-xs text-indigo-700 break-all font-mono leading-relaxed">{portalUrl}</p>
          <button onClick={() => navigator.clipboard.writeText(portalUrl)} className="mt-1.5 text-[10px] text-indigo-500 hover:text-indigo-700 font-semibold">Copy link</button>
        </div>
      )}
      <p className="text-xs text-gray-500 mb-4">Client will receive a WhatsApp with their portal link to view candidates and book interview slots.</p>
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending} className="flex-1 py-2.5 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 disabled:opacity-50">
          {mut.isPending ? '…' : '📤 Send via WhatsApp'}
        </button>
      </div>
    </Modal>
  )
}

function ChurnModal({ client, rmId, onClose, toast }: {
  client: ClientItem; rmId: string
  onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const presets = ['Not responding', 'Budget issue', 'Hired internally', 'Position on hold', 'Competitor hired']
  const mut = useMutation({
    mutationFn: () => api.patch(`/clients/${client.client_id}/stage`, { to_stage: 'churned', note: reason }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      qc.invalidateQueries({ queryKey: ['rm-mappings', client.client_id] })
      toast('warn', `${client.company_name} marked as churned`)
      onClose()
    },
  })
  return (
    <Modal title="Mark Client as Churned" subtitle={client.company_name} onClose={onClose}>
      <p className="text-xs text-gray-500 mb-2">Select or type a churn reason</p>
      <div className="flex flex-wrap gap-1.5 mb-3">
        {presets.map(p => (
          <button key={p} onClick={() => setReason(p)}
            className={`text-[11px] px-2.5 py-1 rounded-full border transition-colors ${reason === p ? 'bg-gray-700 border-gray-700 text-white' : 'border-gray-200 text-gray-600 hover:border-gray-400'}`}>
            {p}
          </button>
        ))}
      </div>
      <textarea value={reason} onChange={e => setReason(e.target.value)} placeholder="Or type a custom reason…"
        rows={2} className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-gray-300 resize-none mb-3" />
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending || !reason.trim()}
          className="flex-1 py-2.5 rounded-xl bg-gray-700 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50">
          {mut.isPending ? '…' : 'Mark Churned'}
        </button>
      </div>
    </Modal>
  )
}

function WonDealModal({ client, rmId, onClose, toast }: {
  client: ClientItem; rmId: string
  onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const { data: clientData } = useQuery({
    queryKey: ['client-detail', client.client_id],
    queryFn: () => api.get(`/clients/${client.client_id}`).then(r => r.data.data),
  })
  const [feeAmount, setFeeAmount] = useState<string>('')
  useEffect(() => {
    if (clientData?.fee_amount != null) setFeeAmount(String(clientData.fee_amount))
  }, [clientData])

  const mut = useMutation({
    mutationFn: () => api.post(`/admin/rms/mark-won/${client.client_id}`, {
      fee_amount: feeAmount ? parseFloat(feeAmount) : null,
    }).then(r => r.data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      qc.invalidateQueries({ queryKey: ['rm-mappings', client.client_id] })
      const rejected = data.data?.auto_rejected ?? 0
      toast('success', `Deal won! ${rejected > 0 ? `${rejected} interview candidate${rejected > 1 ? 's' : ''} notified.` : ''}`)
      onClose()
    },
  })
  return (
    <Modal title="Mark Deal as Won 🏆" subtitle={client.company_name} onClose={onClose}>
      <div className="bg-emerald-50 border border-emerald-200 rounded-xl px-3 py-2.5 text-xs text-emerald-700 mb-4">
        Client will be marked <strong>Deal Won</strong>. Candidates who appeared for interview but weren't placed will be auto-rejected and notified.
      </div>
      <label className="block text-xs font-semibold text-gray-600 mb-1">Deal Value (₹)</label>
      <input
        type="number"
        value={feeAmount}
        onChange={e => setFeeAmount(e.target.value)}
        placeholder="e.g. 25000"
        className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-emerald-300 mb-4"
      />
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => mut.mutate()} disabled={mut.isPending}
          className="flex-1 py-2.5 rounded-xl bg-emerald-600 text-white text-xs font-semibold hover:bg-emerald-700 disabled:opacity-50">
          {mut.isPending ? '…' : '🏆 Confirm Won'}
        </button>
      </div>
    </Modal>
  )
}

// ── Slot picker helpers (mirrors SchedulePage) ────────────────────────────────
const _MONTHS     = ['January','February','March','April','May','June','July','August','September','October','November','December']
const _DAYS_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
const _TIME_SLOTS = [
  '9:00 AM','10:00 AM','11:00 AM','12:00 PM',
  '1:00 PM','2:00 PM','3:00 PM','4:00 PM','5:00 PM','6:00 PM','7:00 PM','8:00 PM',
]

function _getCalDays(year: number, month: number): (number | null)[] {
  const firstDay = new Date(year, month, 1).getDay()
  const total    = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = Array(firstDay).fill(null)
  for (let d = 1; d <= total; d++) cells.push(d)
  return cells
}

function _parseTime(t: string) {
  const [tp, period] = t.split(' ')
  let [h, m] = tp.split(':').map(Number)
  if (period === 'PM' && h !== 12) h += 12
  if (period === 'AM' && h === 12) h = 0
  return { hour: h, minute: m }
}

function _makeLabel(date: Date, timeStr: string): string {
  const { hour, minute } = _parseTime(timeStr)
  const start = new Date(date); start.setHours(hour, minute, 0, 0)
  const end   = new Date(start.getTime() + 60 * 60 * 1000)
  const dateLabel = date.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' })
  const fmt = (d: Date) => d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true })
  return `${dateLabel} · ${fmt(start)} – ${fmt(end)}`
}

function _slotKey(date: Date, timeStr: string) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}|${timeStr}`
}

function _sameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate()
}

function SetClientSlotsModal({ clientId, companyName, rmId, onClose, toast }: {
  clientId: string; companyName: string; rmId: string
  onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const today = new Date(); today.setHours(0, 0, 0, 0)

  const { data: clientData, isLoading: loadingSlots } = useQuery({
    queryKey: ['client-detail', clientId],
    queryFn: () => api.get(`/clients/${clientId}`).then(r => r.data.data),
  })

  // Existing slots from DB shown as removable chips — merge both storage fields
  const [existingLabels, setExistingLabels] = useState<string[]>([])
  useEffect(() => {
    const recruiterLabels: string[] = (clientData?.recruiter_slots ?? []).map((s: any) => s.label as string)
    const availableLabels: string[] = (clientData?.available_slots ?? []).map((s: any) => s.label as string)
    const seen = new Set(recruiterLabels)
    const merged = [...recruiterLabels, ...availableLabels.filter(l => !seen.has(l))]
    setExistingLabels(merged)
  }, [clientData])

  const [viewDate, setViewDate]       = useState(new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const [pickedKeys, setPickedKeys]   = useState<Set<string>>(new Set())
  const [pickedLabels, setPickedLabels] = useState<Map<string, string>>(new Map())

  const toggleSlot = (date: Date, timeStr: string) => {
    const key = _slotKey(date, timeStr)
    setPickedKeys(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n })
    setPickedLabels(prev => { const n = new Map(prev); n.has(key) ? n.delete(key) : n.set(key, _makeLabel(date, timeStr)); return n })
  }

  const canGoPrev = viewDate.getFullYear() > today.getFullYear() || viewDate.getMonth() > today.getMonth()

  const allLabels = [...existingLabels, ...Array.from(pickedLabels.values())]

  const mut = useMutation({
    mutationFn: () => api.post(`/admin/rms/set-client-slots/${clientId}`, { slots: allLabels }).then(r => r.data),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ['client-detail', clientId] })
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      qc.invalidateQueries({ queryKey: ['rm-mappings', clientId] })
      toast('success', `Slots saved for ${companyName}`)
      onClose()
    },
  })

  const calDays = _getCalDays(viewDate.getFullYear(), viewDate.getMonth())

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 px-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg p-5 max-h-[90vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-start justify-between mb-4">
          <div>
            <p className="text-sm font-bold text-gray-900">Set Interview Slots</p>
            <p className="text-xs text-gray-500 mt-0.5">{companyName}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none ml-2">×</button>
        </div>

        {/* Month nav */}
        <div className="flex items-center justify-between mb-3">
          <p className="text-sm font-semibold text-gray-800">{_MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}</p>
          <div className="flex gap-1">
            <button onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1))}
              disabled={!canGoPrev}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed text-gray-600 text-sm">‹</button>
            <button onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1))}
              className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100 text-gray-600 text-sm">›</button>
          </div>
        </div>

        {/* Calendar grid */}
        <div className="grid grid-cols-7 mb-1">
          {_DAYS_SHORT.map(d => <div key={d} className="text-center text-[10px] font-medium text-gray-400 py-1">{d}</div>)}
        </div>
        <div className="grid grid-cols-7 gap-y-1 mb-4">
          {calDays.map((day, i) => {
            if (!day) return <div key={i} />
            const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day)
            const isPast   = cellDate < today
            const isToday  = _sameDay(cellDate, today)
            const isSel    = selectedDate ? _sameDay(cellDate, selectedDate) : false
            return (
              <button key={i} onClick={() => !isPast && setSelectedDate(cellDate)} disabled={isPast}
                className={`mx-auto w-8 h-8 flex items-center justify-center rounded-full text-xs transition-colors
                  ${isPast ? 'text-gray-300 cursor-not-allowed' : 'cursor-pointer hover:bg-blue-50 hover:text-blue-600'}
                  ${isSel ? 'bg-blue-600 text-white font-semibold' : ''}
                  ${isToday && !isSel ? 'font-semibold text-blue-600' : ''}
                  ${!isPast && !isSel && !isToday ? 'text-gray-800' : ''}
                `}>
                {day}
              </button>
            )
          })}
        </div>

        {/* Time slots */}
        {selectedDate ? (
          <div className="mb-4">
            <p className="text-xs font-medium text-gray-600 mb-2">
              {selectedDate.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
            </p>
            <div className="grid grid-cols-3 gap-1.5">
              {_TIME_SLOTS.map(t => {
                const key    = _slotKey(selectedDate, t)
                const picked = pickedKeys.has(key)
                return (
                  <button key={t} onClick={() => toggleSlot(selectedDate, t)}
                    className={`py-2 px-2 rounded-lg border text-xs font-medium transition-all
                      ${picked ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-200 text-gray-700 hover:border-blue-400 hover:text-blue-600'}`}>
                    {t}
                  </button>
                )
              })}
            </div>
          </div>
        ) : (
          <p className="text-xs text-gray-400 mb-4">Select a date to see available times.</p>
        )}

        {/* All selected chips */}
        {loadingSlots ? (
          <div className="flex items-center gap-2 mb-4 text-[11px] text-gray-400">
            <div className="w-3.5 h-3.5 border-2 border-gray-300 border-t-indigo-500 rounded-full animate-spin shrink-0" />
            Loading saved slots…
          </div>
        ) : allLabels.length > 0 && (
          <div className="mb-4">
            <p className="text-[10px] font-semibold text-gray-500 uppercase tracking-wide mb-2">Selected slots ({allLabels.length})</p>
            <div className="flex flex-wrap gap-1.5">
              {existingLabels.map((label, i) => (
                <span key={`ex-${i}`} className="inline-flex items-center gap-1 text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-100 rounded-full px-2.5 py-1">
                  {label}
                  <button onClick={() => setExistingLabels(l => l.filter((_, idx) => idx !== i))} className="text-indigo-300 hover:text-indigo-600 ml-0.5">×</button>
                </span>
              ))}
              {Array.from(pickedLabels.entries()).map(([key, label]) => (
                <span key={key} className="inline-flex items-center gap-1 text-[10px] bg-blue-50 text-blue-700 border border-blue-100 rounded-full px-2.5 py-1">
                  {label}
                  <button onClick={() => {
                    setPickedKeys(p => { const n = new Set(p); n.delete(key); return n })
                    setPickedLabels(p => { const n = new Map(p); n.delete(key); return n })
                  }} className="text-blue-300 hover:text-blue-600 ml-0.5">×</button>
                </span>
              ))}
            </div>
          </div>
        )}

        {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
        <div className="flex gap-2 pt-2 border-t border-gray-100">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
          <button onClick={() => mut.mutate()} disabled={allLabels.length === 0 || mut.isPending}
            className="flex-1 py-2.5 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 disabled:opacity-50">
            {mut.isPending ? '…' : `Save ${allLabels.length} slot${allLabels.length !== 1 ? 's' : ''}`}
          </button>
        </div>
      </div>
    </div>
  )
}

function _parseSlotLabelToIso(label: string): string | null {
  const m = label.match(/(\d{1,2})\s+([A-Za-z]+),?\s+(\d{4})\s*[·•]\s*(\d{1,2}:\d{2}\s*[aApP][mM])/)
  if (!m) return null
  const [, day, month, year, timeStr] = m
  const dt = new Date(`${day} ${month} ${year} ${timeStr.trim()}`)
  return isNaN(dt.getTime()) ? null : dt.toISOString()
}

function RescheduleModal({ mappingId, clientId, candidateName, companyName, rmId, onClose, toast }: {
  mappingId: string; clientId: string; candidateName: string; companyName: string
  rmId: string; onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null)

  const { data: clientData, isLoading: clientLoading } = useQuery({
    queryKey: ['client-detail', clientId],
    queryFn: () => api.get(`/clients/${clientId}`).then(r => r.data.data),
  })
  const recruiterSlots: { label: string }[] = clientData?.recruiter_slots ?? []
  const availableSlots: { label: string }[] = clientData?.available_slots ?? []
  const recruiterLabelSet = new Set(recruiterSlots.map(s => s.label))
  const slots: { label: string }[] = [...recruiterSlots, ...availableSlots.filter(s => !recruiterLabelSet.has(s.label))]

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['rm-mappings', clientId] })
    qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
  }

  const rescheduleMut = useMutation({
    mutationFn: () => {
      const iso = selectedSlot ? _parseSlotLabelToIso(selectedSlot) : null
      if (!iso) throw new Error('Could not parse slot time')
      return api.post(`/clients/${clientId}/mappings/${mappingId}/modify-slot`, { interview_slot: iso }).then(r => r.data)
    },
    onSuccess: () => { invalidate(); toast('success', `Interview rescheduled for ${candidateName}`); onClose() },
  })

  const resendMut = useMutation({
    mutationFn: () => api.post(`/admin/rms/resend-slot/${mappingId}`).then(r => r.data),
    onSuccess: () => { invalidate(); toast('success', `Slot selection sent to ${candidateName}`); onClose() },
  })

  return (
    <Modal title="Reschedule / Resend Slot" subtitle={`${candidateName} → ${companyName}`} onClose={onClose}>
      {clientLoading ? (
        <div className="flex items-center justify-center py-6">
          <div className="w-5 h-5 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : slots.length === 0 ? (
        <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 mb-4">
          No interview slots set for this client yet. Set slots first using the "Set Slots" button.
        </p>
      ) : (
        <div className="space-y-1.5 max-h-44 overflow-y-auto mb-4">
          {slots.map(slot => (
            <button key={slot.label} onClick={() => setSelectedSlot(slot.label)}
              className={`w-full text-left px-3 py-2.5 rounded-xl border text-xs transition-colors ${
                selectedSlot === slot.label
                  ? 'bg-teal-600 border-teal-600 text-white font-semibold'
                  : 'border-gray-200 text-gray-700 hover:border-teal-300 hover:bg-teal-50'
              }`}>
              📅 {slot.label}
            </button>
          ))}
        </div>
      )}
      {(rescheduleMut.error as any)?.message && <p className="text-xs text-red-500 mb-2">{(rescheduleMut.error as any).message}</p>}
      {(rescheduleMut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(rescheduleMut.error as any).response.data.detail}</p>}
      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
          <button onClick={() => rescheduleMut.mutate()} disabled={!selectedSlot || rescheduleMut.isPending || resendMut.isPending}
            className="flex-1 py-2.5 rounded-xl bg-teal-600 text-white text-xs font-semibold hover:bg-teal-700 disabled:opacity-50">
            {rescheduleMut.isPending ? '…' : '🔄 Reschedule'}
          </button>
        </div>
        <button onClick={() => resendMut.mutate()} disabled={resendMut.isPending || rescheduleMut.isPending}
          className="w-full py-2.5 rounded-xl bg-sky-50 border border-sky-200 text-sky-700 text-xs font-semibold hover:bg-sky-100 disabled:opacity-50">
          {resendMut.isPending ? '…' : '📤 Send Slot Selection to Candidate'}
        </button>
      </div>
    </Modal>
  )
}

function BookSlotModal({ mappingId, clientId, candidateName, companyName, rmId, onClose, toast }: {
  mappingId: string; clientId: string; candidateName: string; companyName: string
  rmId: string; onClose: () => void; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [selectedSlot, setSelectedSlot] = useState<string | null>(null)

  const { data: clientData, isLoading: clientLoading } = useQuery({
    queryKey: ['client-detail', clientId],
    queryFn: () => api.get(`/clients/${clientId}`).then(r => r.data.data),
  })
  const recruiterSlots: { label: string }[] = clientData?.recruiter_slots ?? []
  const availableSlots: { label: string }[] = clientData?.available_slots ?? []
  const recruiterLabelSet = new Set(recruiterSlots.map(s => s.label))
  const slots: { label: string }[] = [...recruiterSlots, ...availableSlots.filter(s => !recruiterLabelSet.has(s.label))]

  const mut = useMutation({
    mutationFn: (slot_label: string) => api.post(`/admin/rms/book-slot/${mappingId}`, { slot_label }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-mappings', clientId] })
      toast('success', `Interview booked for ${candidateName}`)
      onClose()
    },
  })
  return (
    <Modal title="Book Interview Slot" subtitle={`${candidateName} → ${companyName}`} onClose={onClose}>
      {clientLoading ? (
        <div className="flex items-center justify-center py-6">
          <div className="w-5 h-5 border-2 border-indigo-500 border-t-transparent rounded-full animate-spin" />
        </div>
      ) : slots.length === 0 ? (
        <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 mb-4">No interview slots set for this client yet.</p>
      ) : (
        <div className="space-y-1.5 max-h-52 overflow-y-auto mb-4">
          {slots.map(slot => (
            <button key={slot.label} onClick={() => setSelectedSlot(slot.label)}
              className={`w-full text-left px-3 py-2.5 rounded-xl border text-xs transition-colors ${selectedSlot === slot.label ? 'bg-indigo-600 border-indigo-600 text-white font-semibold' : 'border-gray-200 text-gray-700 hover:border-indigo-300 hover:bg-indigo-50'}`}>
              📅 {slot.label}
            </button>
          ))}
        </div>
      )}
      {(mut.error as any)?.response?.data?.detail && <p className="text-xs text-red-500 mb-2">{(mut.error as any).response.data.detail}</p>}
      <div className="flex gap-2">
        <button onClick={onClose} className="flex-1 py-2.5 rounded-xl border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-50">Cancel</button>
        <button onClick={() => selectedSlot && mut.mutate(selectedSlot)} disabled={!selectedSlot || mut.isPending}
          className="flex-1 py-2.5 rounded-xl bg-indigo-600 text-white text-xs font-semibold hover:bg-indigo-700 disabled:opacity-50">
          {mut.isPending ? '…' : 'Confirm Slot'}
        </button>
      </div>
    </Modal>
  )
}

// ── Candidate action cell ─────────────────────────────────────────────────────
function CandidateActions({ row, clientId, rmId, toast }: {
  row: CandidateRow; clientId: string; rmId: string
  toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [modal, setModal] = useState<'hire'|'reject'|'book'|'reschedule'|null>(null)
  const [confirmResend, setConfirmResend] = useState(false)

  const resendSlot = useMutation({
    mutationFn: () => api.post(`/admin/rms/resend-slot/${row.id}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-mappings', clientId] })
      setConfirmResend(false)
      toast('success', `Slot selection sent to ${name}`)
    },
  })
  const name = row.candidate_name ?? 'Candidate'

  const advanceMut = useMutation({
    mutationFn: (stage: string) => api.patch(`/clients/${clientId}/mappings/${row.id}/stage`, { stage }).then(r => r.data),
    onSuccess: (_, stage) => {
      qc.invalidateQueries({ queryKey: ['rm-mappings', clientId] })
      if (stage === 'interview_done') toast('success', `${name} marked interview done`)
    },
  })

  const stage = row.stage

  if (stage === 'placed' || stage === 'not_interested' || stage === 'declined_for_interview') return null
  if (stage === 'rejected') return (
    <span className="text-[10px] text-red-400 italic">{row.rejection_reason?.replace(/_/g,' ') ?? 'Rejected'}</span>
  )

  return (
    <>
      <div className="flex gap-1 flex-wrap">
        {(stage === 'interested' || stage === 'client_approval_pending') && (
          <button onClick={() => setModal('book')}
            className="px-1.5 py-0.5 text-[10px] rounded bg-teal-50 border border-teal-200 text-teal-700 font-semibold hover:bg-teal-100 whitespace-nowrap">
            📅 Book
          </button>
        )}
        {(stage === 'invited_for_interview' || stage === 'slot_sent_candidate' || stage === 'slot_booked') && (
          <button onClick={() => advanceMut.mutate('interview_done')} disabled={advanceMut.isPending}
            className="px-1.5 py-0.5 text-[10px] rounded bg-amber-50 border border-amber-200 text-amber-700 font-semibold hover:bg-amber-100 whitespace-nowrap disabled:opacity-50">
            {advanceMut.isPending ? '…' : '✓ Done'}
          </button>
        )}
        {(stage === 'slot_sent_candidate' || stage === 'slot_booked' || stage === 'interview_done') && (
          <button onClick={() => setModal('reschedule')}
            className="px-1.5 py-0.5 text-[10px] rounded bg-teal-50 border border-teal-200 text-teal-700 font-semibold hover:bg-teal-100 whitespace-nowrap">
            🔄 Reschedule
          </button>
        )}
        {stage === 'client_approval_pending' && (
          !confirmResend ? (
            <button onClick={() => setConfirmResend(true)}
              className="px-1.5 py-0.5 text-[10px] rounded bg-violet-50 border border-violet-200 text-violet-700 font-semibold hover:bg-violet-100 whitespace-nowrap">
              ✉️ Invite
            </button>
          ) : (
            <div className="flex items-center gap-1 whitespace-nowrap">
              <span className="text-[9px] text-gray-500">Send?</span>
              <button onClick={() => resendSlot.mutate()} disabled={resendSlot.isPending}
                className="text-[10px] text-violet-700 font-semibold hover:text-violet-900 disabled:opacity-50">
                {resendSlot.isPending ? '…' : 'Yes'}
              </button>
              <button onClick={() => setConfirmResend(false)} className="text-[10px] text-gray-400 hover:text-gray-600">×</button>
            </div>
          )
        )}
        {(stage === 'invited_for_interview' || stage === 'slot_sent_candidate') && (
          !confirmResend ? (
            <button onClick={() => setConfirmResend(true)}
              className="px-1.5 py-0.5 text-[10px] rounded bg-sky-50 border border-sky-200 text-sky-700 font-semibold hover:bg-sky-100 whitespace-nowrap">
              📤 Resend
            </button>
          ) : (
            <div className="flex items-center gap-1 whitespace-nowrap">
              <button onClick={() => resendSlot.mutate()} disabled={resendSlot.isPending}
                className="text-[10px] text-sky-700 font-semibold hover:text-sky-900 disabled:opacity-50">
                {resendSlot.isPending ? '…' : 'Confirm'}
              </button>
              <button onClick={() => setConfirmResend(false)} className="text-[10px] text-gray-400 hover:text-gray-600">×</button>
            </div>
          )
        )}
        {stage === 'interview_done' && (
          <>
            <button onClick={() => setModal('hire')}
              className="px-1.5 py-0.5 text-[10px] rounded bg-green-600 text-white font-semibold hover:bg-green-700 whitespace-nowrap">
              ✅ Hire
            </button>
            <button onClick={() => setModal('reject')}
              className="px-1.5 py-0.5 text-[10px] rounded bg-red-50 border border-red-200 text-red-600 font-semibold hover:bg-red-100 whitespace-nowrap">
              ✗ Reject
            </button>
          </>
        )}
      </div>

      {modal === 'hire'       && <HireModal       mappingId={row.id} candidateName={name} companyName="" rmId={rmId} onClose={() => setModal(null)} toast={toast} />}
      {modal === 'reject'     && <RejectModal     mappingId={row.id} candidateName={name} companyName="" rmId={rmId} onClose={() => setModal(null)} toast={toast} />}
      {modal === 'book'       && <BookSlotModal   mappingId={row.id} clientId={clientId} candidateName={name} companyName="" rmId={rmId} onClose={() => setModal(null)} toast={toast} />}
      {modal === 'reschedule' && <RescheduleModal mappingId={row.id} clientId={clientId} candidateName={name} companyName="" rmId={rmId} onClose={() => setModal(null)} toast={toast} />}
    </>
  )
}


// ── Helpers ───────────────────────────────────────────────────────────────────
function isToday(iso: string): boolean {
  const d = new Date(iso), n = new Date()
  return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
}

function fmtSlot(iso: string): string {
  const d = new Date(iso), n = new Date()
  const tom = new Date(n); tom.setDate(n.getDate() + 1)
  const t = d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === n.toDateString()) return `Today · ${t}`
  if (d.toDateString() === tom.toDateString()) return `Tomorrow · ${t}`
  return d.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) + ' · ' + t
}


const TERMINAL_STAGES = ['placed', 'rejected', 'not_interested', 'declined_for_interview', 'client_closed']

// ── Candidate cell (one cell in the table) ────────────────────────────────────
function CandidateCell({ row, clientId, rmId, toast }: {
  row: CandidateRow; clientId: string; rmId: string
  toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const st = candStatusLabel(row.stage)
  const now = new Date()
  const slotToday = row.interview_slot ? isToday(row.interview_slot) : false
  const slotPast  = row.interview_slot ? new Date(row.interview_slot) < now : false
  const accentCls = slotToday ? 'border-l-red-400 bg-red-50/40' : row.interview_slot && !slotPast ? 'border-l-emerald-400' : 'border-l-gray-200'

  return (
    <div className={`bg-white rounded-md border border-gray-100 border-l-[3px] ${accentCls} px-2.5 py-2`}>
      {/* Name + score */}
      <div className="flex items-center justify-between gap-1 mb-0.5">
        <p className="font-semibold text-gray-900 truncate text-[12px]">{row.candidate_name ?? '—'}</p>
        {row.match_score != null && (
          <span className={`shrink-0 text-[9px] font-bold px-1 py-px rounded ${
            row.match_score >= 75 ? 'bg-green-100 text-green-700' :
            row.match_score >= 50 ? 'bg-amber-100 text-amber-700' :
                                    'bg-red-100 text-red-600'
          }`}>{row.match_score}%</span>
        )}
      </div>

      {/* Phone — large */}
      {row.candidate_phone && (
        <p className="text-[11px] font-semibold text-gray-700 tracking-wide mb-0.5">{row.candidate_phone}</p>
      )}

      {/* Location + salary */}
      <div className="flex items-center gap-2 mb-1 flex-wrap">
        {row.current_location && (
          <span title={row.current_location} className="text-[10px] text-gray-400 truncate max-w-[90px] cursor-default">📍 {row.current_location}</span>
        )}
        {row.current_salary != null && (
          <span className="text-[11px] font-semibold text-indigo-600 shrink-0">{fmtSalary(row.current_salary)}</span>
        )}
        {row.expected_salary != null && (
          <span className="text-[10px] text-gray-400 shrink-0">→ {fmtSalary(row.expected_salary)}</span>
        )}
      </div>

      {/* Interview time OR status */}
      <div className="mb-1.5 text-[9px]">
        {row.interview_slot ? (
          <span className={`font-semibold ${slotPast ? 'text-gray-300 line-through' : slotToday ? 'text-red-600' : 'text-emerald-600'}`}>
            {slotToday ? '🔴 ' : '🟢 '}{fmtSlot(row.interview_slot)}
          </span>
        ) : (
          <span className={`px-1.5 py-px rounded-full font-medium ${st.cls}`}>{st.label}</span>
        )}
      </div>

      {/* Actions */}
      <div className="text-[10px]">
        <CandidateActions row={row} clientId={clientId} rmId={rmId} toast={toast} />
      </div>
    </div>
  )
}

// ── Client column header ──────────────────────────────────────────────────────
function ClientColumnHeader({ client, candidateCount, hasToday, todayCount, rmId, toast }: {
  client: ClientItem; candidateCount: number; hasToday: boolean; todayCount: number
  rmId: string; toast: (t: 'success'|'info'|'warn', m: string) => void
}) {
  const qc = useQueryClient()
  const [portalModal, setPortalModal]         = useState(false)
  const [slotsModal,  setSlotsModal]          = useState(false)
  const [churnModal,  setChurnModal]          = useState(false)
  const [wonModal,    setWonModal]            = useState(false)
  const [autoMatchModal, setAutoMatchModal]   = useState(false)
  const [includeMuslim,  setIncludeMuslim]    = useState(false)
  const status = clientStatusLabel(client)

  const autoMatch = useMutation({
    mutationFn: (inclMuslim: boolean) => api.post(`/clients/${client.client_id}/mappings/auto`, { include_muslim: inclMuslim }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['rm-mappings', client.client_id] })
      qc.invalidateQueries({ queryKey: ['rm-actions', rmId] })
      toast('success', 'Auto-match complete')
    },
    onError: () => toast('warn', 'Auto-match failed'),
  })

  const headerBg   = hasToday ? 'bg-red-600'    : 'bg-slate-800'
  const subTextCls = hasToday ? 'text-red-200'  : 'text-slate-400'

  return (
    <th className="border-r border-gray-200 p-0 align-top text-left font-normal" style={{ width: 250, minWidth: 250 }}>
      <div className={`${headerBg} px-3 pt-3 pb-2.5`}>
        {/* Top row: company + icon actions */}
        <div className="flex items-start justify-between gap-2 mb-1">
          <div className="min-w-0">
            {hasToday && (
              <span className="text-[8px] font-bold text-red-600 bg-white px-1.5 py-px rounded-full mb-1 inline-block">
                🔴 {todayCount} TODAY
              </span>
            )}
            <p className="text-[12px] font-bold text-white leading-snug truncate">{client.company_name}</p>
            {client.job_title && <p className={`text-[10px] truncate ${subTextCls}`}>{client.job_title}</p>}
            {client.poc_phone && <p className={`text-[9px] font-mono mt-0.5 ${subTextCls}`}>{client.poc_name ? `${client.poc_name} · ` : ''}{client.poc_phone}</p>}
          </div>
          {/* Just two icon buttons top-right */}
          <div className="flex items-center gap-1 shrink-0 mt-0.5">
            <a href={`/clients/${client.client_id}`} target="_blank" rel="noreferrer"
              title="Open client detail"
              className="w-6 h-6 flex items-center justify-center rounded text-white/60 hover:text-white hover:bg-white/10 text-[11px]">
              ↗
            </a>
            <button onClick={() => setChurnModal(true)} title="More / churn"
              className="w-6 h-6 flex items-center justify-center rounded text-white/60 hover:text-white hover:bg-white/10 text-[11px]">
              ···
            </button>
          </div>
        </div>

        {/* Stage pill + count */}
        <div className="flex items-center gap-1.5 mt-1.5 mb-2">
          <span className="text-[9px] px-1.5 py-px rounded-full bg-white/15 text-white font-medium">{status.label}</span>
          {candidateCount >= 0 && (
            <span className={`text-[9px] ${subTextCls}`}>{candidateCount} candidate{candidateCount !== 1 ? 's' : ''}</span>
          )}
        </div>

        {/* Primary actions — one per row, text labels */}
        <div className="flex gap-1">
          <button onClick={() => setSlotsModal(true)}
            className="flex-1 py-1 text-[10px] font-semibold rounded bg-white/10 hover:bg-white/20 text-white text-center">
            📅 Slots
          </button>
          <button onClick={() => { setIncludeMuslim(false); setAutoMatchModal(true) }} disabled={autoMatch.isPending}
            className="flex-1 py-1 text-[10px] font-semibold rounded bg-white/10 hover:bg-white/20 text-white text-center disabled:opacity-40">
            {autoMatch.isPending ? '⏳' : '🔍 Match'}
          </button>
          {client.stage !== 'deal_won' && (
            <button onClick={() => setWonModal(true)}
              className="flex-1 py-1 text-[10px] font-semibold rounded bg-white/10 hover:bg-white/20 text-white text-center">
              🏆 Won
            </button>
          )}
        </div>
      </div>

      {slotsModal && <SetClientSlotsModal clientId={client.client_id} companyName={client.company_name}
        rmId={rmId} onClose={() => setSlotsModal(false)} toast={toast} />}
      {churnModal && <ChurnModal client={client} rmId={rmId} onClose={() => setChurnModal(false)} toast={toast} />}
      {wonModal && <WonDealModal client={client} rmId={rmId} onClose={() => setWonModal(false)} toast={toast} />}
      {autoMatchModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4" onClick={() => setAutoMatchModal(false)}>
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm" onClick={e => e.stopPropagation()}>
            <h2 className="text-base font-semibold mb-1">Auto-Match — {client.company_name}</h2>
            <p className="text-sm text-gray-500 mb-5">Choose which candidates to include in this match run.</p>
            <label className="flex items-center gap-3 cursor-pointer mb-6">
              <input
                type="checkbox"
                checked={includeMuslim}
                onChange={e => setIncludeMuslim(e.target.checked)}
                className="w-4 h-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-sm text-gray-700">Include Muslim candidates</span>
            </label>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setAutoMatchModal(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => { setAutoMatchModal(false); autoMatch.mutate(includeMuslim) }}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Run Match
              </button>
            </div>
          </div>
        </div>
      )}
    </th>
  )
}

// ── PIN screen ────────────────────────────────────────────────────────────────
function PinLoginScreen({ rmId, onUnlock }: { rmId: string; onUnlock: () => void }) {
  const [pin, setPin] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const submit = async () => {
    if (!pin) return
    setLoading(true); setError('')
    try {
      await api.post(`/admin/rms/${rmId}/verify-pin`, { pin })
      sessionStorage.setItem(`rm_auth_${rmId}`, '1')
      onUnlock()
    } catch (e: any) {
      setError(e?.response?.data?.detail || 'Incorrect PIN')
    } finally { setLoading(false) }
  }
  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-xs text-center">
        <div className="w-12 h-12 bg-indigo-100 rounded-full flex items-center justify-center mx-auto mb-4 text-2xl">🔒</div>
        <h1 className="text-lg font-bold text-gray-900 mb-1">RM Dashboard</h1>
        <p className="text-xs text-gray-400 mb-6">Enter your PIN to continue</p>
        <input type="password" inputMode="numeric" maxLength={8} value={pin}
          onChange={e => { setPin(e.target.value); setError('') }}
          onKeyDown={e => e.key === 'Enter' && submit()}
          placeholder="Enter PIN"
          className="w-full border border-gray-200 rounded-xl px-4 py-3 text-center text-lg tracking-widest font-mono focus:outline-none focus:ring-2 focus:ring-indigo-400 mb-3"
          autoFocus />
        {error && <p className="text-xs text-red-500 mb-3">{error}</p>}
        <button onClick={submit} disabled={loading || !pin}
          className="w-full py-3 rounded-xl bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-100 disabled:text-gray-400 text-white text-sm font-semibold">
          {loading ? 'Verifying…' : 'Unlock'}
        </button>
      </div>
    </div>
  )
}

// ── Status legend ─────────────────────────────────────────────────────────────
const LEGEND_ITEMS = [
  { label: 'Matched',          cls: 'bg-gray-100 text-gray-500',         meaning: 'AI selected, not yet contacted' },
  { label: 'Reached Out',      cls: 'bg-sky-100 text-sky-700',           meaning: 'JD sent via WhatsApp, awaiting reply' },
  { label: 'Interested',       cls: 'bg-blue-100 text-blue-700',         meaning: 'Candidate replied yes to JD' },
  { label: 'Shortlisted',      cls: 'bg-violet-100 text-violet-700',     meaning: 'Passed evaluation — RM to invite for interview' },
  { label: 'Slot Sent',        cls: 'bg-teal-100 text-teal-700',         meaning: 'Slot-selection link sent to candidate, awaiting booking' },
  { label: 'Slot Booked',      cls: 'bg-teal-200 text-teal-800',         meaning: 'Candidate picked a slot — interview confirmed' },
  { label: 'Interview Done',   cls: 'bg-amber-100 text-amber-700',       meaning: 'Interview completed, awaiting client decision' },
  { label: 'Hired ✓',          cls: 'bg-green-100 text-green-700',       meaning: 'Placed with client' },
  { label: 'Rejected',         cls: 'bg-red-100 text-red-600',           meaning: 'Client rejected after interview' },
  { label: 'Not Interested',   cls: 'bg-gray-100 text-gray-500',         meaning: 'Candidate declined the JD' },
  { label: 'Declined',         cls: 'bg-red-100 text-red-500',           meaning: 'Candidate declined interview invite' },
  { label: 'Waitlisted',       cls: 'bg-amber-100 text-amber-600',       meaning: 'On hold — may be called if position reopens' },
]

function StatusLegend() {
  const [open, setOpen] = useState(false)
  return (
    <div className="border-b border-gray-200 bg-white shrink-0">
      <button onClick={() => setOpen(o => !o)}
        className="w-full flex items-center gap-2 px-4 py-2 text-[11px] text-gray-500 hover:bg-gray-50 text-left">
        <span className="font-semibold text-gray-700">Status Guide</span>
        <span className="text-gray-400">{open ? '▲ hide' : '▼ show what each status means'}</span>
      </button>
      {open && (
        <div className="px-4 pb-3 flex flex-wrap gap-x-5 gap-y-2">
          {LEGEND_ITEMS.map(item => (
            <div key={item.label} className="flex items-center gap-1.5">
              <span className={`text-[9px] px-1.5 py-px rounded-full font-semibold whitespace-nowrap ${item.cls}`}>{item.label}</span>
              <span className="text-[10px] text-gray-400">{item.meaning}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Main inner page ───────────────────────────────────────────────────────────
const ACTIVE_STAGES  = ['onboarded', 'agreement_sent']
const CLOSED_STAGES  = ['churned', 'deal_won', 'disqualified']

function RMPageInner() {
  const { rmId } = useParams<{ rmId: string }>()
  const [toasts, setToasts]   = useState<Toast[]>([])
  const [search, setSearch]   = useState('')
  const [tab, setTab]         = useState<'active'|'closed'>('active')

  const addToast = (type: 'success'|'info'|'warn', msg: string) => {
    const id = ++_toastId
    setToasts(t => [...t, { id, type, msg }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 3500)
  }
  const removeToast = (id: number) => setToasts(t => t.filter(x => x.id !== id))

  const { data, isLoading, isError } = useQuery({
    queryKey: ['rm-actions', rmId],
    queryFn: () => getRMActions(rmId!),
    refetchInterval: 2 * 60 * 1000,
    retry: false,
  })

  const { rm, clients = [] } = data ?? { rm: null as any, clients: [] as ClientItem[] }

  const mappingQueries = useQueries({
    queries: clients.map((c: ClientItem) => ({
      queryKey: ['rm-mappings', c.client_id],
      queryFn: () => getMappings(c.client_id),
      staleTime: 30_000,
      enabled: !!clients.length,
    }))
  })

  const now = new Date()

  // Build per-client candidate arrays: AI-scored only, active only, sorted
  type ColEntry = { client: ClientItem; candidates: CandidateRow[]; loading: boolean }
  const colsRaw: ColEntry[] = clients.map((c: ClientItem, i: number) => {
    const all = (mappingQueries[i]?.data ?? []) as CandidateRow[]
    const active = all
      .filter(m => m.match_score != null && !TERMINAL_STAGES.includes(m.stage))
      .sort((a, b) => {
        // 1. Today's interview always first
        const aToday = a.interview_slot ? isToday(a.interview_slot) : false
        const bToday = b.interview_slot ? isToday(b.interview_slot) : false
        if (aToday && !bToday) return -1
        if (!aToday && bToday) return 1
        // 2. Stage priority (most actionable first, reached-out last)
        const pDiff = stagePriority(a.stage) - stagePriority(b.stage)
        if (pDiff !== 0) return pDiff
        // 3. Within same stage: upcoming slot time, then match score
        const aSlot = a.interview_slot ? new Date(a.interview_slot).getTime() : Infinity
        const bSlot = b.interview_slot ? new Date(b.interview_slot).getTime() : Infinity
        if (aSlot !== bSlot) return aSlot - bSlot
        return (b.match_score ?? 0) - (a.match_score ?? 0)
      })
    return { client: c, candidates: active, loading: mappingQueries[i]?.isLoading ?? true }
  })

  // Tab + search filter
  const stageFilter = tab === 'active' ? ACTIVE_STAGES : CLOSED_STAGES
  const colsFiltered = colsRaw.filter(({ client }) =>
    stageFilter.includes(client.stage) &&
    (client.company_name.toLowerCase().includes(search.toLowerCase()) ||
    (client.job_title ?? '').toLowerCase().includes(search.toLowerCase()))
  )

  // Sort columns: today → upcoming → pipeline count
  const cols = [...colsFiltered].sort((a, b) => {
    const aToday = a.candidates.some(m => m.interview_slot && isToday(m.interview_slot))
    const bToday = b.candidates.some(m => m.interview_slot && isToday(m.interview_slot))
    if (aToday && !bToday) return -1
    if (!aToday && bToday) return 1
    const nextSlot = (cands: CandidateRow[]) => cands.reduce<number>((min, m) => {
      if (!m.interview_slot) return min
      const t = new Date(m.interview_slot).getTime()
      return t > now.getTime() && t < min ? t : min
    }, Infinity)
    const aNext = nextSlot(a.candidates), bNext = nextSlot(b.candidates)
    if (aNext !== bNext) return aNext - bNext
    return b.client.pipeline_count - a.client.pipeline_count
  })

  const maxRows = cols.length ? Math.max(...cols.map(c => c.candidates.length), 0) : 0
  const todayClientCount = cols.filter(c => c.candidates.some(m => m.interview_slot && isToday(m.interview_slot))).length
  const today = new Date().toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })

  if (isLoading) return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center">
      <div className="w-6 h-6 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" />
    </div>
  )
  if (isError || !data) return (
    <div className="min-h-screen bg-gray-100 flex items-center justify-center">
      <p className="text-sm text-gray-400">RM page not found.</p>
    </div>
  )

  return (
    <div className="min-h-screen bg-gray-100 flex flex-col">
      <ToastStack toasts={toasts} remove={removeToast} />

      {/* Sticky header */}
      <div className="bg-white border-b border-gray-200 px-5 py-3 sticky top-0 z-40 flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-full bg-indigo-100 flex items-center justify-center text-sm font-bold text-indigo-700">
            {rm?.name?.split(' ').map((w: string) => w[0]).join('').slice(0, 2).toUpperCase()}
          </div>
          <div>
            <p className="text-sm font-bold text-gray-900">{rm?.name}</p>
            <p className="text-[10px] text-gray-400">{today}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {todayClientCount > 0 && (
            <span className="text-[10px] font-bold px-2.5 py-1 rounded-full bg-red-100 text-red-600">
              🔴 {todayClientCount} client{todayClientCount > 1 ? 's' : ''} with interview today
            </span>
          )}
          <span className="text-[10px] font-semibold px-2.5 py-1 rounded-full bg-blue-100 text-blue-700">
            {clients.length} clients
          </span>
        </div>
      </div>

      {/* Search bar */}
      {/* Tabs + search */}
      <div className="bg-white border-b border-gray-200 sticky top-[57px] z-30 shrink-0 flex items-center gap-4 px-4 py-2">
        <div className="flex gap-1 shrink-0">
          {([
            { key: 'active', label: 'Active', count: colsRaw.filter(c => ACTIVE_STAGES.includes(c.client.stage)).length },
            { key: 'closed', label: 'Closed', count: colsRaw.filter(c => CLOSED_STAGES.includes(c.client.stage)).length },
          ] as const).map(t => (
            <button key={t.key} onClick={() => { setTab(t.key); setSearch('') }}
              className={`px-3 py-1.5 text-xs font-semibold rounded-lg transition-colors ${
                tab === t.key
                  ? 'bg-indigo-600 text-white'
                  : 'text-gray-500 hover:bg-gray-100'
              }`}>
              {t.label}
              <span className={`ml-1.5 text-[10px] px-1 py-0.5 rounded-full ${
                tab === t.key ? 'bg-white/20 text-white' : 'bg-gray-100 text-gray-400'
              }`}>{t.count}</span>
            </button>
          ))}
        </div>
        <input value={search} onChange={e => setSearch(e.target.value)}
          placeholder={`Filter ${tab} clients…`}
          className="flex-1 max-w-sm text-xs border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-indigo-300" />
      </div>

      {/* Status legend */}
      <StatusLegend />

      {/* Table — horizontal scroll */}
      <div className="flex-1 overflow-auto p-4">
        {cols.length === 0 ? (
          <p className="text-sm text-gray-400 text-center py-20">No clients found</p>
        ) : (
          <table className="border-collapse" style={{ tableLayout: 'fixed', width: cols.length * 244 }}>
            {/* Client column headers */}
            <thead>
              <tr>
                {cols.map(({ client, candidates, loading }) => {
                  const hasToday   = candidates.some(m => m.interview_slot && isToday(m.interview_slot))
                  const todayCount = candidates.filter(m => m.interview_slot && isToday(m.interview_slot)).length
                  return (
                    <ClientColumnHeader key={client.client_id}
                      client={client} candidateCount={loading ? -1 : candidates.length}
                      hasToday={hasToday} todayCount={todayCount}
                      rmId={rmId!} toast={addToast} />
                  )
                })}
              </tr>
            </thead>

            {/* Candidate rows */}
            <tbody>
              {maxRows === 0 ? (
                <tr>
                  {cols.map(({ client, loading }) => (
                    <td key={client.client_id} className="border border-gray-100 p-2 align-top">
                      {loading ? (
                        <div className="space-y-1.5">
                          <div className="h-2.5 bg-gray-100 rounded animate-pulse w-3/4" />
                          <div className="h-2.5 bg-gray-100 rounded animate-pulse w-1/2" />
                        </div>
                      ) : (
                        <p className="text-[10px] text-gray-300 text-center py-2">No matched candidates</p>
                      )}
                    </td>
                  ))}
                </tr>
              ) : (
                Array.from({ length: maxRows }).map((_, rowIdx) => (
                  <tr key={rowIdx}>
                    {cols.map(({ client, candidates, loading }) => {
                      const cand = candidates[rowIdx]
                      return (
                        <td key={client.client_id} className="border border-gray-100 p-1.5 align-top bg-white">
                          {loading && rowIdx === 0 ? (
                            <div className="space-y-1.5 p-1">
                              <div className="h-2.5 bg-gray-100 rounded animate-pulse w-3/4" />
                              <div className="h-2.5 bg-gray-100 rounded animate-pulse w-1/2" />
                            </div>
                          ) : cand ? (
                            <CandidateCell row={cand} clientId={client.client_id} rmId={rmId!} toast={addToast} />
                          ) : null}
                        </td>
                      )
                    })}
                  </tr>
                ))
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default function RMPage() {
  const { rmId } = useParams<{ rmId: string }>()
  const [unlocked, setUnlocked] = useState(() => !!sessionStorage.getItem(`rm_auth_${rmId}`))
  if (!unlocked) return <PinLoginScreen rmId={rmId!} onUnlock={() => setUnlocked(true)} />
  return <RMPageInner />
}
