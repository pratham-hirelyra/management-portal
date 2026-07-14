import { useState, useEffect, useRef } from 'react'
import type { Client, ClientStage } from '../types'
import SalaryTypoConfirm, { salaryTypoFlag } from './SalaryTypoConfirm'

const JOB_TITLES = [
  {
    value: 'Senior Accountant',
    desc: 'Min 3 years exp – handles finalization, GST, TDS compliance',
  },
  {
    value: 'Junior Accountant',
    desc: '0–2 years exp – day-to-day bookkeeping and data entry',
  },
]

const INDUSTRIES = [
  'Construction / Real Estate',
  'Healthcare / Pharmaceuticals',
  'IT / Technology',
  'Education / Training',
  'Logistics / Transportation',
  'Financial Services',
  'Hospitality',
  'Food & Beverage / FMCG',
  'Agriculture',
  'Chemicals',
  'Textiles',
  'CA Firm / Tax Consultancy',
  'Others',
]

const BUSINESS_TYPES = [
  'Manufacturer (On Job Work)',
  'Manufacturer (Owned Factory)',
  'Trader',
  'Services',
]

const SHIFT_CHIPS = ['9am – 6pm', '9am – 7pm', '10am – 7pm', '10.30am – 7.30pm', '10am – 8pm']
const DAY_CHIPS   = ['Mon – Sat', 'Mon – Fri', 'Mon – Sun']

const TOOLS = [
  'Tally', 'ZohoBooks', 'QuickBooks',
  'MS Excel', 'Busy Accounting', 'Marg ERP', 'SAP',
  'Oracle Financials', 'FreshBooks', 'Xero',
  'Miracle Accounting', 'Wings Accounting', 'Other',
]

const SELECTABLE_STAGES: { value: ClientStage; label: string }[] = [
  { value: 'lead', label: 'Lead' },
  { value: 'scraped', label: 'Scraped' },
  { value: 'reachout_sent', label: 'Reachout Sent' },
  { value: 'interested', label: 'Interested' },
  { value: 'onboarded', label: 'Onboarded' },
]

type FormData = {
  company_name: string
  poc_name: string
  poc_phone: string
  job_title: string
  open_positions: string
  job_location: string
  industry: string
  industry_other: string
  business_type: string
  office_type: string
  is_ca_firm: boolean
  num_employees: string
  female_employee_pct: string
  diwali_bonus: string
  min_salary: string
  max_salary: string
  key_skills_raw: string
  shift_chip: string
  shift_custom: string
  day_chip: string
  days_custom: string
  gender_requirements: string
  gst: string
  tds: string
  tools_selected: string[]
  job_type: string
  travel_radius: string
  other_details: string
  source: string
  segment: string
  initial_stage: ClientStage
}

interface Props {
  initial?: Partial<Client>
  onSubmit: (data: Record<string, unknown>) => void
  onCancel: () => void
  loading?: boolean
  isCreate?: boolean
}

function Chip({ label, selected, onClick }: { label: string; selected: boolean; onClick: () => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`px-3 py-1.5 rounded-full text-xs font-medium border transition-all whitespace-nowrap
        ${selected
          ? 'bg-blue-600 border-blue-600 text-white'
          : 'bg-white border-gray-300 text-gray-600 hover:border-blue-400 hover:text-blue-600'
        }`}
    >
      {label}
    </button>
  )
}

// job_timings is stored as "{shift} | {days}" — split it back into the
// chip/custom pairs the /client-onboard form uses for editing.
function parseJobTimings(jt?: string | null) {
  const [shiftPart, daysPart] = (jt ?? '').split('|').map(s => s.trim())
  const shift_chip = SHIFT_CHIPS.includes(shiftPart) ? shiftPart : ''
  const shift_custom = shift_chip ? '' : (shiftPart || '')
  const day_chip = DAY_CHIPS.includes(daysPart) ? daysPart : ''
  const days_custom = day_chip ? '' : (daysPart || '')
  return { shift_chip, shift_custom, day_chip, days_custom }
}

