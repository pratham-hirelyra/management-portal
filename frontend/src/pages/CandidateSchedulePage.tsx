import { useState } from 'react'
import type React from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { api } from '../api/axios'
import HiringFunnel from '../components/HiringFunnel'

const INTERVIEW_FUNNEL = [
  { key: 'ai_interview',   label: 'AI Interview',        status: 'done'    as const },
  { key: 'slot_selection', label: 'Time Slot Selection', status: 'done'    as const },
  { key: 'main_interview', label: 'Main Interview',      status: 'active'  as const },
]

interface CandidateScheduleInfo {
  mapping_id: string
  stage: string
  company_name: string
  job_title: string | null
  job_location: string | null
  location_lat: number | null
  location_lng: number | null
  industry: string | null
  job_type: string | null
  num_employees: number | null
  female_employee_pct: number | null
  min_salary: number | null
  max_salary: number | null
  job_timings: string | null
  age_requirements: string | null
  tools_requirements: string | null
  other_details: string | null
  candidate_name: string
  rm_name: string | null
  rm_phone: string | null
  available_slots: string[]
}

const getCandidateSchedule = (mappingId: string) =>
  api.get<{ data: CandidateScheduleInfo }>(`/schedule/candidate/${mappingId}`).then(r => r.data.data)

const confirmSlot = (mappingId: string, slot_label: string) =>
  api.post(`/schedule/candidate/${mappingId}/confirm`, { slot_label }).then(r => r.data)

// Slot label format: "Mon 25 Jun 3pm" — filter out any whose date is before today
function isSlotPast(label: string): boolean {
  const parts = label.trim().split(/\s+/)
  if (parts.length < 3) return false
  const day = parseInt(parts[1])
  const monthMap: Record<string, number> = {
    Jan:0,Feb:1,Mar:2,Apr:3,May:4,Jun:5,Jul:6,Aug:7,Sep:8,Oct:9,Nov:10,Dec:11
  }
  const month = monthMap[parts[2]]
  if (month === undefined || isNaN(day)) return false
  const now = new Date()
  const year = now.getFullYear()
  const slotDate = new Date(year, month, day)
  // If slot appears to be >6 months in the past, it's next year
  if (now.getTime() - slotDate.getTime() > 183 * 24 * 3600 * 1000) slotDate.setFullYear(year + 1)
  const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  return slotDate < todayStart
}

function JdRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs text-gray-400 mb-0.5">{label}</p>
      <p className="text-xs text-gray-700">{children}</p>
    </div>
  )
}

