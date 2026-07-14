import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getClientPortal,
  submitSlotsFromPortal,
  inviteCandidate,
  rejectCandidateFromPortal,
  submitClientFeedbackFromPortal,
  type ApprovalCandidate,
  type FeedbackCandidate,
} from '../api/outreach'

// ── Calendar helpers (same as SchedulePage) ───────────────────────────────────

const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December']
const DAYS_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
const TIME_SLOTS = [
  '9:00 AM','10:00 AM','11:00 AM','12:00 PM',
  '1:00 PM','2:00 PM','3:00 PM','4:00 PM','5:00 PM','6:00 PM','7:00 PM','8:00 PM',
]
const MIN_SLOTS = 4

function getCalendarDays(year: number, month: number): (number | null)[] {
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = Array(firstDay).fill(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  return cells
}

function parseTime(t: string) {
  const [timePart, period] = t.split(' ')
  const [h, m] = timePart.split(':').map(Number)
  let hour = h
  if (period === 'PM' && h !== 12) hour += 12
  if (period === 'AM' && h === 12) hour = 0
  return { hour, minute: m }
}

function makeLabel(date: Date, timeStr: string): string {
  const { hour, minute } = parseTime(timeStr)
  const start = new Date(date)
  start.setHours(hour, minute, 0, 0)
  const end = new Date(start.getTime() + 60 * 60 * 1000)
  const dateLabel = date.toLocaleDateString('en-IN', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
  const fmt = (d: Date) =>
    d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true })
  return `${dateLabel} · ${fmt(start)} – ${fmt(end)}`
}

