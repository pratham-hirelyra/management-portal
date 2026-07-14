import { useState } from 'react'
import type { Candidate } from '../types'
import { scoreBadge } from '../types'

interface Props {
  candidate: Candidate
  onClose: () => void
}

type Tab = 'profile' | 'evaluation' | 'questions'

export default function CandidateDetailModal({ candidate: c, onClose }: Props) {
  const [tab, setTab] = useState<Tab>('profile')

  const evalSummary: Record<string, any> | undefined = (() => {
    if (!c.evaluation_summary) return undefined
    if (typeof c.evaluation_summary === 'string') {
      try { return JSON.parse(c.evaluation_summary) } catch { return undefined }
    }
    return c.evaluation_summary as Record<string, any>
  })()

  const scoring = evalSummary?.technical_scoring as Record<string, any> | undefined
  const questions = scoring
    ? Object.entries(scoring).filter(([k]) => k.startsWith('question'))
    : []
  const totalScore = scoring?.total_score
  const maxScore = scoring?.max_score

  const evalStatusNorm = (c.evaluation_status || '').toLowerCase()
  const statusColor =
    evalStatusNorm === 'pass' ? 'bg-green-100 text-green-700' :
    evalStatusNorm === 'fail' ? 'bg-red-100 text-red-600' :
    'bg-gray-100 text-gray-600'

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl my-4">
        {/* Header */}
        <div className="flex items-start justify-between p-6 border-b border-gray-200">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-lg font-semibold text-gray-900">{c.name}</h2>
              {c.category && (
                <span className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded-full">{c.category}</span>
              )}
              <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColor}`}>
                {c.evaluation_status}
              </span>
            </div>
            <p className="text-sm text-gray-500 mt-0.5">
              {c.age_years ? `${c.age_years} yrs` : ''} {c.gender} · {c.phone}
              {c.email ? ` · ${c.email}` : ''}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">×</button>
        </div>

        {/* Tabs */}
        <div className="flex border-b border-gray-200 px-6">
          {(['profile', 'evaluation', 'questions'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`px-4 py-2.5 text-sm font-medium transition-colors capitalize ${
                tab === t ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <div className="p-6 overflow-y-auto max-h-[70vh]">

          {/* Profile tab */}
          {tab === 'profile' && (
            <div className="space-y-5">
              <Section title="Contact & Location">
                <Row label="Phone" value={c.phone} />
                <Row label="Email" value={c.email} />
                <Row label="Current Location" value={c.current_location} />
                <Row label="Mentioned Location" value={c.mentioned_location} />
                <Row label="State" value={c.state} />
                <Row label="Working Radius" value={c.working_radius ? `${c.working_radius} km` : null} />
              </Section>
              <Section title="Work Profile">
                <Row label="Industry" value={c.industry} />
                <Row label="Job Type" value={c.job_type} />
                <Row label="Experience" value={c.experience ? `${c.experience} yr` : null} />
                <Row label="Work Preference" value={c.work_preference} />
                <Row label="Source" value={c.source} />
                <Row label="Call Status" value={c.call_status} />
              </Section>
              <Section title="Salary">
                <Row label="Current" value={c.current_salary != null ? `₹${c.current_salary.toLocaleString()}` : null} />
              </Section>
              {c.other_details && (() => {
                let od: Record<string, string> = {}
                const raw = c.other_details as any
                if (typeof raw === 'string') {
                  try { od = JSON.parse(raw) } catch {}
                } else if (Array.isArray(raw)) {
                  for (const el of raw) {
                    if (typeof el === 'string') { try { Object.assign(od, JSON.parse(el)) } catch {} }
                    else if (el && typeof el === 'object') Object.assign(od, el)
                  }
                } else if (raw && typeof raw === 'object') {
                  od = raw
                }
                const entries = Object.entries(od).filter(([, v]) => v && typeof v === 'string')
                return entries.length > 0 ? (
                  <Section title="Skills & Tools">
                    {entries.map(([k, v]) => (
                      <Row key={k} label={k.toUpperCase()} value={v} />
                    ))}
                  </Section>
                ) : null
              })()}
              <Section title="Links">
                {c.cv_url && <LinkRow label="CV" href={c.cv_url} />}
                {c.voice_recording_url && <LinkRow label="Voice Recording" href={c.voice_recording_url} />}
                {c.evaluation_pdf_url && <LinkRow label="Evaluation PDF" href={c.evaluation_pdf_url} />}
                {c.final_evaluation_report_url && <LinkRow label="Final Report" href={c.final_evaluation_report_url} />}
              </Section>
            </div>
          )}

          {/* Evaluation tab */}
          {tab === 'evaluation' && (
            <div className="space-y-4">
              <div className="flex items-center gap-4">
                {c.total_score != null && (
                  <div className="text-center">
                    <p className="text-xs text-gray-400 mb-1">Total Score</p>
                    <span className={`text-sm font-bold px-3 py-1 rounded-full ${scoreBadge(c.total_score)}`}>
                      {Number(c.total_score).toFixed(2)}%
                    </span>
                  </div>
                )}
                {totalScore != null && maxScore != null && (
                  <div className="text-center">
                    <p className="text-xs text-gray-400 mb-1">Technical</p>
                    <span className="text-sm font-bold text-gray-700">{totalScore}/{maxScore}</span>
                  </div>
                )}
              </div>

              {evalSummary?.overview_summary && (
                <div>
                  <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">Overview</p>
                  <p className="text-sm text-gray-700 leading-relaxed bg-gray-50 rounded-lg p-3">
                    {evalSummary.overview_summary}
                  </p>
                </div>
              )}

              {c.hiring_decision_reason && !evalSummary?.overview_summary && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <p className="text-xs text-amber-700">{c.hiring_decision_reason}</p>
                </div>
              )}
            </div>
          )}

          {/* Questions tab */}
          {tab === 'questions' && (
            <div className="space-y-4">
              {questions.length === 0 ? (
                <p className="text-sm text-gray-400">No question data available.</p>
              ) : questions.map(([key, q]: [string, any]) => (
                <div key={key} className="border border-gray-200 rounded-lg p-4">
                  <div className="flex items-start justify-between gap-3 mb-2">
                    <p className="text-sm font-medium text-gray-800">{q.question}</p>
                    <span className={`shrink-0 text-xs font-bold px-2 py-0.5 rounded-full ${
                      q.score > 0 ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'
                    }`}>
                      {q.score > 0 ? `+${q.score}` : '0'}
                    </span>
                  </div>
                  <div className="space-y-1 text-xs">
                    <p><span className="text-gray-400">Correct: </span><span className="text-gray-700">{q.correct_answer}</span></p>
                    <p><span className="text-gray-400">Response: </span><span className="text-gray-700">{q.candidate_response}</span></p>
                    {q.justification && (
                      <p className="text-gray-500 italic">{q.justification}</p>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-2">{title}</p>
      <div className="grid grid-cols-2 gap-2">{children}</div>
    </div>
  )
}

function Row({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-sm text-gray-800">{value}</p>
    </div>
  )
}

function LinkRow({ label, href }: { label: string; href: string }) {
  return (
    <div className="col-span-2">
      <a href={href} target="_blank" rel="noreferrer" className="text-xs text-blue-500 hover:underline">
        {label} ↗
      </a>
    </div>
  )
}