export default function CandidateSchedulePage() {
  const { mappingId } = useParams<{ mappingId: string }>()
  const [selected, setSelected] = useState<string | null>(null)
  const [confirmed, setConfirmed] = useState(false)
  const [jdOpen, setJdOpen] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)

  const derivedKeySkills = (title: string | null) => {
    const t = (title || '').toLowerCase()
    if (t.includes('junior'))
      return 'Data entry (purchase/sales/bank), bank reco, petty cash, other admin work'
    return 'Accounting, GST, TDS, coordination with CA'
  }

  const { data: info, isLoading, error } = useQuery({
    queryKey: ['candidate-schedule', mappingId],
    queryFn: () => getCandidateSchedule(mappingId!),
    enabled: !!mappingId,
  })

  const confirmMutation = useMutation({
    mutationFn: () => confirmSlot(mappingId!, selected!),
    onSuccess: () => setConfirmed(true),
  })

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (error || !info) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-gray-400">This scheduling link is invalid or has expired.</p>
      </div>
    )
  }

  if (['slot_booked', 'interview_scheduled', 'interview_done', 'placed'].includes(info.stage)) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-6">
        <div className="max-w-sm w-full text-center">
          <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg className="w-7 h-7 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Already Confirmed</h2>
          <p className="text-sm text-gray-500 mb-4">
            Your interview slot with <span className="font-medium text-gray-700">{info.company_name}</span> has already been booked.
          </p>
          {info.rm_phone && (
            <div className="bg-gray-50 rounded-xl p-4 text-left">
              <p className="text-xs text-gray-400 mb-1">Need to change your slot? Contact your recruiter</p>
              <a
                href={`https://wa.me/${info.rm_phone.replace(/\D/g, '')}`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-2 text-sm font-medium text-green-700 hover:underline"
              >
                <svg className="w-4 h-4 shrink-0" fill="currentColor" viewBox="0 0 24 24">
                  <path d="M17.472 14.382c-.297-.149-1.758-.867-2.03-.967-.273-.099-.471-.148-.67.15-.197.297-.767.966-.94 1.164-.173.199-.347.223-.644.075-.297-.15-1.255-.463-2.39-1.475-.883-.788-1.48-1.761-1.653-2.059-.173-.297-.018-.458.13-.606.134-.133.298-.347.446-.52.149-.174.198-.298.298-.497.099-.198.05-.371-.025-.52-.075-.149-.669-1.612-.916-2.207-.242-.579-.487-.5-.669-.51-.173-.008-.371-.01-.57-.01-.198 0-.52.074-.792.372-.272.297-1.04 1.016-1.04 2.479 0 1.462 1.065 2.875 1.213 3.074.149.198 2.096 3.2 5.077 4.487.709.306 1.262.489 1.694.625.712.227 1.36.195 1.871.118.571-.085 1.758-.719 2.006-1.413.248-.694.248-1.289.173-1.413-.074-.124-.272-.198-.57-.347m-5.421 7.403h-.004a9.87 9.87 0 01-5.031-1.378l-.361-.214-3.741.982.998-3.648-.235-.374a9.86 9.86 0 01-1.51-5.26c.001-5.45 4.436-9.884 9.888-9.884 2.64 0 5.122 1.03 6.988 2.898a9.825 9.825 0 012.893 6.994c-.003 5.45-4.437 9.884-9.885 9.884m8.413-18.297A11.815 11.815 0 0012.05 0C5.495 0 .16 5.335.157 11.892c0 2.096.547 4.142 1.588 5.945L.057 24l6.305-1.654a11.882 11.882 0 005.683 1.448h.005c6.554 0 11.89-5.335 11.893-11.893a11.821 11.821 0 00-3.48-8.413z"/>
                </svg>
                {info.rm_name || 'Your Recruiter'} · {info.rm_phone}
              </a>
            </div>
          )}
          <p className="mt-8 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    )
  }

  if (confirmed) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-6">
        <div className="max-w-sm w-full text-center">
          <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg className="w-7 h-7 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Interview Confirmed!</h2>
          <p className="text-sm text-gray-500 mb-1">
            Your interview with <span className="font-medium text-gray-700">{info.company_name}</span> is confirmed for:
          </p>
          <p className="text-sm font-semibold text-blue-600 mt-2 mb-6">{selected}</p>

          <HiringFunnel stages={INTERVIEW_FUNNEL} className="mb-6" />

          <p className="mt-2 text-xs text-gray-400">You'll receive a WhatsApp message with further details shortly.</p>
          <p className="mt-6 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    )
  }

  const futureSlots = info.available_slots.filter(s => !isSlotPast(s))

  if (futureSlots.length === 0) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-6">
        <div className="max-w-sm text-center">
          <p className="text-gray-500">
            {info.available_slots.length === 0
              ? 'No interview slots are available yet. Please check back later or contact your recruiter.'
              : 'All available slots have passed. Please contact your recruiter to receive updated slots.'}
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-white">
      <div className="max-w-3xl mx-auto min-h-screen flex flex-col md:flex-row">

        {/* Left info panel */}
        <div className="md:w-80 shrink-0 border-b md:border-b-0 md:border-r border-gray-200 px-8 py-10 overflow-y-auto">
          <img
            src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg"
            alt="Just Accountants"
            className="w-10 h-10 rounded-xl object-cover mb-5"
          />
          <p className="text-xs text-gray-400 uppercase tracking-widest mb-1">Just Accountants</p>
          <h1 className="text-lg font-bold text-gray-900 leading-snug mb-1">{info.company_name}</h1>
          {info.job_title && (
            <p className="text-sm font-medium text-blue-600 mb-1">{info.job_title}</p>
          )}


          {/* Location link */}
          {(info.location_lat && info.location_lng) ? (
            <a
              href={`https://www.google.com/maps?q=${info.location_lat},${info.location_lng}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-xs text-blue-600 hover:underline mb-5"
            >
              <svg className="w-3.5 h-3.5 shrink-0" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17.657 16.657L13.414 20.9a1.998 1.998 0 01-2.827 0l-4.244-4.243a8 8 0 1111.314 0z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 11a3 3 0 11-6 0 3 3 0 016 0z" />
              </svg>
              {info.job_location || 'View on Map'}
            </a>
          ) : info.job_location ? (
            <p className="text-xs text-gray-400 mb-5">{info.job_location}</p>
          ) : null}

          {/* Job Details — collapsible */}
          <div className="border-t border-gray-100 pt-4">
            <button
              onClick={() => setJdOpen(o => !o)}
              className="flex items-center justify-between w-full text-left"
            >
              <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest">Job Details</p>
              <svg
                className={`w-4 h-4 text-gray-400 transition-transform ${jdOpen ? 'rotate-180' : ''}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {jdOpen && (
              <div className="mt-4 space-y-4">
                {(info.min_salary || info.max_salary) && (
                  <JdRow label="Salary">
                    ₹{info.min_salary?.toLocaleString('en-IN') ?? '—'} – ₹{((info.max_salary ?? 0) + 5000).toLocaleString('en-IN')} / month
                  </JdRow>
                )}
                {info.job_timings && <JdRow label="Timings">{info.job_timings}</JdRow>}
                {info.job_type && <JdRow label="Job Type">{info.job_type}</JdRow>}
                {info.industry && <JdRow label="Industry">{info.industry}</JdRow>}
                {info.num_employees != null && <JdRow label="Employees">{info.num_employees}</JdRow>}
                {info.female_employee_pct != null && <JdRow label="Female %">{info.female_employee_pct}%</JdRow>}
                {info.age_requirements && <JdRow label="Age">{info.age_requirements}</JdRow>}
                {info.tools_requirements && <JdRow label="Tools">{info.tools_requirements}</JdRow>}
                <div>
                  <p className="text-xs text-gray-400 mb-1.5">Key Skills</p>
                  <div className="flex flex-wrap gap-1.5">
                    {derivedKeySkills(info.job_title).split(', ').map(s => (
                      <span key={s} className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">{s}</span>
                    ))}
                  </div>
                </div>
                {info.other_details && (
                  <div>
                    <p className="text-xs text-gray-400 mb-1">Other Details</p>
                    <p className="text-xs text-gray-600 leading-relaxed">{info.other_details}</p>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="mt-6 pt-5 border-t border-gray-100">
            <p className="text-xs text-gray-400 leading-relaxed">
              Hi {info.candidate_name?.split(' ')[0]}! Select one of the available interview slots.
            </p>
          </div>

        </div>

        {/* Right panel */}
        <div className="flex-1 px-6 md:px-10 py-10">
          <h2 className="text-base font-semibold text-gray-900 mb-1">Select an interview slot</h2>
          <p className="text-sm text-gray-400 mb-6">Pick the time that works best for you.</p>

          <div className="space-y-3 mb-8">
            {futureSlots.map((slot, i) => {
              const isSelected = selected === slot
              return (
                <button
                  key={i}
                  onClick={() => setSelected(slot)}
                  className={`
                    w-full text-left px-4 py-3.5 rounded-xl border-2 transition-all
                    ${isSelected
                      ? 'border-blue-500 bg-blue-50'
                      : 'border-gray-200 hover:border-blue-300 hover:bg-blue-50/30'
                    }
                  `}
                >
                  <div className="flex items-center gap-3">
                    <div className={`w-4 h-4 rounded-full border-2 flex items-center justify-center shrink-0 ${isSelected ? 'border-blue-500' : 'border-gray-300'}`}>
                      {isSelected && <div className="w-2 h-2 rounded-full bg-blue-500" />}
                    </div>
                    <span className={`text-sm font-medium ${isSelected ? 'text-blue-700' : 'text-gray-700'}`}>
                      {slot}
                    </span>
                  </div>
                </button>
              )
            })}
          </div>

          <div className="border-t border-gray-100 pt-6 max-w-sm">
            <button
              onClick={() => setShowConfirm(true)}
              disabled={!selected || confirmMutation.isPending}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-100 disabled:text-gray-400 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
            >
              {confirmMutation.isPending ? 'Confirming…' : !selected ? 'Select a slot to confirm' : 'Confirm Interview'}
            </button>
            {confirmMutation.isError && (
              <p className="text-red-500 text-xs text-center mt-2">
                {(confirmMutation.error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
                  ?? 'Something went wrong. Please refresh and try again.'}
              </p>
            )}
          </div>

          {/* Confirmation modal */}
          {showConfirm && (
            <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
              <div className="bg-white rounded-2xl shadow-xl max-w-sm w-full p-6">
                <h3 className="text-base font-semibold text-gray-900 mb-2">Confirm your slot</h3>
                <p className="text-sm text-gray-500 mb-1">You've selected:</p>
                <p className="text-sm font-medium text-blue-700 mb-5">{selected}</p>
                <div className="flex gap-3">
                  <button
                    onClick={() => setShowConfirm(false)}
                    className="flex-1 py-2.5 rounded-xl border border-gray-200 text-sm text-gray-600 hover:bg-gray-50"
                  >
                    Go back
                  </button>
                  <button
                    onClick={() => { setShowConfirm(false); confirmMutation.mutate() }}
                    className="flex-1 py-2.5 rounded-xl bg-blue-600 hover:bg-blue-700 text-white text-sm font-semibold"
                  >
                    Yes, confirm
                  </button>
                </div>
              </div>
            </div>
          )}

          <p className="mt-8 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    </div>
  )
}