function slotKey(date: Date, timeStr: string) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}|${timeStr}`
}

function isSameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
}

// ── Activity feed helpers ─────────────────────────────────────────────────────

function timeAgo(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime()
  const mins  = Math.floor(diff / 60_000)
  const hours = Math.floor(diff / 3_600_000)
  const days  = Math.floor(diff / 86_400_000)
  if (mins  < 1)  return 'just now'
  if (mins  < 60) return `${mins}m ago`
  if (hours < 24) return `${hours}h ago`
  return `${days}d ago`
}

const EVENT_META: Record<string, { icon: string; label: string; color: string }> = {
  sourced:        { icon: '🔍', label: 'Candidate matched to your role',         color: 'text-gray-600' },
  jd_sent:        { icon: '📨', label: 'Job description sent to candidate',       color: 'text-blue-600' },
  interested:     { icon: '✅', label: 'Candidate expressed interest',            color: 'text-green-600' },
  shortlisted:    { icon: '⭐', label: 'Candidate shortlisted for your review',   color: 'text-indigo-600' },
  invited:        { icon: '📅', label: 'Candidate invited for interview',         color: 'text-teal-600' },
  interview_done: { icon: '🤝', label: 'Interview completed',                     color: 'text-purple-600' },
  placed:         { icon: '🎉', label: 'Candidate placed!',                       color: 'text-green-700' },
}

function ActivityFeed({ events }: { events: { type: string; at: string }[] }) {
  if (!events.length) return null
  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-4">Recent Activity</p>
      <div className="space-y-3">
        {events.map((e, i) => {
          const meta = EVENT_META[e.type] ?? { icon: '•', label: e.type, color: 'text-gray-500' }
          return (
            <div key={i} className="flex items-start gap-3">
              <span className="text-base leading-none mt-0.5 shrink-0">{meta.icon}</span>
              <div className="flex-1 min-w-0">
                <p className={`text-xs font-medium ${meta.color}`}>{meta.label}</p>
              </div>
              <span className="text-[10px] text-gray-400 shrink-0 pt-0.5">{timeAgo(e.at)}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function PipelineFunnel({ ov }: { ov: Record<string, number> }) {
  const steps = [
    { key: 'sourced',       label: 'Sourced',        desc: 'Candidates matched',         color: 'text-gray-700',   dot: 'bg-gray-400' },
    { key: 'jd_sent',       label: 'JD Sent',         desc: 'Job description shared',     color: 'text-blue-600',   dot: 'bg-blue-400' },
    { key: 'replied',       label: 'Replied',         desc: 'Expressed interest',         color: 'text-sky-600',    dot: 'bg-sky-400' },
    { key: 'passed_screen', label: 'Screened',        desc: 'Passed our criteria',        color: 'text-amber-600',  dot: 'bg-amber-400' },
    { key: 'in_review',     label: 'In Review',       desc: 'Awaiting your decision',     color: 'text-indigo-600', dot: 'bg-indigo-400' },
    { key: 'placed',        label: 'Placed',          desc: 'Hired successfully',         color: 'text-green-600',  dot: 'bg-green-500' },
  ]

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-5">Hiring Pipeline</p>
      <div className="space-y-3">
        {steps.map((step, i) => {
          const val = ov[step.key] ?? 0
          const prevVal = i === 0 ? null : (ov[steps[i - 1].key] ?? 0)
          const pct = prevVal ? Math.round((val / prevVal) * 100) : null
          return (
            <div key={step.key}>
              <div className="flex items-center gap-3">
                {/* Connector line on left */}
                <div className="flex flex-col items-center shrink-0 w-4">
                  {i > 0 && <div className="w-px h-3 bg-gray-200 mb-0.5" />}
                  <div className={`w-2.5 h-2.5 rounded-full ${val > 0 ? step.dot : 'bg-gray-200'}`} />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-baseline gap-2">
                    <span className={`text-xl font-bold ${val > 0 ? step.color : 'text-gray-300'}`}>{val}</span>
                    <span className="text-xs font-semibold text-gray-700">{step.label}</span>
                    <span className="text-[10px] text-gray-400 hidden sm:inline">{step.desc}</span>
                  </div>
                </div>
                {pct !== null && val > 0 && (
                  <span className="text-[10px] text-gray-400 shrink-0">{pct}% converted</span>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function ProcessExplainer() {
  const steps = [
    { n: '1', title: 'Source & Match',    body: 'We find candidates matching your job requirements — salary, location, skills, and experience.' },
    { n: '2', title: 'Share JD & Screen', body: 'We share your job description and shortlist only those who express genuine interest.' },
    { n: '3', title: 'AI Interview',      body: 'Every interested candidate completes a 5-minute AI call testing their accounting knowledge.' },
    { n: '4', title: 'You Decide',        body: 'Only candidates who pass our evaluation are presented here for your final review.' },
  ]
  return (
    <div className="bg-indigo-50 border border-indigo-100 rounded-2xl p-5">
      <p className="text-xs font-semibold text-indigo-400 uppercase tracking-widest mb-4">Our Screening Process</p>
      <div className="space-y-3">
        {steps.map(s => (
          <div key={s.n} className="flex gap-3">
            <span className="w-5 h-5 rounded-full bg-indigo-600 text-white text-[10px] font-bold flex items-center justify-center shrink-0 mt-0.5">{s.n}</span>
            <div>
              <p className="text-xs font-semibold text-indigo-800">{s.title}</p>
              <p className="text-[11px] text-indigo-600 leading-relaxed">{s.body}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ── Shared helpers ────────────────────────────────────────────────────────────

const LABEL_COLORS: Record<string, string> = {
  'Quick Joiner':       'bg-green-100 text-green-700',
  'High Stability':     'bg-blue-100 text-blue-700',
  'Average Stability':  'bg-amber-100 text-amber-700',
  'High Turnover Risk': 'bg-red-100 text-red-600',
}

function Avatar({ name, photoUrl }: { name: string; photoUrl: string | null }) {
  const initials = name.trim().split(/\s+/).map(p => p[0]?.toUpperCase() ?? '').slice(0, 2).join('')
  if (photoUrl) {
    return <img src={photoUrl} alt={name} className="w-14 h-14 rounded-full object-cover border-2 border-white shadow" />
  }
  return (
    <div className="w-14 h-14 rounded-full bg-gradient-to-br from-blue-400 to-indigo-600 flex items-center justify-center text-white font-bold text-lg shadow">
      {initials}
    </div>
  )
}

function MmsBadge({ score }: { score: number }) {
  const color = score >= 75 ? 'bg-green-100 text-green-700' : score >= 50 ? 'bg-amber-100 text-amber-700' : 'bg-red-100 text-red-600'
  return <span className={`text-[10px] px-2 py-0.5 rounded-full font-semibold ${color}`}>MMS {score.toFixed(0)}%</span>
}

// ── Slot submission form ──────────────────────────────────────────────────────

function SlotSubmissionForm({ token, pocName, onSuccess }: {
  token: string
  pocName: string
  onSuccess: () => void
}) {
  const today = new Date(); today.setHours(0, 0, 0, 0)
  const earliest = new Date(today); earliest.setDate(earliest.getDate() + 1)

  const [viewDate, setViewDate] = useState(new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const [pickedKeys, setPickedKeys] = useState<Set<string>>(new Set())
  const [pickedLabels, setPickedLabels] = useState<Map<string, string>>(new Map())

  const submitMut = useMutation({
    mutationFn: () => submitSlotsFromPortal(token, {
      client_name: pocName,
      slots: Array.from(pickedLabels.values()),
    }),
    onSuccess,
  })

  const toggleSlot = (date: Date, timeStr: string) => {
    const key = slotKey(date, timeStr)
    setPickedKeys(prev => { const n = new Set(prev); n.has(key) ? n.delete(key) : n.add(key); return n })
    setPickedLabels(prev => { const n = new Map(prev); n.has(key) ? n.delete(key) : n.set(key, makeLabel(date, timeStr)); return n })
  }

  const calDays = getCalendarDays(viewDate.getFullYear(), viewDate.getMonth())
  const canGoPrev = viewDate.getFullYear() > today.getFullYear() || viewDate.getMonth() > today.getMonth()
  const needsMore = pickedKeys.size > 0 && pickedKeys.size < MIN_SLOTS

  return (
    <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
      <div className="flex gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-5">
        <span className="text-amber-500 shrink-0">💡</span>
        <p className="text-xs text-amber-700 leading-relaxed">
          <span className="font-semibold">Tip:</span> Most candidates are free <span className="font-semibold">before 11 AM or after 6 PM</span> — including a few slots in those windows helps.
        </p>
      </div>

      {/* Month nav */}
      <div className="flex items-center justify-between mb-4">
        <p className="text-sm font-semibold text-gray-900">{MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}</p>
        <div className="flex gap-1">
          <button onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1))}
            disabled={!canGoPrev}
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 disabled:opacity-30">
            ‹
          </button>
          <button onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1))}
            className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100">
            ›
          </button>
        </div>
      </div>

      {/* Calendar */}
      <div className="grid grid-cols-7 mb-1">
        {DAYS_SHORT.map(d => <div key={d} className="text-center text-[10px] font-medium text-gray-400 py-1">{d}</div>)}
      </div>
      <div className="grid grid-cols-7 gap-y-1 mb-5">
        {calDays.map((day, i) => {
          if (!day) return <div key={i} />
          const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day)
          const isPast = cellDate < earliest
          const isSelected = selectedDate ? isSameDay(cellDate, selectedDate) : false
          return (
            <button key={i} onClick={() => !isPast && setSelectedDate(cellDate)} disabled={isPast}
              className={`mx-auto w-9 h-9 flex items-center justify-center rounded-full text-sm transition-colors
                ${isPast ? 'text-gray-300 cursor-not-allowed' : 'cursor-pointer hover:bg-blue-50 hover:text-blue-600'}
                ${isSelected ? 'bg-blue-600 text-white font-semibold' : ''}
                ${!isPast && !isSelected ? 'text-gray-800' : ''}`}>
              {day}
            </button>
          )
        })}
      </div>

      {/* Time slots */}
      {selectedDate ? (
        <div className="mb-5">
          <p className="text-xs font-medium text-gray-600 mb-2">
            {selectedDate.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
          </p>
          <div className="grid grid-cols-3 gap-2">
            {TIME_SLOTS.map(timeStr => {
              const key = slotKey(selectedDate, timeStr)
              const picked = pickedKeys.has(key)
              return (
                <button key={timeStr} onClick={() => toggleSlot(selectedDate, timeStr)}
                  className={`py-2 px-2 rounded-lg border text-xs font-medium transition-all
                    ${picked ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-200 text-gray-700 hover:border-blue-400 hover:text-blue-600'}`}>
                  {timeStr}
                </button>
              )
            })}
          </div>
        </div>
      ) : (
        <p className="text-xs text-gray-400 mb-5">Select a date to see available times.</p>
      )}

      {/* Selected chips */}
      {pickedKeys.size > 0 && (
        <div className="mb-5">
          <p className="text-[10px] font-medium text-gray-500 mb-2">Selected slots ({pickedKeys.size})</p>
          {needsMore && (
            <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-2">
              Pick at least {MIN_SLOTS} slots — you've picked {pickedKeys.size} so far.
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {Array.from(pickedLabels.entries()).map(([key, label]) => (
              <span key={key} className="inline-flex items-center gap-1 text-[10px] bg-blue-50 text-blue-700 border border-blue-100 rounded-full px-2.5 py-1">
                {label}
                <button onClick={() => {
                  setPickedKeys(prev => { const n = new Set(prev); n.delete(key); return n })
                  setPickedLabels(prev => { const n = new Map(prev); n.delete(key); return n })
                }} className="text-blue-400 hover:text-blue-700">×</button>
              </span>
            ))}
          </div>
        </div>
      )}

      <button
        onClick={() => submitMut.mutate()}
        disabled={pickedKeys.size === 0 || needsMore || submitMut.isPending}
        className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-100 disabled:text-gray-400 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
      >
        {submitMut.isPending ? 'Confirming…'
          : pickedKeys.size === 0 ? 'Select at least 4 slots'
          : needsMore ? `Pick ${MIN_SLOTS - pickedKeys.size} more slot${MIN_SLOTS - pickedKeys.size > 1 ? 's' : ''}`
          : `Confirm ${pickedKeys.size} slot${pickedKeys.size > 1 ? 's' : ''}`}
      </button>
      {submitMut.isError && (
        <p className="text-red-500 text-xs text-center mt-2">
          {(submitMut.error as any)?.response?.data?.detail || 'Something went wrong. Please try again.'}
        </p>
      )}
    </div>
  )
}

