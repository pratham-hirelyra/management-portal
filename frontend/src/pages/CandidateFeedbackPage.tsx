import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getCandidateFeedbackPage,
  submitCandidateFeedback,
} from '../api/outreach'

export default function CandidateFeedbackPage() {
  const { token } = useParams<{ token: string }>()
  const qc = useQueryClient()
  const [confirming, setConfirming] = useState<'join' | 'no_join' | null>(null)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['candidate-feedback', token],
    queryFn: () => getCandidateFeedbackPage(token!),
    retry: false,
  })

  const submitMut = useMutation({
    mutationFn: (decision: 'join' | 'no_join') =>
      submitCandidateFeedback(token!, decision),
    onSuccess: () => {
      setConfirming(null)
      qc.invalidateQueries({ queryKey: ['candidate-feedback', token] })
    },
  })

  if (isLoading) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <p className="text-gray-400 text-sm">Loading…</p>
    </div>
  )

  if (isError || !data) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center px-4">
        <p className="text-3xl font-bold text-gray-200 mb-3">404</p>
        <p className="text-gray-500 text-sm">This feedback link was not found or has expired.</p>
      </div>
    </div>
  )

  // Already submitted
  if (data.feedback) {
    const joined = data.feedback === 'join'
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
        <div className="max-w-sm w-full text-center">
          <div className={`w-16 h-16 rounded-full flex items-center justify-center mx-auto mb-5 ${
            joined ? 'bg-green-100' : 'bg-gray-100'
          }`}>
            <span className="text-3xl">{joined ? '🎉' : '👋'}</span>
          </div>
          <h2 className="text-xl font-bold text-gray-900 mb-2">
            {joined ? 'Excellent! We will be in touch.' : 'Thank you for letting us know.'}
          </h2>
          <p className="text-sm text-gray-500">
            {joined
              ? `We've noted your interest in joining ${data.company_name}. Our team will reach out with the next steps shortly.`
              : `We appreciate your honesty. We'll keep you in mind for other opportunities that may be a better fit.`}
          </p>
          <p className="text-xs text-gray-300 mt-8">Powered by HireLyra</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center px-4 py-10">
      <div className="max-w-sm w-full">

        {/* Header */}
        <div className="text-center mb-8">
          <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-3">
            HireLyra
          </p>
          <h1 className="text-2xl font-bold text-gray-900 leading-snug">
            Hi {data.candidate_name?.split(' ')[0] || 'there'} 👋
          </h1>
          <p className="text-sm text-gray-500 mt-2">
            We hope your interview with{' '}
            <span className="font-semibold text-gray-700">{data.company_name}</span>{' '}
            went well!
          </p>
        </div>

        {/* Job card */}
        <div className="bg-white rounded-2xl border border-gray-100 shadow-sm p-5 mb-6">
          <p className="text-xs text-gray-400 mb-1">Position you interviewed for</p>
          <p className="font-semibold text-gray-900 text-base">{data.job_title}</p>
          {data.job_location && (
            <p className="text-xs text-gray-500 mt-0.5">{data.job_location}</p>
          )}
          <p className="text-sm text-gray-600 mt-1">{data.company_name}</p>
        </div>

        {/* Question */}
        <p className="text-sm font-medium text-gray-700 text-center mb-4">
          Are you interested in joining this company?
        </p>

        {/* Confirm overlay when tapping No */}
        {confirming === 'no_join' ? (
          <div className="bg-white rounded-2xl border border-red-100 shadow-sm p-5 space-y-3">
            <p className="text-sm font-medium text-gray-800 text-center">
              Are you sure you don't want to proceed?
            </p>
            <p className="text-xs text-gray-400 text-center">
              We'll let the team know and look for other opportunities for you.
            </p>
            <button
              onClick={() => submitMut.mutate('no_join')}
              disabled={submitMut.isPending}
              className="w-full py-3 rounded-xl bg-red-50 border-2 border-red-200 text-red-600
                font-semibold text-sm hover:bg-red-100 transition-colors disabled:opacity-50"
            >
              {submitMut.isPending ? 'Submitting…' : "Yes, I'm not interested"}
            </button>
            <button
              onClick={() => setConfirming(null)}
              className="w-full py-2 text-xs text-gray-400 hover:text-gray-600"
            >
              Cancel
            </button>
          </div>
        ) : (
          <div className="space-y-3">
            <button
              onClick={() => submitMut.mutate('join')}
              disabled={submitMut.isPending}
              className="w-full py-4 rounded-2xl bg-green-500 hover:bg-green-600 active:scale-95
                text-white font-bold text-base shadow-sm transition-all disabled:opacity-50"
            >
              {submitMut.isPending ? 'Submitting…' : '✅  Yes, I want to join!'}
            </button>
            <button
              onClick={() => setConfirming('no_join')}
              disabled={submitMut.isPending}
              className="w-full py-3.5 rounded-2xl border-2 border-gray-200 bg-white hover:bg-gray-50
                text-gray-500 font-semibold text-sm transition-colors disabled:opacity-50"
            >
              No, thank you
            </button>
          </div>
        )}

        <p className="text-xs text-gray-300 text-center mt-8">Powered by HireLyra</p>
      </div>
    </div>
  )
}
