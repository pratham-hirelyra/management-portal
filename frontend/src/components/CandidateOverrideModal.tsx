import { useState, useEffect, useRef } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { upsertCandidateOverride } from '../api/candidates'
import type { Candidate } from '../types'
import SalaryTypoConfirm, { salaryTypoFlag } from './SalaryTypoConfirm'

declare global { interface Window { google: any } }

const CANDIDATE_LABELS = ['Quick Joiner', 'High Stability', 'Average Stability', 'High Turnover Risk'] as const
const GST_TDS_OPTIONS = [
  { value: '', label: '— not set —' },
  { value: 'preparation', label: 'Preparation' },
  { value: 'filing', label: 'Filing' },
]
const WORK_PREF_OPTIONS = [
  { value: 'corporate',  label: 'Corporate Office (e.g. Prahladnagar, Bopal-Ambli, Navrangpura, Ashram Road)' },
  { value: 'industrial', label: 'Industrial Area (e.g. Kathwada, Odhav, Sarkhej, Vatva)' },
  { value: 'ca_office',  label: 'CA Office (Chartered Accountant firm or accounting practice)' },
  { value: 'any',        label: 'No preference — show me all opportunities' },
]

function normalizeWorkPref(raw: string | null | undefined): string {
  const v = (raw ?? '').toLowerCase().trim()
  if (!v) return ''
  if (v === 'corporate' || v === 'corporate only' || v.startsWith('corporate')) return 'corporate'
  if (v === 'industrial' || v.startsWith('industrial')) return 'industrial'
  if (v === 'ca_office' || v.startsWith('ca office') || v.startsWith('ca_office') || v === 'ca') return 'ca_office'
  if (v === 'any' || v.startsWith('no pref')) return 'any'
  return ''
}

// Splits a comma-joined raw value into known preference tokens, deduped.
// Unrecognized tokens (e.g. the system-set 'not_looking' sentinel) are dropped —
// same as the previous single-select normalizeWorkPref(), so saving without
// touching this field behaves exactly as it did before this was multi-select.
function parseWorkPrefs(raw: string | null | undefined): string[] {
  const v = (raw ?? '').trim()
  if (!v) return []
  const out: string[] = []
  for (const part of v.split(',')) {
    const norm = normalizeWorkPref(part)
    if (norm && !out.includes(norm)) out.push(norm)
  }
  return out
}

const TOOLS = [
  'Tally', 'ZohoBooks', 'QuickBooks', 'Busy Accounting', 'Marg ERP',
  'SAP', 'Oracle Financials', 'FreshBooks', 'Xero',
  'Miracle Accounting', 'Wings Accounting', 'Other',
]

function loadGMaps(key: string, cb: () => void) {
  if (window.google) { cb(); return }
  if (document.getElementById('gmaps-script')) {
    const wait = setInterval(() => { if (window.google) { clearInterval(wait); cb() } }, 100)
    return
  }
  const s = document.createElement('script')
  s.id = 'gmaps-script'
  s.src = `https://maps.googleapis.com/maps/api/js?key=${key}&libraries=places`
  s.onload = cb
  document.head.appendChild(s)
}

function parseTools(raw: string | undefined | null): string[] {
  if (!raw) return []
  return raw.split(',').map(s => s.trim()).filter(s => TOOLS.includes(s))
}

function flattenOtherDetails(od: Record<string, string> | any[] | null | undefined): Record<string, string> {
  if (!od) return {}
  if (Array.isArray(od)) {
    const merged: Record<string, string> = {}
    for (const el of od) {
      if (typeof el === 'string') { try { Object.assign(merged, JSON.parse(el)) } catch {} }
      else if (el && typeof el === 'object') Object.assign(merged, el)
    }
    return merged
  }
  return od as Record<string, string>
}

interface Props {
  candidate: Candidate
  onClose: () => void
}