// ── Review Candidates tab ─────────────────────────────────────────────────────

const NOTICE_PERIOD_LABELS: Record<string, string> = {
  'Immediate':        'Immediate',
  'Short Notice':     '15 days – 1 month',
  'Long Notice':      '2 – 3 months',
  'Very Long Notice': '3+ months',
}

function formatNoticePeriod(val: string | null | undefined): string {
  if (!val) return '—'
  if (NOTICE_PERIOD_LABELS[val]) return NOTICE_PERIOD_LABELS[val]
  if (/^\d+$/.test(val.trim())) return `${val} days`
  return val
}

const DECLINE_REASONS = ['Salary expectation', 'Location', 'Experience level', 'Other']

function ApprovalCard({ token, candidate }: { token: string; candidate: ApprovalCandidate }) {
  const qc = useQueryClient()
  const [pickedReason, setPickedReason] = useState('')
  const [otherText, setOtherText] = useState('')
  const [showDeclineForm, setShowDeclineForm] = useState(false)
  const [done, setDone] = useState<'invited' | 'declined' | null>(null)

  const inviteMut = useMutation({
    mutationFn: () => inviteCandidate(token, candidate.mapping_id),
    onSuccess: () => { setDone('invited'); qc.invalidateQueries({ queryKey: ['client-portal', token] }) },
  })
  const declineMut = useMutation({
    mutationFn: () => {
      const reason = pickedReason === 'Other' ? (otherText.trim() || 'Other') : pickedReason
      return rejectCandidateFromPortal(token, candidate.mapping_id, reason || undefined)
    },
    onSuccess: () => { setDone('declined'); qc.invalidateQueries({ queryKey: ['client-portal', token] }) },
  })

  if (done === 'invited') return (
    <div className="bg-green-50 border border-green-200 rounded-2xl p-5 text-center">
      <p className="text-green-700 font-semibold text-sm">✓ Interview slot link sent to {candidate.name}</p>
    </div>
  )
  if (done === 'declined') return (
    <div className="bg-gray-50 border border-gray-200 rounded-2xl p-5 text-center">
      <p className="text-gray-400 text-sm">{candidate.name} — not a fit</p>
    </div>
  )

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-full bg-blue-100 flex items-center justify-center shrink-0 overflow-hidden">
          {candidate.photo_url
            ? <img src={candidate.photo_url} alt={candidate.name} className="w-full h-full object-cover" />
            : <span className="text-base font-bold text-blue-700">{candidate.initials}</span>
          }
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-bold text-gray-900">{candidate.name}</h2>
            {candidate.age_years && <span className="text-xs text-gray-400">{candidate.age_years} yrs</span>}
            {candidate.current_location && <span className="text-xs text-gray-500">· {candidate.current_location}</span>}
          </div>
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1.5 text-xs text-gray-600">
            <div><span className="text-gray-400">Current Salary: </span><span className="font-medium">₹{candidate.current_salary?.toLocaleString('en-IN') ?? '—'}</span></div>
            <div><span className="text-gray-400">Technical Score: </span><span className="font-medium">{candidate.ai_technical_score ?? '—'}/100</span></div>
            <div><span className="text-gray-400">Software: </span><span className="font-medium">{candidate.accounting_software || '—'}</span></div>
            <div><span className="text-gray-400">Notice: </span><span className="font-medium">{formatNoticePeriod(candidate.notice_period)}</span></div>
            {candidate.job_stability && candidate.job_stability !== 'Unknown' && (
              <div><StabilityBadge label={candidate.job_stability} /></div>
            )}
            {candidate.cv_url && (
              <div><a href={candidate.cv_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline font-medium">View CV ↗</a></div>
            )}
          </div>
        </div>
      </div>

      {candidate.evaluation_report_url && (
        <div className="mt-4 pt-3 border-t border-gray-100">
          <a href={candidate.evaluation_report_url} target="_blank" rel="noreferrer"
            className="text-xs text-blue-600 hover:underline font-medium">Evaluation Report ↗</a>
        </div>
      )}

      <div className="mt-4 pt-3 border-t border-gray-100">
        {!showDeclineForm ? (
          <div className="flex gap-2">
            <button onClick={() => inviteMut.mutate()} disabled={inviteMut.isPending}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-green-50 border border-green-200 text-green-700 text-xs font-semibold hover:bg-green-100 transition-colors disabled:opacity-50">
              {inviteMut.isPending ? 'Sending…' : '✓ Invite for Interview'}
            </button>
            <button onClick={() => setShowDeclineForm(true)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-gray-50 border border-gray-200 text-gray-600 text-xs font-semibold hover:bg-gray-100 transition-colors">
              Not a Fit
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <p className="text-xs text-gray-500">Select a reason:</p>
            <div className="flex flex-wrap gap-2">
              {DECLINE_REASONS.map(r => (
                <button key={r} onClick={() => setPickedReason(r)}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                    pickedReason === r ? 'bg-gray-700 text-white border-gray-700' : 'bg-white text-gray-600 border-gray-300 hover:border-gray-500'
                  }`}>
                  {r}
                </button>
              ))}
            </div>
            {pickedReason === 'Other' && (
              <textarea value={otherText} onChange={e => setOtherText(e.target.value)}
                placeholder="Please describe…" rows={2}
                className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs resize-none focus:outline-none focus:ring-2 focus:ring-gray-300" />
            )}
            <div className="flex gap-2 mt-1">
              <button onClick={() => { setShowDeclineForm(false); setPickedReason('') }}
                className="px-3 py-1.5 rounded-xl border border-gray-200 text-xs text-gray-600 hover:bg-gray-50">
                Back
              </button>
              <button onClick={() => declineMut.mutate()} disabled={!pickedReason || declineMut.isPending}
                className="px-4 py-1.5 rounded-xl bg-gray-700 text-white text-xs font-semibold hover:bg-gray-800 disabled:opacity-50 transition-colors">
                {declineMut.isPending ? 'Confirming…' : 'Confirm'}
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ── Interview Feedback tab (HandoverPage style) ───────────────────────────────

const DISLIKE_REASONS = ['Poor technical knowledge', 'Location', 'Salary', 'Other']

function StabilityBadge({ label }: { label: string }) {
  const cls = label === 'High Stability' ? 'text-green-700' :
    label === 'Average Stability' ? 'text-amber-700' :
    label === 'High Turnover Risk' ? 'text-red-600' : 'text-gray-500'
  return <span className="text-xs text-gray-400">Job Stability: <span className={`font-medium ${cls}`}>{label}</span></span>
}

function MmsRing({ score }: { score: number }) {
  const pct = Math.min(100, Math.max(0, score))
  const color = pct >= 75 ? '#22c55e' : pct >= 50 ? '#f59e0b' : '#ef4444'
  const r = 20, circ = 2 * Math.PI * r
  const dash = (pct / 100) * circ
  return (
    <div style={{ position: 'relative', width: 58, height: 58, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <svg width="58" height="58" style={{ transform: 'rotate(-90deg)', position: 'absolute', top: 0, left: 0 }}>
        <circle cx="29" cy="29" r={r} fill="none" stroke="#f3f4f6" strokeWidth="5" />
        <circle cx="29" cy="29" r={r} fill="none" stroke={color} strokeWidth="5"
          strokeDasharray={`${dash} ${circ}`} strokeLinecap="round" />
      </svg>
      <span style={{ position: 'relative', fontSize: '12px', fontWeight: 700, color: '#1f2937' }}>
        {Math.round(pct)}%
      </span>
    </div>
  )
}

function FeedbackCard({ token, candidate }: { token: string; candidate: FeedbackCandidate }) {
  const qc = useQueryClient()
  const [pickedReason, setPickedReason] = useState(candidate.feedback_reason || '')
  const [otherText, setOtherText] = useState('')
  const [showDislikeForm, setShowDislikeForm] = useState(false)

  const interviewPassed = candidate.interview_slot
    ? new Date(candidate.interview_slot) < new Date()
    : candidate.interview_done

  const submitMut = useMutation({
    mutationFn: ({ status, reason }: { status: 'liked' | 'disliked'; reason?: string }) =>
      submitClientFeedbackFromPortal(token, candidate.mapping_id, status, reason),
    onSuccess: () => { setShowDislikeForm(false); qc.invalidateQueries({ queryKey: ['client-portal', token] }) },
  })

  const slotDisplay = candidate.interview_slot_label
    ?? (candidate.interview_slot
      ? new Date(candidate.interview_slot).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
      : null)

  return (
    <div className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
      <div className="flex items-start gap-4">
        <div className="w-12 h-12 rounded-full bg-blue-100 flex items-center justify-center shrink-0 overflow-hidden">
          {candidate.photo_url
            ? <img src={candidate.photo_url} alt={candidate.name} className="w-full h-full object-cover" />
            : <span className="text-base font-bold text-blue-700">{candidate.initials}</span>
          }
        </div>

        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-3 flex-wrap">
            <h2 className="text-base font-bold text-gray-900">{candidate.name}</h2>
            {candidate.age_years && <span className="text-xs text-gray-400">{candidate.age_years} yrs</span>}
            {candidate.current_location && <span className="text-xs text-gray-500">· {candidate.current_location}</span>}
          </div>
          {slotDisplay && (
            <div className="mt-1.5">
              <span className="text-xs bg-teal-50 text-teal-700 px-2 py-0.5 rounded-full font-medium">
                Interview: {slotDisplay}
              </span>
            </div>
          )}
          <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1.5 text-xs text-gray-600">
            <div><span className="text-gray-400">Current Salary: </span><span className="font-medium">₹{candidate.current_salary?.toLocaleString('en-IN') ?? '—'}</span></div>
            <div><span className="text-gray-400">Technical Score: </span><span className="font-medium">{candidate.ai_technical_score ?? '—'}/100</span></div>
            <div><span className="text-gray-400">Software: </span><span className="font-medium">{candidate.accounting_software || '—'}</span></div>
            <div><span className="text-gray-400">Notice: </span><span className="font-medium">{formatNoticePeriod(candidate.notice_period)}</span></div>
            {candidate.job_stability && candidate.job_stability !== 'Unknown' && (
              <div><StabilityBadge label={candidate.job_stability} /></div>
            )}
            {candidate.cv_url && (
              <div><a href={candidate.cv_url} target="_blank" rel="noreferrer" className="text-blue-600 hover:underline font-medium">View CV ↗</a></div>
            )}
          </div>
        </div>

        <div className="flex flex-col items-center gap-1 shrink-0">
          <MmsRing score={candidate.mms_score_pct} />
          <span className="text-[10px] text-gray-400">Match</span>
        </div>
      </div>

      {candidate.evaluation_report_url && (
        <div className="mt-4 pt-3 border-t border-gray-100 flex gap-3">
          <a href={candidate.evaluation_report_url} target="_blank" rel="noreferrer"
            className="text-xs text-blue-600 hover:underline font-medium">Evaluation Report ↗</a>
        </div>
      )}

      {/* Feedback section — only after interview time passes */}
      {interviewPassed && (
        <div className="mt-4 pt-3 border-t border-gray-100">
          {candidate.feedback ? (
            candidate.feedback === 'liked' ? (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700 bg-green-50 px-3 py-1.5 rounded-full">
                ✓ You liked this candidate
              </span>
            ) : (
              <div className="flex items-center gap-2 flex-wrap">
                <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-600 bg-red-50 px-3 py-1.5 rounded-full">
                  ✗ Not a fit
                </span>
                {candidate.feedback_reason && (
                  <span className="text-xs text-gray-400">Reason: {candidate.feedback_reason}</span>
                )}
              </div>
            )
          ) : !showDislikeForm ? (
            <div>
              <p className="text-xs font-semibold text-gray-500 mb-2">Your feedback after the interview:</p>
              <div className="flex gap-2 flex-wrap">
                <button onClick={() => submitMut.mutate({ status: 'liked' })} disabled={submitMut.isPending}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-green-50 border border-green-200 text-green-700 text-xs font-semibold hover:bg-green-100 transition-colors disabled:opacity-50">
                  👍 Liked
                </button>
                <button onClick={() => setShowDislikeForm(true)}
                  className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-red-50 border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-100 transition-colors">
                  👎 Not a fit
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-2">
              <p className="text-xs text-gray-500">Select a reason:</p>
              <div className="flex flex-wrap gap-2">
                {DISLIKE_REASONS.map(r => (
                  <button key={r} onClick={() => setPickedReason(r)}
                    className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                      pickedReason === r ? 'bg-red-600 text-white border-red-600' : 'bg-white text-gray-600 border-gray-300 hover:border-red-300'
                    }`}>
                    {r}
                  </button>
                ))}
              </div>
              {pickedReason === 'Other' && (
                <textarea value={otherText} onChange={e => setOtherText(e.target.value)}
                  placeholder="Please describe…" rows={2}
                  className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs resize-none focus:outline-none focus:ring-2 focus:ring-red-300" />
              )}
              <div className="flex gap-2 mt-1">
                <button onClick={() => { setShowDislikeForm(false); setPickedReason('') }}
                  className="px-3 py-1.5 rounded-xl border border-gray-200 text-xs text-gray-600 hover:bg-gray-50">
                  Back
                </button>
                <button
                  onClick={() => submitMut.mutate({ status: 'disliked', reason: pickedReason === 'Other' ? (otherText.trim() || 'Other') : pickedReason })}
                  disabled={!pickedReason || submitMut.isPending}
                  className="px-4 py-1.5 rounded-xl bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors">
                  {submitMut.isPending ? 'Submitting…' : 'Submit'}
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = 'overview' | 'review' | 'feedback'

// State-aware "nothing to review yet" copy — reads matching_state (set only by the
// existing matching flow) so this never claims outreach has started before it has.
function sourcingCopy(matchingState: string | null | undefined): { title: string; body: string } {
  if (matchingState == null) {
    return {
      title: "We're setting up your matchmaking",
      body: "Our team is reviewing your requirement and will begin matching accountants shortly. Shortlisted profiles will appear here once outreach begins.",
    }
  }
  if (matchingState === 'path2_b') {
    return {
      title: "We're sourcing candidates for your role",
      body: "We're expanding our search to find the right fit. Shortlisted profiles will appear here as soon as we find strong matches.",
    }
  }
  return {
    title: "We're on it!",
    body: "Our team is actively reaching out to relevant candidates. Shortlisted profiles will appear here for your review soon.",
  }
}

export default function ClientPortalPage() {
  const { token } = useParams<{ token: string }>()
  const qc = useQueryClient()
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [tabInitialised, setTabInitialised] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['client-portal', token],
    queryFn: () => getClientPortal(token!),
    retry: false,
    select: d => {
      if (!tabInitialised) {
        // If unlocked (slots exist), always land on feedback
        const tab = d.has_recruiter_slots ? 'feedback' : d.active_tab as Tab
        setActiveTab(tab)
        setTabInitialised(true)
      }
      return d
    },
  })

  if (isLoading) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <p className="text-gray-400 text-sm">Loading…</p>
    </div>
  )

  if (isError || !data) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <p className="text-2xl font-bold text-gray-300 mb-2">404</p>
        <p className="text-gray-500 text-sm">This portal link was not found or has expired.</p>
      </div>
    </div>
  )

  const hasRecruiterSlots = data.has_recruiter_slots
  const approvalCount = data.approval_candidates.length
  const feedbackCount = data.feedback_candidates.length
  const likedCount = data.feedback_candidates.filter((c: any) => c.feedback === 'liked').length
  const dislikedCount = data.feedback_candidates.filter((c: any) => c.feedback === 'disliked').length
  const pendingFeedbackCount = data.feedback_candidates.filter((c: any) => !c.feedback).length

  const tabs: { key: Tab; label: string }[] = [
    { key: 'overview', label: 'Overview' },
    { key: 'review', label: `Review Candidates${approvalCount ? ` (${approvalCount})` : ''}` },
    { key: 'feedback', label: `Interview Feedback${feedbackCount ? ` (${feedbackCount})` : ''}` },
  ]

  const handleTabClick = (key: Tab) => {
    if (!hasRecruiterSlots && key === 'feedback') return
    setActiveTab(key)
  }

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-8">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="text-center mb-6">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">
            JustAccountants · Client Portal
          </p>
          <h1 className="text-xl font-bold text-gray-900">{data.client.company_name}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{data.client.job_title}</p>
        </div>

        {/* Tabs */}
        <div className="flex bg-white rounded-2xl border border-gray-200 shadow-sm p-1 mb-6 gap-1">
          {tabs.map(tab => {
            const locked = !hasRecruiterSlots && tab.key === 'feedback'
            return (
              <button
                key={tab.key}
                onClick={() => handleTabClick(tab.key)}
                disabled={locked}
                className={`flex-1 py-2 px-2 rounded-xl text-xs font-semibold transition-colors relative ${
                  activeTab === tab.key
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : locked
                    ? 'text-gray-300 cursor-not-allowed'
                    : 'text-gray-500 hover:text-gray-700 hover:bg-gray-50'
                }`}
              >
                {locked && <span className="mr-0.5">🔒</span>}
                {tab.label}
              </button>
            )
          })}
        </div>

        {/* Tab: Overview */}
        {activeTab === 'overview' && (
          <div className="space-y-4">
            <PipelineFunnel ov={data.overview} />
            <ActivityFeed events={data.activity_events ?? []} />
            <ProcessExplainer />
            {(data.overview.interview_scheduled > 0 || data.overview.interview_done > 0) && (
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-5">
                <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">Interviews</p>
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Scheduled', value: data.overview.interview_scheduled, color: 'text-teal-600' },
                    { label: 'Done',      value: data.overview.interview_done,      color: 'text-purple-600' },
                    { label: 'Placed',    value: data.overview.placed,              color: 'text-green-600' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-gray-50 rounded-xl px-3 py-3 text-center">
                      <p className={`text-2xl font-bold ${color}`}>{value}</p>
                      <p className="text-[11px] text-gray-500 mt-0.5">{label}</p>
                    </div>
                  ))}
                </div>
              </div>
            )}
            {!hasRecruiterSlots && (
              <div className="bg-amber-50 border border-amber-200 rounded-2xl p-4 text-center">
                <p className="text-xs text-amber-700 font-medium">
                  Interview scheduling will be set up by our team shortly. You'll be able to review candidates once slots are confirmed.
                </p>
              </div>
            )}
          </div>
        )}

        {/* Tab: Review Candidates */}
        {activeTab === 'review' && (
          <>
            {approvalCount === 0 && data.invited_candidates.length === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-10 text-center">
                <div className="w-12 h-12 bg-indigo-50 rounded-full flex items-center justify-center mx-auto mb-4">
                  <span className="text-2xl">🔍</span>
                </div>
                {(() => {
                  const copy = sourcingCopy(data.client.matching_state)
                  return (
                    <>
                      <p className="text-sm font-semibold text-gray-700 mb-1">{copy.title}</p>
                      <p className="text-xs text-gray-400 leading-relaxed">{copy.body}</p>
                    </>
                  )
                })()}
              </div>
            ) : (
              <div className="space-y-6">
                {approvalCount > 0 && (
                  <div className="space-y-4">
                    {data.approval_candidates.map(c => (
                      <ApprovalCard key={c.mapping_id} token={token!} candidate={c} />
                    ))}
                  </div>
                )}
                {data.invited_candidates.length > 0 && (
                  <div>
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
                      Invited for Interview ({data.invited_candidates.length})
                    </p>
                    <div className="bg-white rounded-2xl border border-gray-200 divide-y divide-gray-100">
                      {data.invited_candidates.map(c => (
                        <div key={c.mapping_id} className="flex items-center gap-3 px-4 py-3">
                          <div className="w-8 h-8 rounded-full bg-teal-100 flex items-center justify-center shrink-0 overflow-hidden">
                            {c.photo_url
                              ? <img src={c.photo_url} alt={c.name} className="w-full h-full object-cover" />
                              : <span className="text-xs font-bold text-teal-700">{c.initials}</span>
                            }
                          </div>
                          <div className="min-w-0 flex-1">
                            <p className="text-sm text-gray-800 font-medium">{c.name}</p>
                            <p className="text-xs text-gray-400 mt-0.5">
                              {c.stage === 'slot_sent_candidate'
                                ? 'Slot link sent · awaiting response'
                                : 'Invited · sending slot link…'}
                            </p>
                          </div>
                          <span className={`shrink-0 text-xs font-semibold ${c.stage === 'slot_sent_candidate' ? 'text-amber-500' : 'text-teal-600'}`}>
                            {c.stage === 'slot_sent_candidate' ? '⏳' : '✓'}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </>
        )}

        {/* Tab: Interview Feedback */}
        {activeTab === 'feedback' && (
          <>
            {feedbackCount > 0 && (
              <div className="flex justify-center gap-6 mb-6 text-center">
                <div><p className="text-lg font-bold text-green-600">{likedCount}</p><p className="text-xs text-gray-500">Liked</p></div>
                <div><p className="text-lg font-bold text-red-500">{dislikedCount}</p><p className="text-xs text-gray-500">Disliked</p></div>
                <div><p className="text-lg font-bold text-gray-400">{pendingFeedbackCount}</p><p className="text-xs text-gray-500">Pending</p></div>
              </div>
            )}
            {feedbackCount === 0 ? (
              <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-10 text-center">
                <div className="w-12 h-12 bg-teal-50 rounded-full flex items-center justify-center mx-auto mb-4">
                  <span className="text-2xl">📅</span>
                </div>
                <p className="text-sm font-semibold text-gray-700 mb-1">No interviews scheduled yet</p>
                <p className="text-xs text-gray-400 leading-relaxed">
                  Once you invite candidates and they pick a slot,<br />their interview details will appear here.
                </p>
              </div>
            ) : (
              <div className="space-y-4">
                {data.feedback_candidates.map(c => (
                  <FeedbackCard key={c.mapping_id} token={token!} candidate={c} />
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}
