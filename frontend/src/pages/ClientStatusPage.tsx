import { useState, useEffect } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/axios'
import HiringFunnel from '../components/HiringFunnel'

interface StatusData {
  company_name: string
  poc_name: string
  job_title: string
  funnel: Array<{ key: string; label: string; status: 'done' | 'active' | 'pending' }>
}

const STAGE_MESSAGES: Record<string, { heading: string; sub: string }> = {
  form_submitted:  { heading: 'Thank you for sharing your requirement with us.', sub: 'We have sent an agreement to you over WhatsApp. Kindly acknowledge.' },
  agreement:       { heading: 'Agreement pending',        sub: 'Please sign the agreement sent to your WhatsApp to proceed.' },
  profile_sharing: { heading: 'Sit back and relax!',      sub: "We're finding the best matching profiles for you. Your dedicated RM will be in touch — feel free to reach out on WhatsApp for any queries." },
  interviews:      { heading: 'Interviews in progress',   sub: 'Candidates have been shortlisted. Interviews are being scheduled.' },
  hired:           { heading: 'Placement confirmed!',     sub: 'Congratulations on your successful hire.' },
}

const CLIENT_WA = 'https://wa.me/917383925646'

function activeStage(funnel: StatusData['funnel']) {
  const active = funnel.find(s => s.status === 'active')
  if (active) return active.key
  const lastDone = [...funnel].reverse().find(s => s.status === 'done')
  return lastDone?.key ?? 'form_submitted'
}

function WARedirect({ waUrl }: { waUrl: string }) {
  const [countdown, setCountdown] = useState(10)

  useEffect(() => {
    if (countdown <= 0) { window.location.href = waUrl; return }
    const t = setTimeout(() => setCountdown(c => c - 1), 1000)
    return () => clearTimeout(t)
  }, [countdown, waUrl])

  return (
    <div className="mt-4 space-y-2">
      <a
        href={waUrl}
        className="flex items-center justify-center gap-2 w-full bg-green-600 hover:bg-green-700 text-white text-sm font-semibold py-2.5 rounded-xl transition-colors"
      >
        <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
          <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347z"/>
          <path d="M12 0C5.373 0 0 5.373 0 12c0 2.126.555 4.121 1.527 5.849L.057 23.941l6.235-1.635A11.945 11.945 0 0012 24c6.627 0 12-5.373 12-12S18.627 0 12 0zm0 21.818a9.818 9.818 0 01-5.002-1.368l-.359-.214-3.7.971.988-3.609-.234-.37A9.818 9.818 0 1112 21.818z"/>
        </svg>
        Back to WhatsApp
      </a>
      <p className="text-xs text-gray-400 text-center">Redirecting in {countdown}s…</p>
    </div>
  )
}

export default function ClientStatusPage() {
  const { clientId } = useParams<{ clientId: string }>()

  const { data, isLoading, error } = useQuery({
    queryKey: ['client-status', clientId],
    queryFn: () => api.get<{ data: StatusData }>(`/clients/${clientId}/status`).then(r => r.data.data),
    enabled: !!clientId,
    refetchInterval: 60_000,
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (error || !data) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
        <p className="text-gray-400 text-sm">This link is invalid or has expired.</p>
      </div>
    )
  }

  const current = activeStage(data.funnel)
  const msg = STAGE_MESSAGES[current] ?? STAGE_MESSAGES.form_submitted

  return (
    <div className="min-h-screen bg-gray-50 py-10 px-4">
      <div className="max-w-lg mx-auto">

        {/* Header */}
        <div className="text-center mb-8">
          <img
            src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg"
            alt="Just Accountants"
            className="w-14 h-14 rounded-xl mx-auto mb-3 object-cover"
          />
          <p className="text-xs text-gray-400 uppercase tracking-widest">Just Accountants</p>
        </div>

        {/* Status card */}
        <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6 mb-6">
          <div className="flex items-start gap-4 mb-6">
            <div className="w-10 h-10 bg-blue-600 rounded-xl flex items-center justify-center shrink-0">
              <svg className="w-5 h-5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16m14 0h2m-2 0h-5m-9 0H3m2 0h5M9 7h1m-1 4h1m4-4h1m-1 4h1m-5 10v-5a1 1 0 011-1h2a1 1 0 011 1v5m-4 0h4" />
              </svg>
            </div>
            <div>
              <h1 className="text-lg font-bold text-gray-900">{data.company_name}</h1>
              {data.job_title && (
                <p className="text-sm text-gray-500 mt-0.5">{data.job_title}</p>
              )}
            </div>
          </div>

          {/* Funnel */}
          <HiringFunnel stages={data.funnel} className="mb-6" />

          {/* Current status message */}
          <div className="mt-4 pt-4 border-t border-gray-100 text-center">
            <p className="text-base font-semibold text-gray-900">{msg.heading}</p>
            <p className="text-sm text-gray-500 mt-1">{msg.sub}</p>
            {(current === 'form_submitted' || current === 'agreement') && <WARedirect waUrl={CLIENT_WA} />}
          </div>
        </div>

        <p className="text-center text-xs text-gray-300">Powered by Just Accountants</p>
      </div>
    </div>
  )
}