export default function CandidateOverrideModal({ candidate: c, onClose }: Props) {
  const qc = useQueryClient()
  const od = flattenOtherDetails(c.other_details as any)

  const [phone, setPhone]               = useState(c.phone ?? '')
  const [isActive, setIsActive]         = useState(c.is_active ?? true)
  const [dndUntil, setDndUntil]         = useState(
    c.dnd_until ? new Date(c.dnd_until).toISOString().split('T')[0] : ''
  )
  const [location, setLocation]         = useState(c.current_location ?? '')
  const [radius, setRadius]             = useState(c.working_radius != null ? String(c.working_radius) : '')
  const [currentSalary, setCurrentSalary] = useState(c.current_salary != null ? String(c.current_salary) : '')
  const [salaryConfirmed, setSalaryConfirmed] = useState(() => !salaryTypoFlag(c.current_salary))
  const [gst, setGst]                   = useState((od.gst ?? '').toLowerCase())
  const [tds, setTds]                   = useState((od.tds ?? '').toLowerCase())
  const [tools, setTools]               = useState<string[]>(() => parseTools(od.tools))
  const [showTools, setShowTools]       = useState(false)
  const [workPref, setWorkPref]         = useState<string[]>(() => parseWorkPrefs(c.work_preference))
  const [showWorkPref, setShowWorkPref] = useState(false)
  const [jobType, setJobType]           = useState(c.job_type ?? '')
  const [techScore, setTechScore]       = useState(c.technical_evaluation_score != null ? String(c.technical_evaluation_score) : '')
  const [notes, setNotes]               = useState(c.recruiter_notes ?? '')
  const [photoUrl, setPhotoUrl]         = useState(c.photo_url ?? '')
  const [labels, setLabels]             = useState<string[]>(c.labels ?? [])
  const [religion, setReligion]         = useState<string>(c.religion ?? '')

  const locationRef = useRef<HTMLInputElement>(null)
  const toolsRef    = useRef<HTMLDivElement>(null)
  const workPrefRef = useRef<HTMLDivElement>(null)

  // Google Maps autocomplete
  useEffect(() => {
    const key = (window as any).__RUNTIME_CONFIG__?.GMAPS_KEY || import.meta.env.VITE_GMAPS_KEY
    if (!key) return
    loadGMaps(key, () => {
      const input = locationRef.current
      if (!input) return
      const ac = new window.google.maps.places.Autocomplete(input, {
        componentRestrictions: { country: 'in' },
        types: ['geocode'],
      })
      ac.addListener('place_changed', () => {
        const place = ac.getPlace()
        if (place?.address_components) {
          const wanted = [
            'sublocality_level_2', 'sublocality_level_1', 'sublocality',
            'locality', 'administrative_area_level_2', 'administrative_area_level_1',
          ]
          const parts: string[] = []
          wanted.forEach(type => {
            const comp = place.address_components.find((c: any) => c.types.includes(type))
            if (comp && !parts.includes(comp.long_name)) parts.push(comp.long_name)
          })
          setLocation(parts.length ? parts.join(', ') : place.formatted_address || '')
        }
      })
    })
  }, [])

  // Close tools / work-preference dropdowns on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (toolsRef.current && !toolsRef.current.contains(e.target as Node)) setShowTools(false)
      if (workPrefRef.current && !workPrefRef.current.contains(e.target as Node)) setShowWorkPref(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const toggleTool  = (t: string) => setTools(prev => prev.includes(t) ? prev.filter(x => x !== t) : [...prev, t])
  const toggleLabel = (l: string) => setLabels(prev => prev.includes(l) ? prev.filter(x => x !== l) : [...prev, l])

  const toggleWorkPref = (v: string) =>
    setWorkPref(prev => {
      if (prev.includes(v)) return prev.filter(x => x !== v)
      if (v === 'any') return ['any']
      return [...prev.filter(x => x !== 'any'), v]
    })

  const save = useMutation({
    mutationFn: () => upsertCandidateOverride(c.id, {
      phone: phone !== c.phone ? phone || null : undefined,
      is_active: isActive,
      dnd_until: dndUntil || null,
      current_location: location || null,
      working_radius: radius ? Number(radius) : null,
      current_salary: currentSalary ? Number(currentSalary) : null,
      gst: gst || null,
      tds: tds || null,
      tools: tools.length ? tools.join(', ') : null,
      work_preference: workPref.length ? workPref.join(', ') : null,
      job_type: jobType || null,
      // send the same value for both score columns
      technical_evaluation_score: techScore ? Number(techScore) : null,
      total_score: techScore ? Number(techScore) : null,
      recruiter_notes: notes || null,
      labels,
      photo_url: photoUrl || null,
      religion: religion || null,
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['candidates'] })
      onClose()
    },
  })

  const field = 'w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300'

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl w-full max-w-lg shadow-xl max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <div>
            <h2 className="font-semibold text-gray-900">{c.name}</h2>
            <p className="text-xs text-gray-400">{c.phone}</p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <div className="px-6 py-5 space-y-5">

          {/* Phone */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Mobile Number</label>
            <input
              type="tel"
              className={field}
              placeholder="10-digit number"
              value={phone}
              onChange={e => setPhone(e.target.value.replace(/\D/g, '').slice(0, 10))}
            />
            {phone.length > 0 && phone.length !== 10 && (
              <p className="text-xs text-red-500 mt-1">Must be exactly 10 digits</p>
            )}
          </div>

          {/* Active toggle */}
          <div className="flex items-center justify-between p-4 rounded-xl border-2 border-gray-200">
            <div>
              <p className="text-sm font-medium text-gray-800">Candidate Status</p>
              <p className="text-xs text-gray-400 mt-0.5">Inactive candidates are excluded from auto-matching</p>
            </div>
            <button
              onClick={() => setIsActive(v => !v)}
              className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors ${isActive ? 'bg-green-500' : 'bg-gray-300'}`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${isActive ? 'translate-x-6' : 'translate-x-1'}`} />
            </button>
          </div>
          <p className={`-mt-3 text-xs font-medium ${isActive ? 'text-green-600' : 'text-gray-400'}`}>
            {isActive ? 'Active — available for matching' : 'Inactive — excluded from matching'}
          </p>

          {/* DND Until */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">
              DND Until <span className="text-gray-400 font-normal">(leave blank to clear)</span>
            </label>
            <div className="flex gap-2 items-center">
              <input
                type="date"
                className="border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                value={dndUntil}
                onChange={e => setDndUntil(e.target.value)}
              />
              {dndUntil && (
                <button type="button" onClick={() => setDndUntil('')} className="text-xs text-red-500 hover:text-red-700">
                  Clear
                </button>
              )}
            </div>
            {dndUntil && (
              <p className="text-xs text-amber-600 mt-1">
                Excluded from matching until {new Date(dndUntil).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
              </p>
            )}
          </div>

          {/* Labels */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">Candidate Labels</label>
            <div className="flex flex-wrap gap-2">
              {CANDIDATE_LABELS.map(label => (
                <button
                  key={label}
                  type="button"
                  onClick={() => toggleLabel(label)}
                  className={`px-3 py-1 text-xs rounded-full border font-medium transition-colors ${
                    labels.includes(label)
                      ? 'bg-blue-600 text-white border-blue-600'
                      : 'bg-white text-gray-600 border-gray-300 hover:border-blue-400 hover:text-blue-600'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* Religion classification */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">Religion Classification</label>
            <div className="flex gap-2">
              {([['M', 'Muslim', 'bg-teal-600 text-white border-teal-600', 'border-gray-300 text-gray-600 hover:border-teal-400 hover:text-teal-600'],
                 ['N', 'Not Muslim', 'bg-gray-600 text-white border-gray-600', 'border-gray-300 text-gray-600 hover:border-gray-500'],
                 ['', 'Unknown', 'bg-amber-500 text-white border-amber-500', 'border-gray-300 text-gray-400 hover:border-amber-400']] as const).map(([val, label, activeClass, inactiveClass]) => (
                <button
                  key={val}
                  type="button"
                  onClick={() => setReligion(religion === val ? '' : val)}
                  className={`px-3 py-1.5 text-xs rounded-full border font-medium transition-colors ${religion === val ? activeClass : inactiveClass}`}
                >
                  {label}
                </button>
              ))}
            </div>
            <p className="text-[11px] text-gray-400 mt-1">
              {religion === 'M' ? 'Marked as Muslim — will be excluded when "Include Muslim" is off' :
               religion === 'N' ? 'Marked as not Muslim' :
               'Not classified yet — AI will attempt to classify on next cron run'}
            </p>
          </div>

          {/* Location & radius */}
          <div className="grid grid-cols-2 gap-4">
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-500 mb-1">
                Current Location
                <span className="ml-1 font-normal text-gray-400">(coordinates updated automatically)</span>
              </label>
              <input
                ref={locationRef}
                className={field}
                placeholder="Search area or city…"
                value={location}
                onChange={e => setLocation(e.target.value)}
                autoComplete="off"
              />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Working Radius (km)</label>
              <input type="number" className={field} placeholder="e.g. 15" value={radius} onChange={e => setRadius(e.target.value)} />
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Work Preference</label>
              <div className="relative" ref={workPrefRef}>
                <button
                  type="button"
                  onClick={() => setShowWorkPref(v => !v)}
                  className={`w-full px-3 py-2 rounded-lg border text-sm text-left flex justify-between items-center focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white ${
                    showWorkPref ? 'border-blue-400 rounded-b-none' : 'border-gray-200'
                  }`}
                >
                  <span className="text-gray-600">
                    {workPref.length === 0 ? 'Select…'
                      : workPref.length === 1 ? '1 selected'
                      : `${workPref.length} selected`}
                  </span>
                  <span className={`text-xs text-gray-400 transition-transform duration-200 ${showWorkPref ? 'rotate-180' : ''}`}>▼</span>
                </button>
                {showWorkPref && (
                  <div className="absolute z-20 left-0 right-0 border border-t-0 border-blue-400 rounded-b-lg bg-white shadow-lg max-h-56 overflow-y-auto">
                    {WORK_PREF_OPTIONS.map(o => (
                      <div
                        key={o.value}
                        onClick={() => toggleWorkPref(o.value)}
                        className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer select-none ${workPref.includes(o.value) ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                      >
                        <input type="checkbox" readOnly checked={workPref.includes(o.value)} className="accent-blue-600 pointer-events-none mt-0.5" />
                        <span>{o.label}</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {workPref.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {workPref.map(v => (
                    <span key={v} className="flex items-center gap-1 bg-blue-50 text-blue-800 border border-blue-200 rounded-full px-2.5 py-0.5 text-xs">
                      {WORK_PREF_OPTIONS.find(o => o.value === v)?.label || v}
                      <button type="button" onClick={() => toggleWorkPref(v)} className="text-blue-500 hover:text-red-500 font-bold leading-none ml-0.5">✕</button>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Salary */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Current Salary (₹/month)</label>
            <input type="number" className={field} placeholder="e.g. 18000" value={currentSalary} onChange={e => { setCurrentSalary(e.target.value); setSalaryConfirmed(!salaryTypoFlag(e.target.value)) }} />
            {!salaryConfirmed && (
              <SalaryTypoConfirm
                value={currentSalary}
                onUseSuggested={n => { setCurrentSalary(String(n)); setSalaryConfirmed(true) }}
                onConfirmAsIs={() => setSalaryConfirmed(true)}
              />
            )}
          </div>

          {/* GST / TDS */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-2">GST / TDS Skills</label>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">GST</label>
                <select className={`${field} bg-white`} value={gst} onChange={e => setGst(e.target.value)}>
                  {GST_TDS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              <div>
                <label className="block text-[11px] text-gray-400 mb-1">TDS</label>
                <select className={`${field} bg-white`} value={tds} onChange={e => setTds(e.target.value)}>
                  {GST_TDS_OPTIONS.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
            </div>
          </div>

          {/* Tools — checkbox dropdown */}
          <div>
            <label className="block text-xs font-medium text-gray-500 mb-1">Accounting Tools</label>
            <div className="relative" ref={toolsRef}>
              <button
                type="button"
                onClick={() => setShowTools(v => !v)}
                className={`w-full px-3 py-2 rounded-lg border text-sm text-left flex justify-between items-center focus:outline-none focus:ring-2 focus:ring-blue-300 bg-white ${
                  showTools ? 'border-blue-400 rounded-b-none' : 'border-gray-200'
                }`}
              >
                <span className="text-gray-600">
                  {tools.length === 0 ? 'Select tools…'
                    : tools.length === 1 ? '1 tool selected'
                    : `${tools.length} tools selected`}
                </span>
                <span className={`text-xs text-gray-400 transition-transform duration-200 ${showTools ? 'rotate-180' : ''}`}>▼</span>
              </button>
              {showTools && (
                <div className="absolute z-20 left-0 right-0 border border-t-0 border-blue-400 rounded-b-lg bg-white shadow-lg max-h-48 overflow-y-auto">
                  {TOOLS.map(t => (
                    <div
                      key={t}
                      onClick={() => toggleTool(t)}
                      className={`flex items-center gap-2 px-3 py-2 text-sm cursor-pointer select-none ${tools.includes(t) ? 'bg-blue-50' : 'hover:bg-gray-50'}`}
                    >
                      <input type="checkbox" readOnly checked={tools.includes(t)} className="accent-blue-600 pointer-events-none" />
                      <span>{t}</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {tools.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-2">
                {tools.map(t => (
                  <span key={t} className="flex items-center gap-1 bg-blue-50 text-blue-800 border border-blue-200 rounded-full px-2.5 py-0.5 text-xs">
                    {t}
                    <button type="button" onClick={() => toggleTool(t)} className="text-blue-500 hover:text-red-500 font-bold leading-none ml-0.5">✕</button>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* AI Score */}
          <div className="rounded-xl border border-amber-200 bg-amber-50 p-4">
            <p className="text-xs font-medium text-amber-800 mb-3">
              AI Score Override
              <span className="ml-1 font-normal text-amber-600">— updates category and regenerates PDF</span>
            </p>
            <div>
              <label className="block text-[11px] text-gray-500 mb-1">Technical Score (0–100)</label>
              <input
                type="number" min={0} max={100}
                className="w-full border border-amber-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-300 bg-white"
                placeholder="e.g. 72"
                value={techScore}
                onChange={e => setTechScore(e.target.value)}
              />
            </div>
          </div>

          {/* Job Type & misc */}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-2">Job Type</label>
              <div className="flex gap-4 mt-1">
                {['Full Time', 'Part Time'].map(opt => (
                  <label key={opt} className="flex items-center gap-2 cursor-pointer">
                    <input
                      type="radio"
                      name="jobType"
                      value={opt}
                      checked={jobType === opt}
                      onChange={() => setJobType(opt)}
                      className="accent-blue-600"
                    />
                    <span className="text-sm text-gray-700">{opt}</span>
                  </label>
                ))}
              </div>
            </div>
            <div>
              <label className="block text-xs font-medium text-gray-500 mb-1">Photo URL</label>
              <input type="url" className={field} placeholder="https://…" value={photoUrl} onChange={e => setPhotoUrl(e.target.value)} />
            </div>
            <div className="col-span-2">
              <label className="block text-xs font-medium text-gray-500 mb-1">Recruiter Notes</label>
              <textarea
                rows={3}
                className="w-full border border-gray-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300 resize-none"
                placeholder="Internal notes about this candidate…"
                value={notes}
                onChange={e => setNotes(e.target.value)}
              />
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3">
          <button onClick={onClose} className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800">Cancel</button>
          <button
            disabled={save.isPending || (phone.length > 0 && phone.length !== 10) || !salaryConfirmed}
            onClick={() => save.mutate()}
            className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50"
          >
            {save.isPending ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
