import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { api } from '../api/axios'

interface FeedbackInfo {
  mapping_id: string
  role: 'client' | 'candidate'
  candidate_name: string
  company_name: string
  job_title: string
  already_submitted: boolean
}

export default function FeedbackPage() {
  const { token } = useParams<{ token: string }>()
  const [submitted, setSubmitted] = useState(false)

  const { data, isLoading, isError } = useQuery({
    queryKey: ['feedback', token],
    queryFn: () => api.get(`/feedback/${token}`).then(r => r.data.data as FeedbackInfo),
    retry: false,
  })

  const submitMutation = useMutation({
    mutationFn: (response: 'liked' | 'disliked') =>
      api.post(`/feedback/${token}/submit`, null, { params: { response } }).then(r => r.data),
    onSuccess: () => setSubmitted(true),
  })

  if (isLoading) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <p className="text-gray-400 text-sm">Loading…</p>
    </div>
  )

  if (isError) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <p className="text-2xl font-bold text-gray-300 mb-2">404</p>
        <p className="text-gray-500 text-sm">This feedback link was not found.</p>
      </div>
    </div>
  )

  const info = data!

  if (submitted || info.already_submitted) return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center max-w-sm mx-auto px-4">
        <div className="text-4xl mb-4">✓</div>
        <h1 className="text-lg font-bold text-gray-900 mb-2">Thank you!</h1>
        <p className="text-gray-500 text-sm">Your feedback has been recorded.</p>
      </div>
    </div>
  )

  const isClient = info.role === 'client'
  const question = isClient
    ? `How did the interview with ${info.candidate_name} go?`
    : `How was your interview with ${info.company_name}?`

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50 px-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-8 text-center">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-6">
          HireLyra · {isClient ? 'Client Feedback' : 'Candidate Feedback'}
        </p>

        <h1 className="text-base font-bold text-gray-900 mb-1">{question}</h1>
        <p className="text-xs text-gray-400 mb-8">
          {isClient ? `Candidate: ${info.candidate_name}` : `Role: ${info.job_title} at ${info.company_name}`}
        </p>

        <div className="flex gap-4">
          <button
            onClick={() => submitMutation.mutate('liked')}
            disabled={submitMutation.isPending}
            className="flex-1 py-4 rounded-xl border-2 border-green-200 bg-green-50 hover:bg-green-100
              text-green-700 font-semibold text-sm transition-colors disabled:opacity-50"
          >
            <div className="text-2xl mb-1">👍</div>
            {isClient ? "Like — I'd hire them" : "Like — I'm interested"}
          </button>

          <button
            onClick={() => submitMutation.mutate('disliked')}
            disabled={submitMutation.isPending}
            className="flex-1 py-4 rounded-xl border-2 border-red-200 bg-red-50 hover:bg-red-100
              text-red-600 font-semibold text-sm transition-colors disabled:opacity-50"
          >
            <div className="text-2xl mb-1">👎</div>
            {isClient ? "Dislike — not the right fit" : "Dislike — I've changed my mind"}
          </button>
        </div>
      </div>
    </div>
  )
}
