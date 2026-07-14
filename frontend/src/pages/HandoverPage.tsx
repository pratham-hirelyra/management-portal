import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

interface CandidateCard {
  candidate_id: string
  name: string
  initials: string
  age_years: number | null
  current_area: string | null
  current_salary: number | null
  salary_vs_budget: number
  mms_score_pct: number
  ai_technical_score: number | null
  job_stability: string
  accounting_software: string | null
  gst_experience: string | null
  tds_experience: string | null
  notice_period: string | null
  cv_url: string | null
  evaluation_report_url: string | null
  interview_slot: string | null
  interview_slot_label: string | null
  mapping_id: string
  feedback_client: string | null
  feedback_client_reason: string | null
  interview_done: boolean
  photo_url: string | null
}

interface HandoverData {
  client: { company_name: string; job_title: string }
  feedback_token: string | null
  candidates: CandidateCard[]
}

const DISLIKE_REASONS = [
  'Poor technical knowledge',
  'Location',
  'Salary',
  'Other',
]

function StabilityBadge({ label }: { label: string }) {
  const cls =
    label === 'High Stability' ? 'text-green-700' :
    label === 'Average Stability' ? 'text-amber-700' :
    label === 'High Turnover Risk' ? 'text-red-600' :
    'text-gray-500'
  return (
    <span className="text-xs text-gray-400">Job Stability: <span className={`font-medium ${cls}`}>{label}</span></span>
  )
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

function FeedbackSection({
  candidate, feedbackToken,
}: {
  candidate: CandidateCard
  feedbackToken: string | null
}) {
  const qc = useQueryClient()
  const { token } = useParams<{ token: string }>()

  const [pickedReason, setPickedReason] = useState<string>(candidate.feedback_client_reason || '')
  const [otherText, setOtherText] = useState('')
  const [showDislikeForm, setShowDislikeForm] = useState(false)

  const interviewPassed = candidate.interview_slot
    ? new Date(candidate.interview_slot) < new Date()
    : candidate.interview_done

  const submitFeedback = useMutation({
    mutationFn: ({ status, reason }: { status: string; reason?: string }) =>
      api.post(`/client-feedback/${feedbackToken}/candidate/${candidate.mapping_id}`, { status, reason }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['handover', token] })
      setShowDislikeForm(false)
    },
  })

  if (!interviewPassed || !feedbackToken) return null

  // Already submitted
  if (candidate.feedback_client) {
    return (
      <div className="mt-4 pt-3 border-t border-gray-100">
        {candidate.feedback_client === 'liked' ? (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-green-700 bg-green-50 px-3 py-1.5 rounded-full">
            ✓ You liked this candidate
          </span>
        ) : (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-red-600 bg-red-50 px-3 py-1.5 rounded-full">
              ✗ Not a fit
            </span>
            {candidate.feedback_client_reason && (
              <span className="text-xs text-gray-400">Reason: {candidate.feedback_client_reason}</span>
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="mt-4 pt-3 border-t border-gray-100">
      <p className="text-xs font-semibold text-gray-500 mb-2">Your feedback after the interview:</p>

      {!showDislikeForm ? (
        <div className="flex gap-2 flex-wrap">
          <button
            onClick={() => submitFeedback.mutate({ status: 'liked' })}
            disabled={submitFeedback.isPending}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-green-50 border border-green-200 text-green-700 text-xs font-semibold hover:bg-green-100 transition-colors disabled:opacity-50"
          >
            👍 Liked
          </button>
          <button
            onClick={() => setShowDislikeForm(true)}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-red-50 border border-red-200 text-red-600 text-xs font-semibold hover:bg-red-100 transition-colors"
          >
            👎 Not a fit
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          <p className="text-xs text-gray-500">Select a reason:</p>
          <div className="flex flex-wrap gap-2">
            {DISLIKE_REASONS.map(r => (
              <button
                key={r}
                onClick={() => setPickedReason(r)}
                className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-colors ${
                  pickedReason === r
                    ? 'bg-red-600 text-white border-red-600'
                    : 'bg-white text-gray-600 border-gray-300 hover:border-red-300'
                }`}
              >
                {r}
              </button>
            ))}
          </div>
          {pickedReason === 'Other' && (
            <textarea
              value={otherText}
              onChange={e => setOtherText(e.target.value)}
              placeholder="Please describe…"
              rows={2}
              className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs resize-none focus:outline-none focus:ring-2 focus:ring-red-300"
            />
          )}
          <div className="flex gap-2 mt-1">
            <button
              onClick={() => { setShowDislikeForm(false); setPickedReason('') }}
              className="px-3 py-1.5 rounded-xl border border-gray-200 text-xs text-gray-600 hover:bg-gray-50"
            >
              Back
            </button>
            <button
              onClick={() => {
                const reason = pickedReason === 'Other' ? (otherText.trim() || 'Other') : pickedReason
                submitFeedback.mutate({ status: 'disliked', reason })
              }}
              disabled={!pickedReason || submitFeedback.isPending}
              className="px-4 py-1.5 rounded-xl bg-red-600 text-white text-xs font-semibold hover:bg-red-700 disabled:opacity-50 transition-colors"
            >
              {submitFeedback.isPending ? 'Submitting…' : 'Submit'}
            </button>
          </div>
          {submitFeedback.isError && (
            <p className="text-xs text-red-500">Something went wrong. Please try again.</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function HandoverPage() {
  const { token } = useParams<{ token: string }>()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['handover', token],
    queryFn: () => api.get(`/handover/${token}`).then(r => r.data.data as HandoverData),
    retry: false,
    refetchInterval: 60_000,
  })

  if (isLoading) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <p className="text-gray-400 text-sm">Loading candidates…</p>
    </div>
  )

  if (isError) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <p className="text-2xl font-bold text-gray-300 mb-2">403</p>
        <p className="text-gray-500 text-sm">This link is invalid or has expired.</p>
      </div>
    </div>
  )

  const { client, candidates, feedback_token } = data!

  return (
    <div className="min-h-screen bg-gray-50 py-8 px-4">
      <div className="max-w-4xl mx-auto">
        <div className="mb-8 text-center">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-1">JustAccountants</p>
          <h1 className="text-2xl font-bold text-gray-900">Interview-Ready Candidates</h1>
          <p className="text-sm text-gray-500 mt-1">
            {client.job_title} · {client.company_name}
          </p>
        </div>

        {candidates.length === 0 ? (
          <div className="text-center py-16 text-gray-400 text-sm">No candidates confirmed yet.</div>
        ) : (
          <div className="space-y-4">
            {candidates.map(c => (
              <div key={c.candidate_id} className="bg-white rounded-2xl border border-gray-200 p-5 shadow-sm">
                <div className="flex items-start gap-4">
                  {/* Avatar */}
                  <div className="w-12 h-12 rounded-full bg-blue-100 flex items-center justify-center shrink-0 overflow-hidden">
                    {c.photo_url
                      ? <img src={c.photo_url} alt={c.name} className="w-full h-full object-cover" />
                      : <span className="text-base font-bold text-blue-700">{c.initials}</span>
                    }
                  </div>

                  {/* Main info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-3 flex-wrap">
                      <h2 className="text-base font-bold text-gray-900">{c.name}</h2>
                      {c.age_years && <span className="text-xs text-gray-400">{c.age_years} yrs</span>}
                      {c.current_area && <span className="text-xs text-gray-500">· {c.current_area}</span>}
                    </div>

                    <div className="flex items-center gap-3 mt-1.5 flex-wrap">
                      {(c.interview_slot_label || c.interview_slot) && (
                        <span className="text-xs bg-teal-50 text-teal-700 px-2 py-0.5 rounded-full font-medium">
                          Interview: {c.interview_slot_label ?? new Date(c.interview_slot!).toLocaleString('en-IN', {
                            day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
                          })}
                        </span>
                      )}
                    </div>

                    {/* Details grid */}
                    <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1.5 text-xs text-gray-600">
                      <div>
                        <span className="text-gray-400">Current Salary: </span>
                        <span className="font-medium">₹{c.current_salary?.toLocaleString('en-IN') ?? '—'}</span>
                      </div>
                      <div>
                        <span className="text-gray-400">Technical Score: </span>
                        <span className="font-medium">{c.ai_technical_score ?? '—'}/100</span>
                      </div>
                      <div>
                        <span className="text-gray-400">Software: </span>
                        <span className="font-medium">{c.accounting_software || '—'}</span>
                      </div>
                      <div>
                        <span className="text-gray-400">Notice: </span>
                        <span className="font-medium">{c.notice_period || '—'}</span>
                      </div>
                      {c.job_stability && c.job_stability !== 'Unknown' && (
                        <div><StabilityBadge label={c.job_stability} /></div>
                      )}
                      {c.cv_url && (
                        <div>
                          <a href={c.cv_url} target="_blank" rel="noreferrer"
                            className="text-blue-600 hover:underline font-medium">
                            View CV ↗
                          </a>
                        </div>
                      )}
                    </div>
                  </div>

                  {/* MMS ring */}
                  <div className="flex flex-col items-center gap-1 shrink-0">
                    <MmsRing score={c.mms_score_pct} />
                    <span className="text-[10px] text-gray-400">Match</span>
                  </div>
                </div>

                {/* Links */}
                {c.evaluation_report_url && (
                  <div className="mt-4 pt-3 border-t border-gray-100 flex gap-3">
                    <a href={c.evaluation_report_url} target="_blank" rel="noreferrer"
                      className="text-xs text-blue-600 hover:underline font-medium">
                      Evaluation Report
                    </a>
                  </div>
                )}

                {/* Feedback */}
                <FeedbackSection candidate={c} feedbackToken={feedback_token} />
              </div>
            ))}
          </div>
        )}

        <p className="text-center text-xs text-gray-300 mt-8 pb-4">Powered by JustAccountants</p>
      </div>
    </div>
  )
}