function PlacesInput({
  value, onChange, className, required,
}: {
  value: string
  onChange: (val: string) => void
  className: string
  required?: boolean
}) {
  const inputRef = useRef<HTMLInputElement>(null)
  const gmKey = (window.__RUNTIME_CONFIG__?.GMAPS_KEY || import.meta.env.VITE_GMAPS_KEY) as string | undefined

  useEffect(() => {
    if (!gmKey || !inputRef.current) return

    const attach = () => {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const g = (window as any).google
      if (!inputRef.current || !g?.maps?.places) return
      const ac = new g.maps.places.Autocomplete(inputRef.current, {
        componentRestrictions: { country: 'in' },
        fields: ['name', 'formatted_address', 'address_components'],
        types: ['establishment', 'geocode'],
      })
      ac.addListener('place_changed', () => {
        const p = ac.getPlace()
        let loc = ''
        if (p?.address_components) {
          const wanted = ['sublocality_level_1', 'sublocality', 'locality', 'administrative_area_level_2']
          const parts: string[] = []
          wanted.forEach(type => {
            // eslint-disable-next-line @typescript-eslint/no-explicit-any
            const c = p.address_components.find((c: any) => c.types.includes(type))
            if (c && !parts.includes(c.long_name)) parts.push(c.long_name)
          })
          const areaStr = parts.length ? parts.join(', ') : p.formatted_address || ''
          // p.name can itself be a compound "Name, Area" string from Google —
          // only the leading segment is a useful prefix, since areaStr already
          // covers the locality (avoids "X, Area, X, ..., Area" dupes).
          const namePart = (p.name || '').split(',')[0].trim()
          loc = (namePart && !areaStr.toLowerCase().startsWith(namePart.toLowerCase()))
            ? `${namePart}${areaStr ? ', ' + areaStr : ''}`
            : areaStr
        } else {
          loc = p?.name || p?.formatted_address || inputRef.current?.value || ''
        }
        if (loc) onChange(loc)
      })
    }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    if ((window as any).google?.maps?.places) {
      attach()
      return
    }

    if (!document.querySelector('script[src*="maps.googleapis.com"]')) {
      const s = document.createElement('script')
      s.src = `https://maps.googleapis.com/maps/api/js?key=${gmKey}&libraries=places`
      s.async = true
      s.onload = attach
      document.head.appendChild(s)
    } else {
      const t = setInterval(() => {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        if ((window as any).google?.maps?.places) { clearInterval(t); attach() }
      }, 100)
      return () => clearInterval(t)
    }
  }, [gmKey])

  return (
    <input
      ref={inputRef}
      required={required}
      className={className}
      value={value}
      onChange={e => onChange(e.target.value)}
      placeholder="Enter building/estate name – e.g. Capital One, Bopal-Ambli"
      autoComplete="off"
    />
  )
}

