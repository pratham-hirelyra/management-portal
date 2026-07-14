import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getClientFeedbackPage,
  submitClientFeedback,
  reopenClientFeedback,
  type ClientFeedbackCandidate,
} from '../api/outreach'

const LABEL_COLORS: Record<string, string> = {
  'Quick Joiner':       'bg-green-100 text-green-700',
  'High Stability':     'bg-blue-100 text-blue-700',
  'Average Stability':  'bg-amber-100 text-amber-700',
  'High Turnover Risk': 'bg-red-100 text-red-600',
}

function Avatar({ name, photoUrl }: { name: string; photoUrl: string | null }) {
  const initials = name.trim().split(/\s+/).map(p => p[0]?.toUpperCase() ?? '').slice(0, 2).join('')
  if (photoUrl) {
    return (
      <img
        src={photoUrl}
        alt={name}
        className="w-14 h-14 rounded-full object-cover border-2 border-white shadow"
      />
    )
  }
  return (
    <div className="w-14 h-14 rounded-full bg-gradient-to-br from-blue-400 to-indigo-600 flex items-center justify-center text-white font-bold text-lg shadow">
      {initials}
    </div>
  )
}

function CandidateCard({
  token,
  candidate,
}: {
  token: string
  candidate: ClientFeedbackCandidate
}) {
  const qc = useQueryClient()
  const [dislikeReason, setDislikeReason] = useState(candidate.feedback_reason ?? '')
  const [showReasonBox, setShowReasonBox] = useState(false)

  const submitMut = useMutation({
    mutationFn: ({ status, reason }: { status: 'liked' | 'disliked'; reason?: string }) =>
      submitClientFeedback(token, candidate.mapping_id, status, reason),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['client-feedback', token] }),
  })

  const reopenMut = useMutation({
    mutationFn: () => reopenClientFeedback(token, candidate.mapping_id),
    onSuccess: () => {
      setDislikeReason('')
      setShowReasonBox(false)
      qc.invalidateQueries({ queryKey: ['client-feedback', token] })
    },
  })

  const handleDislike = () => {
    if (!showReasonBox) {
      setShowReasonBox(true)
      return
    }
    submitMut.mutate({ status: 'disliked', reason: dislikeReason || undefined })
  }

  const hasInterview = !!candidate.interview_slot
  const slotStr = hasInterview
    ? new Date(candidate.interview_slot!).toLocaleString('en-IN', {
        day: 'numeric', month: 'short', year: 'numeric',
        hour: '2-digit', minute: '2-digit',
      })
    : null

  return (
    <div className={`bg-white rounded-2xl border shadow-sm p-5 flex flex-col gap-4 transition-all ${
      candidate.feedback === 'liked'    ? 'border-green-300 ring-2 ring-green-100' :
      candidate.feedback === 'disliked' ? 'border-red-300 ring-2 ring-red-100' :
      'border-gray-200'
    }`}>
      {/* Top row */}
      <div className="flex items-start gap-4">
        <Avatar name={candidate.name} photoUrl={candidate.photo_url} />
        <div className="flex-1 min-w-0">
          <p className="font-semibold text-gray-900 text-base">{candidate.name}</p>
          {candidate.age_years && (
            <p className="text-xs text-gray-500">{candidate.age_years} yrs · {candidate.current_location ?? '—'}</p>
          )}
          {slotStr && (
            <p className="text-xs text-teal-600 mt-0.5 font-medium">Interview: {slotStr}</p>
          )}
          {candidate.labels.length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
              {candidate.labels.map(label => (
                <span key={label} className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${LABEL_COLORS[label] ?? 'bg-gray-100 text-gray-600'}`}>
                  {label}
                </span>
              ))}
            </div>
          )}
        </div>
        {candidate.feedback && (
          <span className={`text-xs px-2.5 py-1 rounded-full font-semibold ${
            candidate.feedback === 'liked' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'
          }`}>
            {candidate.feedback === 'liked' ? '👍 Liked' : '👎 Disliked'}
          </span>
        )}
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="bg-gray-50 rounded-xl py-2">
          <p className="text-[10px] text-gray-400 mb-0.5">Salary</p>
          <p className="text-xs font-semibold text-gray-800">
            {candidate.current_salary != null ? `₹${Number(candidate.current_salary).toLocaleString()}` : '—'}
          </p>
        </div>
        <div className="bg-gray-50 rounded-xl py-2">
          <p className="text-[10px] text-gray-400 mb-0.5">Tech Score</p>
          <p className="text-xs font-semibold text-gray-800">{candidate.technical_score.toFixed(0)}</p>
        </div>
        <div className="bg-gray-50 rounded-xl py-2">
          <p className="text-[10px] text-gray-400 mb-0.5">Experience</p>
          <p className="text-xs font-semibold text-gray-800">{candidate.experience ?? '—'}</p>
        </div>
      </div>

      {/* Links */}
      <div className="flex gap-3">
        {candidate.cv_url && (
          <a href={candidate.cv_url} target="_blank" rel="noreferrer"
            className="text-xs text-blue-500 hover:underline">CV ↗</a>
        )}
        {candidate.report_url && (
          <a href={candidate.report_url} target="_blank" rel="noreferrer"
            className="text-xs text-blue-500 hover:underline">Report ↗</a>
        )}
      </div>

      {/* Feedback area */}
      {candidate.feedback ? (
        <div className="space-y-2">
          {candidate.feedback === 'disliked' && candidate.feedback_reason && (
            <p className="text-xs text-gray-500 italic">"{candidate.feedback_reason}"</p>
          )}
          <button
            onClick={() => reopenMut.mutate()}
            disabled={reopenMut.isPending}
            className="w-full py-2 text-xs rounded-xl border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-50"
          >
            {reopenMut.isPending ? 'Reopening…' : '↩ Reopen Feedback'}
          </button>
        </div>
      ) : (
        <div className="space-y-2">
          {showReasonBox && (
            <textarea
              rows={2}
              placeholder="Reason for disliking (optional)…"
              value={dislikeReason}
              onChange={e => setDislikeReason(e.target.value)}
              className="w-full border border-gray-200 rounded-xl px-3 py-2 text-xs focus:outline-none focus:ring-2 focus:ring-red-300 resize-none"
            />
          )}
          <div className="flex gap-2">
            <button
              onClick={() => submitMut.mutate({ status: 'liked' })}
              disabled={submitMut.isPending}
              className="flex-1 py-2.5 rounded-xl border-2 border-green-200 bg-green-50 hover:bg-green-100
                text-green-700 font-semibold text-xs transition-colors disabled:opacity-50"
            >
              👍 Proceed
            </button>
            <button
              onClick={handleDislike}
              disabled={submitMut.isPending}
              className="flex-1 py-2.5 rounded-xl border-2 border-red-200 bg-red-50 hover:bg-red-100
                text-red-600 font-semibold text-xs transition-colors disabled:opacity-50"
            >
              {showReasonBox ? (submitMut.isPending ? '…' : 'Confirm') : '👎 Pass'}
            </button>
          </div>
          {showReasonBox && (
            <button
              onClick={() => setShowReasonBox(false)}
              className="w-full text-xs text-gray-400 hover:text-gray-600"
            >
              Cancel
            </button>
          )}
        </div>
      )}
    </div>
  )
}

export default function ClientFeedbackPage() {
  const { token } = useParams<{ token: string }>()

  const { data, isLoading, isError } = useQuery({
    queryKey: ['client-feedback', token],
    queryFn: () => getClientFeedbackPage(token!),
    retry: false,
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
        <p className="text-gray-500 text-sm">This feedback link was not found or has expired.</p>
      </div>
    </div>
  )

  const likedCount = data.candidates.filter(c => c.feedback === 'liked').length
  const dislikedCount = data.candidates.filter(c => c.feedback === 'disliked').length
  const pendingCount = data.candidates.filter(c => !c.feedback).length

  return (
    <div className="min-h-screen bg-gray-50 px-4 py-8">
      <div className="max-w-2xl mx-auto">
        {/* Header */}
        <div className="text-center mb-8">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-2">
            JustAccountants · Interview Feedback
          </p>
          <h1 className="text-xl font-bold text-gray-900">{data.client.company_name}</h1>
          <p className="text-sm text-gray-500 mt-0.5">{data.client.job_title}</p>
        </div>

        {/* Summary bar */}
        {data.candidates.length > 0 && (
          <div className="flex justify-center gap-6 mb-6 text-center">
            <div>
              <p className="text-lg font-bold text-green-600">{likedCount}</p>
              <p className="text-xs text-gray-500">Liked</p>
            </div>
            <div>
              <p className="text-lg font-bold text-red-500">{dislikedCount}</p>
              <p className="text-xs text-gray-500">Disliked</p>
            </div>
            <div>
              <p className="text-lg font-bold text-gray-400">{pendingCount}</p>
              <p className="text-xs text-gray-500">Pending</p>
            </div>
          </div>
        )}

        {data.candidates.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            <p className="text-sm">No interview candidates yet.</p>
          </div>
        ) : (
          <div className="space-y-4">
            {data.candidates.map(c => (
              <CandidateCard key={c.mapping_id} token={token!} candidate={c} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
