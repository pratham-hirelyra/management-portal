import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { Client } from '../types'
import { api } from '../api/axios'

interface AgreementFields {
  client_name: string
  poc_name: string
  job_title: string
  job_location: string
  min_salary: string
  max_salary: string
  salary_deductions: string
  key_skills: string
  job_timings: string
  tools_requirements: string
  other_details: string
  age_requirements: string
  gender_requirements: string
  fee_amount: string
  payment_terms: string
  replacement_period: string
  payment_due_days: string
  backdoor_period: string
}

interface Props {
  client: Client
  onClose: () => void
}

function toFields(c: Client): AgreementFields {
  return {
    client_name: c.company_name ?? '',
    poc_name: c.poc_name ?? '',
    job_title: c.job_title ?? '',
    job_location: c.job_location ?? c.location ?? '',
    min_salary: c.min_salary?.toString() ?? '',
    max_salary: c.max_salary?.toString() ?? '',
    salary_deductions: c.salary_deductions ?? '',
    key_skills: Array.isArray(c.key_skills) ? c.key_skills.join(', ') : '',
    job_timings: c.job_timings ?? '',
    tools_requirements: c.tools_requirements ?? '',
    other_details: c.other_details ?? '',
    age_requirements: c.age_requirements ?? '',
    gender_requirements: c.gender_requirements ?? '',
    fee_amount: c.fee_amount?.toString() ?? '',
    payment_terms: c.payment_terms ?? 'immediate',
    replacement_period: c.replacement_period ?? '3 months',
    payment_due_days: c.payment_due_days?.toString() ?? '10',
    backdoor_period: c.backdoor_period ?? '12 months',
  }
}

export default function AgreementModal({ client, onClose }: Props) {
  const qc = useQueryClient()
  const [fields, setFields] = useState<AgreementFields>(toFields(client))
  const [generatedUrl, setGeneratedUrl] = useState<string | null>(client.agreement_url ?? null)

  const set = (k: keyof AgreementFields, v: string) =>
    setFields(prev => ({ ...prev, [k]: v }))

  const generate = useMutation({
    mutationFn: () =>
      api.post<{ data: { url: string } }>(`/clients/${client.id}/generate-agreement`, {
        ...fields,
        min_salary: fields.min_salary ? parseFloat(fields.min_salary) : null,
        max_salary: fields.max_salary ? parseFloat(fields.max_salary) : null,
        payment_due_days: fields.payment_due_days ? parseInt(fields.payment_due_days, 10) : null,
      }).then(r => r.data.data.url),
    onSuccess: url => {
      setGeneratedUrl(url)
      qc.invalidateQueries({ queryKey: ['client', client.id] })
    },
  })

  const errorMsg = generate.isError
    ? ((generate.error as any)?.response?.data?.detail || (generate.error as any)?.message || 'Unknown error')
    : null

  const inp = 'w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400'
  const lbl = 'block text-xs font-medium text-gray-500 mb-0.5'

  const Field = ({ label, k }: { label: string; k: keyof AgreementFields }) => (
    <div>
      <label className={lbl}>{label}</label>
      <input className={inp} value={fields[k]} onChange={e => set(k, e.target.value)} />
    </div>
  )

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4 overflow-y-auto">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6 my-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold">Generate Recruitment Agreement</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
        </div>

        <div className="space-y-3 mb-5">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Client Name" k="client_name" />
            <Field label="POC Name" k="poc_name" />
            <Field label="Job Title" k="job_title" />
            <Field label="Job Location" k="job_location" />
            <Field label="Min Salary (₹)" k="min_salary" />
            <Field label="Max Salary (₹)" k="max_salary" />
            <Field label="Fee Amount" k="fee_amount" />
            <Field label="Replacement Period" k="replacement_period" />
            <Field label="Payment Terms" k="payment_terms" />
            <Field label="Payment Due (days)" k="payment_due_days" />
            <Field label="Backdoor Hiring Period" k="backdoor_period" />
            <Field label="Salary Deductions" k="salary_deductions" />
            <Field label="Job Timings" k="job_timings" />
            <Field label="Gender Requirements" k="gender_requirements" />
          </div>
          <div>
            <label className={lbl}>Key Skills</label>
            <input className={inp} value={fields.key_skills} onChange={e => set('key_skills', e.target.value)} />
          </div>
          <div>
            <label className={lbl}>Tools Requirements</label>
            <input className={inp} value={fields.tools_requirements} onChange={e => set('tools_requirements', e.target.value)} />
          </div>
          <div>
            <label className={lbl}>Age Requirements</label>
            <input className={inp} value={fields.age_requirements} onChange={e => set('age_requirements', e.target.value)} />
          </div>
          <div>
            <label className={lbl}>Other Details</label>
            <textarea rows={2} className={inp} value={fields.other_details} onChange={e => set('other_details', e.target.value)} />
          </div>
        </div>

        {generate.isError && (
          <p className="text-xs text-red-500 mb-3">Failed: {errorMsg}</p>
        )}

        {generatedUrl && (
          <div className="mb-4 p-3 bg-green-50 border border-green-200 rounded-lg flex items-center justify-between gap-3">
            <span className="text-xs text-green-700 font-medium">PDF ready</span>
            <a href={generatedUrl} target="_blank" rel="noreferrer" className="text-xs text-blue-600 hover:underline truncate max-w-xs">
              {generatedUrl}
            </a>
          </div>
        )}

        <div className="flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
            Close
          </button>
          <button
            onClick={() => generate.mutate()}
            disabled={generate.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {generate.isPending ? 'Generating PDF…' : generatedUrl ? 'Regenerate PDF' : 'Generate PDF'}
          </button>
        </div>
      </div>
    </div>
  )
}