export default function ClientForm({ initial = {}, onSubmit, onCancel, loading, isCreate }: Props) {
  const jtParsed = parseJobTimings(initial.job_timings)
  const isOtherIndustry = !!initial.industry && !INDUSTRIES.slice(0, -1).includes(initial.industry)

  const [form, setForm] = useState<FormData>({
    company_name: initial.company_name ?? '',
    poc_name: initial.poc_name ?? '',
    poc_phone: initial.poc_phone ?? '',
    job_title: initial.job_title ?? '',
    open_positions: initial.open_positions != null ? String(initial.open_positions) : '1',
    job_location: initial.job_location ?? '',
    industry: isOtherIndustry ? 'Others' : (initial.industry ?? ''),
    industry_other: isOtherIndustry ? (initial.industry ?? '') : '',
    business_type: (initial as Record<string, unknown>).business_type as string ?? '',
    office_type: (initial as Record<string, unknown>).office_type as string ?? '',
    is_ca_firm: !!(initial as Record<string, unknown>).is_ca_firm,
    num_employees: initial.num_employees != null ? String(initial.num_employees) : '',
    female_employee_pct: (initial as Record<string, unknown>).female_employee_pct != null
      ? String((initial as Record<string, unknown>).female_employee_pct) : '',
    diwali_bonus: (initial as Record<string, unknown>).diwali_bonus as string ?? '',
    min_salary: initial.min_salary != null ? String(initial.min_salary) : '',
    max_salary: initial.max_salary != null ? String(initial.max_salary) : '',
    key_skills_raw: initial.key_skills?.join(', ') ?? '',
    shift_chip: jtParsed.shift_chip,
    shift_custom: jtParsed.shift_custom,
    day_chip: jtParsed.day_chip,
    days_custom: jtParsed.days_custom,
    gender_requirements: initial.gender_requirements ?? '',
    gst: (initial.gst_tds as { gst?: string } | null)?.gst ?? '',
    tds: (initial.gst_tds as { tds?: string } | null)?.tds ?? '',
    tools_selected: initial.tools_requirements
      ? initial.tools_requirements.split(',').map(s => s.trim()).filter(Boolean)
      : [],
    job_type: initial.job_type ?? '',
    travel_radius: (initial as Record<string, unknown>).travel_radius != null ? String((initial as Record<string, unknown>).travel_radius) : '',
    other_details: initial.other_details ?? '',
    source: initial.source ?? '',
    segment: initial.segment ?? '',
    initial_stage: 'lead',
  })

  const [minSalaryConfirmed, setMinSalaryConfirmed] = useState(() => !salaryTypoFlag(initial.min_salary))
  const [maxSalaryConfirmed, setMaxSalaryConfirmed] = useState(() => !salaryTypoFlag(initial.max_salary))
  const [showTools, setShowTools] = useState(false)
  const toolsRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (toolsRef.current && !toolsRef.current.contains(e.target as Node))
        setShowTools(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const set = <K extends keyof FormData>(field: K, val: FormData[K]) =>
    setForm(prev => ({ ...prev, [field]: val }))

  const setMulti = (u: Partial<FormData>) =>
    setForm(prev => ({ ...prev, ...u }))

  const toggleTool = (t: string) =>
    setForm(prev => ({
      ...prev,
      tools_selected: prev.tools_selected.includes(t)
        ? prev.tools_selected.filter(x => x !== t)
        : [...prev.tools_selected, t],
    }))

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()

    const industry = form.industry === 'Others' ? form.industry_other : form.industry
    const key_skills = form.key_skills_raw
      ? form.key_skills_raw.split(',').map(s => s.trim()).filter(Boolean)
      : []
    const gst_tds = (form.gst || form.tds) ? { gst: form.gst, tds: form.tds } : null
    const job_timings = [form.shift_chip || form.shift_custom, form.day_chip || form.days_custom]
      .filter(Boolean).join(' | ') || null
    const tools_requirements = form.tools_selected.join(', ')

    onSubmit({
      company_name: form.company_name,
      poc_name: form.poc_name,
      poc_phone: form.poc_phone || null,
      job_title: form.job_title || null,
      open_positions: form.open_positions ? Number(form.open_positions) : null,
      job_location: form.job_location,
      industry: industry || null,
      business_type: form.business_type || null,
      office_type: form.office_type || null,
      is_ca_firm: form.is_ca_firm,
      num_employees: form.num_employees ? Number(form.num_employees) : null,
      female_employee_pct: form.female_employee_pct ? Number(form.female_employee_pct) : null,
      diwali_bonus: form.diwali_bonus || null,
      min_salary: form.min_salary ? Number(form.min_salary) : null,
      max_salary: form.max_salary ? Number(form.max_salary) : null,
      key_skills,
      job_timings,
      gender_requirements: form.gender_requirements || null,
      gst_tds,
      tools_requirements,
      job_type: form.job_type || null,
      travel_radius: form.travel_radius ? Number(form.travel_radius) : null,
      other_details: form.other_details || null,
      source: form.source || null,
      segment: form.segment || null,
      ...(isCreate ? { initial_stage: form.initial_stage } : {}),
    })
  }

  const inp = 'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400'
  const lbl = 'block text-xs font-semibold text-gray-700 mt-4 mb-1'

  return (
    <form onSubmit={handleSubmit} className="space-y-1">

      {isCreate && (
        <div className="p-3 bg-blue-50 rounded-lg border border-blue-100 mb-2">
          <label className={lbl}>Starting Stage</label>
          <select className={inp} value={form.initial_stage} onChange={e => set('initial_stage', e.target.value as ClientStage)}>
            {SELECTABLE_STAGES.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
          </select>
        </div>
      )}

      <label className={lbl}>Company Name *</label>
      <input required className={inp} value={form.company_name} onChange={e => set('company_name', e.target.value)} />

      <label className={lbl}>Client POC Name *</label>
      <input required className={inp} value={form.poc_name} onChange={e => set('poc_name', e.target.value)} />

      <label className={lbl}>POC WhatsApp Number</label>
      <input className={inp} type="tel" inputMode="numeric" pattern="[0-9]{10}" maxLength={10}
        value={form.poc_phone} onChange={e => set('poc_phone', e.target.value.replace(/\D/g, '').slice(0, 10))} placeholder="9876543210" />

      <div className="grid grid-cols-3 gap-3 mt-4">
        <div className="col-span-2">
          <label className={lbl.replace('mt-4', '')}>Job Title *</label>
          <select required className={inp} value={form.job_title} onChange={e => set('job_title', e.target.value)}>
            <option value="">Please select carefully…</option>
            {JOB_TITLES.map(jt => (
              <option key={jt.value} value={jt.value}>{jt.value} – {jt.desc}</option>
            ))}
          </select>
        </div>
        <div>
          <label className={lbl.replace('mt-4', '')}>Positions *</label>
          <input required type="number" min={1} max={50} className={inp} value={form.open_positions} onChange={e => set('open_positions', e.target.value)} />
        </div>
      </div>

      <label className={lbl}>Job Location *</label>
      <PlacesInput required className={inp} value={form.job_location} onChange={v => set('job_location', v)} />

      <label className={lbl}>Client Industry *</label>
      <select required className={inp} value={form.industry} onChange={e => set('industry', e.target.value)}>
        <option value="">Select industry</option>
        {INDUSTRIES.map(i => <option key={i} value={i}>{i}</option>)}
      </select>
      {form.industry === 'Others' && (
        <input
          required
          className={`${inp} mt-2`}
          placeholder="Specify industry…"
          value={form.industry_other}
          onChange={e => set('industry_other', e.target.value)}
        />
      )}

      <label className={lbl}>Business Type</label>
      <select className={inp} value={form.business_type} onChange={e => set('business_type', e.target.value)}>
        <option value="">Select type</option>
        {BUSINESS_TYPES.map(bt => <option key={bt} value={bt}>{bt}</option>)}
      </select>

      <label className={lbl}>Is this a CA firm?</label>
      <div className="flex gap-6 mt-1">
        {[{ label: 'Yes', value: true }, { label: 'No', value: false }].map(opt => (
          <label key={opt.label} className="flex items-center gap-2 cursor-pointer text-sm text-gray-700">
            <input
              type="radio"
              name="is_ca_firm"
              checked={form.is_ca_firm === opt.value}
              onChange={() => set('is_ca_firm', opt.value)}
              className="accent-blue-500"
            />
            {opt.label}
          </label>
        ))}
      </div>

      <label className={lbl}>Work Location Type (auto-classified from location)</label>
      <select className={inp} value={form.office_type} onChange={e => set('office_type', e.target.value)}>
        <option value="">Auto / Not set</option>
        <option value="Corporate">Corporate</option>
        <option value="Industrial">Industrial</option>
      </select>

      <label className={lbl}>How many employees work in your company today? *</label>
      <input required type="number" min={1} className={inp} value={form.num_employees} onChange={e => set('num_employees', e.target.value)} />

      <label className={lbl}>Female Employee %</label>
      <input type="number" min={0} max={100} className={inp} placeholder="e.g. 40" value={form.female_employee_pct} onChange={e => set('female_employee_pct', e.target.value)} />

      <label className={lbl}>Diwali Bonus</label>
      <div className="flex gap-6 mt-1">
        {['yes', 'no'].map(v => (
          <label key={v} className="flex items-center gap-2 cursor-pointer text-sm text-gray-700">
            <input
              type="radio"
              name="diwali_bonus"
              value={v}
              checked={form.diwali_bonus === v}
              onChange={e => set('diwali_bonus', e.target.value)}
              className="accent-blue-500"
            />
            {v === 'yes' ? 'Yes' : 'No'}
          </label>
        ))}
      </div>

      <label className={lbl}>Minimum Salary * (monthly in hand after PF, PT deductions)</label>
      <input required type="number" min={0} className={inp} value={form.min_salary}
        onChange={e => { set('min_salary', e.target.value); setMinSalaryConfirmed(!salaryTypoFlag(e.target.value)) }} />
      {!minSalaryConfirmed && (
        <SalaryTypoConfirm
          value={form.min_salary}
          onUseSuggested={n => { set('min_salary', String(n)); setMinSalaryConfirmed(true) }}
          onConfirmAsIs={() => setMinSalaryConfirmed(true)}
        />
      )}

      <label className={lbl}>Maximum Salary * (monthly in hand after PF, PT deductions)</label>
      <input required type="number" min={0} className={inp} value={form.max_salary}
        onChange={e => { set('max_salary', e.target.value); setMaxSalaryConfirmed(!salaryTypoFlag(e.target.value)) }} />
      {!maxSalaryConfirmed && (
        <SalaryTypoConfirm
          value={form.max_salary}
          onUseSuggested={n => { set('max_salary', String(n)); setMaxSalaryConfirmed(true) }}
          onConfirmAsIs={() => setMaxSalaryConfirmed(true)}
        />
      )}

      <label className={lbl}>Key Skills Required *</label>
      <textarea required rows={3} className={inp} placeholder="Comma separated skills" value={form.key_skills_raw} onChange={e => set('key_skills_raw', e.target.value)} />

      <label className={lbl}>Job Timings *</label>
      <div className="flex flex-wrap gap-1.5 items-center">
        {SHIFT_CHIPS.map(chip => (
          <Chip key={chip} label={chip} selected={form.shift_chip === chip}
            onClick={() => setMulti({ shift_chip: form.shift_chip === chip ? '' : chip, shift_custom: '' })} />
        ))}
        <input
          className="px-3 py-1.5 rounded-full text-xs border border-gray-300 focus:outline-none focus:ring-1 focus:ring-blue-400 w-36"
          placeholder="Custom timings…"
          value={form.shift_custom}
          onChange={e => setMulti({ shift_custom: e.target.value, shift_chip: '' })}
          onKeyDown={e => { if (e.key === 'Enter') e.preventDefault() }}
        />
      </div>

      <label className={lbl}>Working Days *</label>
      <div className="flex flex-wrap gap-1.5 items-center">
        {DAY_CHIPS.map(chip => (
          <Chip key={chip} label={chip} selected={form.day_chip === chip}
            onClick={() => setMulti({ day_chip: form.day_chip === chip ? '' : chip, days_custom: '' })} />
        ))}
        <input
          className="px-3 py-1.5 rounded-full text-xs border border-gray-300 focus:outline-none focus:ring-1 focus:ring-blue-400 w-36"
          placeholder="Custom days…"
          value={form.days_custom}
          onChange={e => setMulti({ days_custom: e.target.value, day_chip: '' })}
          onKeyDown={e => { if (e.key === 'Enter') e.preventDefault() }}
        />
      </div>

      <label className={lbl}>Gender Requirement *</label>
      <select required className={inp} value={form.gender_requirements} onChange={e => set('gender_requirements', e.target.value)}>
        <option value="">Select</option>
        <option value="Male">Male</option>
        <option value="Female">Female</option>
        <option value="Any">Any</option>
      </select>

      <label className={lbl}>GST</label>
      <select className={inp} value={form.gst} onChange={e => set('gst', e.target.value)}>
        <option value="">Select</option>
        <option value="Filing">Filing</option>
        <option value="Preparation">Preparation</option>
      </select>

      <label className={lbl}>TDS</label>
      <select className={inp} value={form.tds} onChange={e => set('tds', e.target.value)}>
        <option value="">Select</option>
        <option value="Filing">Filing</option>
        <option value="Preparation">Preparation</option>
      </select>

      <label className={lbl}>Accounting Software *</label>
      <div className="relative" ref={toolsRef}>
        <button
          type="button"
          onClick={() => setShowTools(v => !v)}
          className={`w-full rounded-lg border px-3 py-2 text-sm text-left flex justify-between items-center focus:outline-none focus:ring-2 focus:ring-blue-400 ${
            showTools ? 'border-blue-400 rounded-b-none' : 'border-gray-300'
          }`}
        >
          <span className="text-gray-600">
            {!form.tools_selected.length ? 'Select accounting software…'
              : form.tools_selected.length === 1 ? '1 software selected'
              : `${form.tools_selected.length} software selected`}
          </span>
          <span className={`text-xs text-gray-400 transition-transform duration-200 ${showTools ? 'rotate-180' : ''}`}>▼</span>
        </button>
        {showTools && (
          <div className="absolute z-20 left-0 right-0 border border-t-0 border-blue-400 rounded-b-lg bg-white shadow-lg max-h-48 overflow-y-auto">
            {TOOLS.map(t => (
              <div
                key={t}
                onClick={() => toggleTool(t)}
                className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer select-none ${form.tools_selected.includes(t) ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
              >
                <input type="checkbox" readOnly checked={form.tools_selected.includes(t)} className="accent-blue-500 pointer-events-none" />
                <span>{t}</span>
              </div>
            ))}
          </div>
        )}
      </div>
      {form.tools_selected.length > 0 && (
        <div className="flex flex-wrap gap-1.5 mt-2">
          {form.tools_selected.map(t => (
            <span key={t} className="flex items-center gap-1 bg-blue-50 text-blue-800 border border-blue-200 rounded-full px-2.5 py-0.5 text-xs">
              {t}
              <button type="button" onClick={() => toggleTool(t)} className="text-blue-500 hover:text-red-500 font-bold leading-none ml-0.5">✕</button>
            </span>
          ))}
        </div>
      )}

      <label className={lbl}>Job Type</label>
      <div className="flex gap-6 mt-1">
        {['Full Time', 'Part Time'].map(jt => (
          <label key={jt} className="flex items-center gap-2 cursor-pointer text-sm text-gray-700">
            <input
              type="radio"
              name="jobType"
              value={jt}
              checked={form.job_type === jt}
              onChange={e => set('job_type', e.target.value)}
              className="accent-green-500"
            />
            {jt}
          </label>
        ))}
      </div>

      <label className={lbl}>Candidate Travel Radius (km)</label>
      <div className="flex items-center gap-2">
        <input
          type="number"
          min={1}
          max={100}
          className={`${inp} w-32`}
          placeholder="e.g. 10"
          value={form.travel_radius}
          onChange={e => set('travel_radius', e.target.value)}
        />
        <span className="text-xs text-gray-500">km — only candidates within this distance from the job location will be considered. Leave blank to use candidate's own radius.</span>
      </div>

      <label className={lbl}>Other Details</label>
      <textarea
        rows={3}
        className={inp}
        placeholder="e.g. Work from home not allowed · No uniform · Interview at office only · Candidate must be local"
        value={form.other_details}
        onChange={e => set('other_details', e.target.value)}
      />

      <label className={lbl}>Source</label>
      <input className={inp} value={form.source} onChange={e => set('source', e.target.value)} />

      <label className={lbl}>Segment</label>
      <select className={inp} value={form.segment} onChange={e => set('segment', e.target.value)}>
        <option value="">Untagged</option>
        <option value="ca_network">CA Network</option>
        <option value="active_job_post">Active Job Post</option>
        <option value="employer_lead">Employer Lead</option>
      </select>

      <div className="flex justify-end gap-3 pt-4">
        <button type="button" onClick={onCancel} className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
          Cancel
        </button>
        <button type="submit" disabled={loading || !minSalaryConfirmed || !maxSalaryConfirmed} className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50">
          {loading ? 'Saving…' : 'Save'}
        </button>
      </div>
    </form>
  )
}
