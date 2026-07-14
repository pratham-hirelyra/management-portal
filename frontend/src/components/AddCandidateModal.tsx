import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { createCandidate } from '../api/candidates'
import type { CandidateCreatePayload } from '../api/candidates'

interface Props {
  onClose: () => void
}

const EMPTY: CandidateCreatePayload = {
  name: '',
  phone: '',
  age_years: null,
  gender: null,
  email: null,
  experience: null,
  current_location: null,
  current_salary: null,
  expected_salary: null,
  working_radius: null,
  job_type: null,
  work_preference: null,
  industry: null,
  category: null,
  cv_url: null,
  source: null,
  religion: null,
}

function Field({ label, children, required }: { label: string; children: React.ReactNode; required?: boolean }) {
  return (
    <div>
      <label className="block text-xs font-medium text-gray-600 mb-1">
        {label}{required && <span className="text-red-500 ml-0.5">*</span>}
      </label>
      {children}
    </div>
  )
}

const inputCls = "w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
const selectCls = inputCls

export default function AddCandidateModal({ onClose }: Props) {
  const qc = useQueryClient()
  const [form, setForm] = useState<CandidateCreatePayload>(EMPTY)

  const set = (key: keyof CandidateCreatePayload, value: string | number | null) =>
    setForm(f => ({ ...f, [key]: value === '' ? null : value }))

  const mutation = useMutation({
    mutationFn: () => createCandidate(form),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['candidates'] })
      onClose()
    },
  })

  const canSubmit = form.name.trim() !== '' && form.phone.trim() !== ''

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="text-base font-semibold text-gray-800">Add Candidate</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-lg leading-none">×</button>
        </div>

        {/* Body */}
        <div className="overflow-y-auto flex-1 px-6 py-5">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">

            {/* Required */}
            <Field label="Full Name" required>
              <input
                className={inputCls}
                value={form.name}
                onChange={e => set('name', e.target.value)}
                placeholder="e.g. Priya Sharma"
              />
            </Field>
            <Field label="Phone" required>
              <input
                className={inputCls}
                value={form.phone}
                onChange={e => set('phone', e.target.value)}
                placeholder="10-digit mobile number"
              />
            </Field>

            {/* Personal */}
            <Field label="Age">
              <input
                type="number"
                className={inputCls}
                value={form.age_years ?? ''}
                onChange={e => set('age_years', e.target.value ? Number(e.target.value) : null)}
                placeholder="e.g. 28"
                min={18} max={65}
              />
            </Field>
            <Field label="Gender">
              <select className={selectCls} value={form.gender ?? ''} onChange={e => set('gender', e.target.value)}>
                <option value="">Select</option>
                <option>Male</option>
                <option>Female</option>
                <option>Other</option>
              </select>
            </Field>
            <Field label="Email">
              <input
                type="email"
                className={inputCls}
                value={form.email ?? ''}
                onChange={e => set('email', e.target.value)}
                placeholder="candidate@email.com"
              />
            </Field>
            <Field label="Religion">
              <input
                className={inputCls}
                value={form.religion ?? ''}
                onChange={e => set('religion', e.target.value)}
                placeholder="Optional"
              />
            </Field>

            {/* Work details */}
            <Field label="Industry">
              <input
                className={inputCls}
                value={form.industry ?? ''}
                onChange={e => set('industry', e.target.value)}
                placeholder="e.g. Accounting, Finance"
              />
            </Field>
            <Field label="Category">
              <input
                className={inputCls}
                value={form.category ?? ''}
                onChange={e => set('category', e.target.value)}
                placeholder="e.g. Accountant, CA"
              />
            </Field>
            <Field label="Experience (years)">
              <input
                className={inputCls}
                value={form.experience ?? ''}
                onChange={e => set('experience', e.target.value)}
                placeholder="e.g. 3"
              />
            </Field>
            <Field label="Job Type">
              <select className={selectCls} value={form.job_type ?? ''} onChange={e => set('job_type', e.target.value)}>
                <option value="">Select</option>
                <option value="full_time">Full Time</option>
                <option value="part_time">Part Time</option>
              </select>
            </Field>
            <Field label="Work Preference">
              <select className={selectCls} value={form.work_preference ?? ''} onChange={e => set('work_preference', e.target.value)}>
                <option value="">Select</option>
                <option value="Office">Office</option>
                <option value="WFH">Work From Home</option>
                <option value="Hybrid">Hybrid</option>
              </select>
            </Field>
            <Field label="Source">
              <input
                className={inputCls}
                value={form.source ?? ''}
                onChange={e => set('source', e.target.value)}
                placeholder="e.g. WhatsApp, Naukri"
              />
            </Field>

            {/* Location */}
            <Field label="Current Location">
              <input
                className={inputCls}
                value={form.current_location ?? ''}
                onChange={e => set('current_location', e.target.value)}
                placeholder="City or area"
              />
            </Field>
            <Field label="Working Radius (km)">
              <input
                type="number"
                className={inputCls}
                value={form.working_radius ?? ''}
                onChange={e => set('working_radius', e.target.value ? Number(e.target.value) : null)}
                placeholder="e.g. 10"
                min={0}
              />
            </Field>

            {/* Salary */}
            <Field label="Current Salary (₹/month)">
              <input
                type="number"
                className={inputCls}
                value={form.current_salary ?? ''}
                onChange={e => set('current_salary', e.target.value ? Number(e.target.value) : null)}
                placeholder="e.g. 25000"
                min={0}
              />
            </Field>
            <Field label="Expected Salary (₹/month)">
              <input
                type="number"
                className={inputCls}
                value={form.expected_salary ?? ''}
                onChange={e => set('expected_salary', e.target.value ? Number(e.target.value) : null)}
                placeholder="e.g. 30000"
                min={0}
              />
            </Field>

            {/* CV */}
            <Field label="CV URL">
              <input
                className={inputCls}
                value={form.cv_url ?? ''}
                onChange={e => set('cv_url', e.target.value)}
                placeholder="https://drive.google.com/…"
              />
            </Field>
          </div>

          {mutation.isError && (
            <p className="mt-4 text-sm text-red-600">
              {(mutation.error as any)?.response?.data?.detail ?? 'Failed to add candidate. Please try again.'}
            </p>
          )}
        </div>

        {/* Footer */}
        <div className="flex gap-2 justify-end px-6 py-4 border-t border-gray-100">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={() => mutation.mutate()}
            disabled={!canSubmit || mutation.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed font-medium"
          >
            {mutation.isPending ? 'Adding…' : 'Add Candidate'}
          </button>
        </div>
      </div>
    </div>
  )
}
