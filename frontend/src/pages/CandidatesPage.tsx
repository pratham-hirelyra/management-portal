import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { getCandidates, importFromSheet, deleteCandidate, triggerAICall } from '../api/candidates'
import type { Candidate } from '../types'
import { scoreBadge } from '../types'
import CandidateDetailModal from '../components/CandidateDetailModal'
import CandidateOverrideModal from '../components/CandidateOverrideModal'
import AddCandidateModal from '../components/AddCandidateModal'

export default function CandidatesPage() {
  const qc = useQueryClient()
  const [search, setSearch] = useState('')
  const [industry, setIndustry] = useState('')
  const [evalStatus, setEvalStatus] = useState('')
  const [activeFilter, setActiveFilter] = useState<'all' | 'active' | 'inactive'>('all')
  const [selected, setSelected] = useState<Candidate | null>(null)
  const [editing, setEditing] = useState<Candidate | null>(null)
  const [showImport, setShowImport] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [sheetUrl, setSheetUrl] = useState('')

  const importMutation = useMutation({
    mutationFn: () => importFromSheet(sheetUrl),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['candidates'] }),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => deleteCandidate(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['candidates'] }),
  })

  const [page, setPage] = useState(1)
  const PAGE_SIZE = 50

  const is_active =
    activeFilter === 'active' ? true :
    activeFilter === 'inactive' ? false :
    undefined

  const { data, isLoading } = useQuery({
    queryKey: ['candidates', search, industry, evalStatus, activeFilter, page],
    queryFn: () => getCandidates({
      search: search || undefined,
      industry: industry || undefined,
      evaluation_status: evalStatus || undefined,
      is_active,
      page,
      page_size: PAGE_SIZE,
    }),
  })

  const candidates = data?.candidates ?? []
  const total = data?.total ?? 0
  const totalPages = Math.ceil(total / PAGE_SIZE)

  const resetPage = () => setPage(1)

  const importResult = importMutation.data

  return (
    <div className="p-6">
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 mb-6">
        <h1 className="text-xl font-bold text-gray-900">Candidates</h1>
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => setShowAdd(true)}
            className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 font-medium"
          >
            + Add Candidate
          </button>
          <button
            onClick={() => { setShowImport(true); importMutation.reset() }}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
          >
            Import from Sheets
          </button>
        </div>
      </div>

      {/* Import modal */}
      {showImport && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-lg">
            <h2 className="text-base font-semibold text-gray-800 mb-1">Import from Google Sheets</h2>
            <p className="text-xs text-gray-500 mb-4">
              Share the sheet publicly (Anyone with link → Viewer), then paste the URL below.
            </p>

            <input
              type="text"
              value={sheetUrl}
              onChange={e => setSheetUrl(e.target.value)}
              placeholder="https://docs.google.com/spreadsheets/d/…"
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 mb-4"
            />

            {importResult && (
              <div className="mb-4 p-3 rounded-lg bg-gray-50 text-xs space-y-1">
                <p className="font-medium text-gray-700">Import complete</p>
                <p className="text-green-700">✓ {importResult.inserted} new candidates added</p>
                <p className="text-blue-700">↻ {importResult.updated} existing candidates updated</p>
                {importResult.skipped > 0 && (
                  <p className="text-gray-500">— {importResult.skipped} rows skipped (no phone)</p>
                )}
                {importResult.errors.length > 0 && (
                  <details className="mt-1">
                    <summary className="text-red-600 cursor-pointer">{importResult.errors.length} errors</summary>
                    <ul className="mt-1 space-y-0.5 text-red-500">
                      {importResult.errors.map((e, i) => (
                        <li key={i}>Row {e.row} ({e.phone}): {e.error}</li>
                      ))}
                    </ul>
                  </details>
                )}
              </div>
            )}

            {importMutation.isError && (
              <p className="mb-4 text-xs text-red-600">
                {(importMutation.error as any)?.response?.data?.detail ?? 'Import failed'}
              </p>
            )}

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => { setShowImport(false); setSheetUrl('') }}
                className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
              >
                Close
              </button>
              <button
                onClick={() => importMutation.mutate()}
                disabled={!sheetUrl.trim() || importMutation.isPending}
                className="px-3 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
              >
                {importMutation.isPending ? 'Importing…' : 'Import'}
              </button>
            </div>
          </div>
        </div>
      )}

      <div className="flex flex-wrap gap-3 mb-6">
        <input
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 w-56"
          placeholder="Search name / phone…"
          value={search}
          onChange={e => { setSearch(e.target.value); resetPage() }}
        />
        <input
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 w-40"
          placeholder="Industry"
          value={industry}
          onChange={e => { setIndustry(e.target.value); resetPage() }}
        />
        <select
          className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={evalStatus}
          onChange={e => { setEvalStatus(e.target.value); resetPage() }}
        >
          <option value="">All statuses</option>
          <option value="Pass">Pass</option>
          <option value="Fail">Fail</option>
          <option value="HIRED">Hired / Placed</option>
        </select>

        {/* Active filter */}
        <div className="flex rounded-lg border border-gray-300 overflow-hidden text-sm">
          {(['all', 'active', 'inactive'] as const).map(opt => (
            <button
              key={opt}
              onClick={() => { setActiveFilter(opt); resetPage() }}
              className={`px-3 py-2 capitalize transition-colors ${
                activeFilter === opt
                  ? 'bg-blue-600 text-white'
                  : 'bg-white text-gray-600 hover:bg-gray-50'
              }`}
            >
              {opt}
            </button>
          ))}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[...Array(8)].map((_, i) => (
            <div key={i} className="h-14 rounded-lg bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-200">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['Name', 'Phone', 'Category', 'Industry', 'Location', 'Exp', 'Salary', 'Score', 'Status', 'Religion', 'Active', 'Mappings', 'Labels', ''].map(h => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {candidates.length === 0 ? (
                <tr><td colSpan={14} className="px-4 py-8 text-center text-sm text-gray-400">No candidates found.</td></tr>
              ) : candidates.map(c => (
                <CandidateRow
                  key={c.id}
                  candidate={c}
                  onView={() => setSelected(c)}
                  onEdit={() => setEditing(c)}
                  onDelete={() => deleteMutation.mutate(c.id)}
                  isDeleting={deleteMutation.isPending && deleteMutation.variables === c.id}
                />
              ))}

            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div className="flex items-center justify-between mt-4">
          <p className="text-xs text-gray-500">
            Showing {((page - 1) * PAGE_SIZE) + 1}–{Math.min(page * PAGE_SIZE, total)} of {total.toLocaleString()} candidates
          </p>
          <div className="flex items-center gap-1">
            <button
              onClick={() => setPage(1)}
              disabled={page === 1}
              className="px-2 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              «
            </button>
            <button
              onClick={() => setPage(p => p - 1)}
              disabled={page === 1}
              className="px-3 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Prev
            </button>

            {/* Page number pills */}
            {Array.from({ length: Math.min(5, totalPages) }, (_, i) => {
              const start = Math.max(1, Math.min(page - 2, totalPages - 4))
              return start + i
            }).map(p => (
              <button
                key={p}
                onClick={() => setPage(p)}
                className={`px-3 py-1 text-xs rounded border transition-colors ${
                  p === page
                    ? 'bg-blue-600 text-white border-blue-600'
                    : 'border-gray-300 text-gray-600 hover:bg-gray-50'
                }`}
              >
                {p}
              </button>
            ))}

            <button
              onClick={() => setPage(p => p + 1)}
              disabled={page >= totalPages}
              className="px-3 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              Next
            </button>
            <button
              onClick={() => setPage(totalPages)}
              disabled={page >= totalPages}
              className="px-2 py-1 text-xs rounded border border-gray-300 text-gray-600 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              »
            </button>
          </div>
        </div>
      )}

      {showAdd && <AddCandidateModal onClose={() => setShowAdd(false)} />}
      {selected && (
        <CandidateDetailModal candidate={selected} onClose={() => setSelected(null)} />
      )}
      {editing && (
        <CandidateOverrideModal candidate={editing} onClose={() => setEditing(null)} />
      )}
    </div>
  )
}

function PhotoLightbox({ url, name, onClose }: { url: string; name: string; onClose: () => void }) {
  return (
    <div
      className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
      onClick={onClose}
    >
      <div className="relative max-w-sm w-full mx-4" onClick={e => e.stopPropagation()}>
        <img src={url} alt={name} className="w-full rounded-2xl shadow-2xl object-contain max-h-[80vh]" />
        <button
          onClick={onClose}
          className="absolute -top-3 -right-3 w-8 h-8 bg-white rounded-full shadow-lg flex items-center justify-center text-gray-600 hover:text-gray-900 text-lg font-bold"
        >
          ×
        </button>
        <p className="text-center text-white text-sm font-medium mt-3">{name}</p>
      </div>
    </div>
  )
}

function CandidateRow({
  candidate: c,
  onView,
  onEdit,
  onDelete,
  isDeleting,
}: {
  candidate: Candidate
  onView: () => void
  onEdit: () => void
  onDelete: () => void
  isDeleting: boolean
}) {
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [showPhoto, setShowPhoto] = useState(false)
  const [showCallMenu, setShowCallMenu] = useState(false)
  const [callTriggered, setCallTriggered] = useState<'retry' | 'once' | null>(null)

  const triggerCall = useMutation({
    mutationFn: (retry: boolean) => triggerAICall(c.id, retry),
    onSuccess: (_, retry) => { setCallTriggered(retry ? 'retry' : 'once'); setShowCallMenu(false) },
  })

  const status = c.evaluation_status ?? ''
  const statusColor =
    status === 'Pass'   ? 'bg-green-100 text-green-700' :
    status === 'Fail'   ? 'bg-red-100 text-red-600' :
    status === 'HIRED'  ? 'bg-blue-100 text-blue-700' :
    status              ? 'bg-amber-100 text-amber-700' :
                          'bg-gray-100 text-gray-400'

  const displayLocation = c.current_location

  return (
    <tr
      className={`border-b border-gray-100 hover:bg-gray-50 cursor-pointer ${!c.is_active ? 'opacity-60' : ''}`}
      onClick={onView}
    >
      <td className="px-4 py-3">
        {showPhoto && c.photo_url && (
          <PhotoLightbox url={c.photo_url} name={c.name} onClose={() => setShowPhoto(false)} />
        )}
        <div className="flex items-center gap-2.5">
          <div
            className={`w-8 h-8 rounded-full shrink-0 overflow-hidden bg-blue-100 flex items-center justify-center ${c.photo_url ? 'cursor-pointer ring-1 ring-transparent hover:ring-blue-400 transition-all' : ''}`}
            onClick={e => { if (c.photo_url) { e.stopPropagation(); setShowPhoto(true) } }}
          >
            {c.photo_url
              ? <img src={c.photo_url} alt={c.name} className="w-full h-full object-cover" />
              : <span className="text-xs font-bold text-blue-600">
                  {c.name.split(' ').map((n: string) => n[0]).slice(0, 2).join('').toUpperCase()}
                </span>
            }
          </div>
          <div>
            <p className="font-medium text-gray-800">{c.name}</p>
            <p className="text-xs text-gray-400">{c.age_years ? `${c.age_years} yrs` : ''} {c.gender}</p>
            {c.interview_slot && (
              <div className="mt-0.5">
                <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-teal-100 text-teal-700 font-medium whitespace-nowrap">
                  Interview {new Date(c.interview_slot).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                  {c.interview_with ? ` · ${c.interview_with}` : ''}
                </span>
              </div>
            )}
          </div>
        </div>
      </td>
      <td className="px-4 py-3 text-gray-600">{c.phone}</td>
      <td className="px-4 py-3 text-gray-600">{c.category ?? '—'}</td>
      <td className="px-4 py-3 text-gray-600 max-w-[140px] truncate">{c.industry}</td>
      <td className="px-4 py-3 text-gray-600 max-w-[140px] truncate">{displayLocation}</td>
      <td className="px-4 py-3 text-gray-600">{c.experience ? `${c.experience} yr` : '—'}</td>
      <td className="px-4 py-3 text-gray-600 whitespace-nowrap">
        {c.current_salary != null ? `₹${c.current_salary.toLocaleString()}` : '—'}
      </td>
      <td className="px-4 py-3">
        {c.total_score != null ? (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${scoreBadge(c.total_score)}`}>
            {Number(c.total_score).toFixed(1)}
          </span>
        ) : '—'}
      </td>
      <td className="px-4 py-3">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusColor}`}>
          {c.evaluation_status ?? 'Not evaluated'}
        </span>
      </td>
      <td className="px-4 py-3">
        {c.religion === 'M' ? (
          <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-teal-100 text-teal-700">Muslim</span>
        ) : c.religion === 'N' ? (
          <span className="text-xs text-gray-400">Non-M</span>
        ) : (
          <span className="text-xs text-gray-300">—</span>
        )}
      </td>
      <td className="px-4 py-3">
        {c.dnd_until && new Date(c.dnd_until) > new Date() ? (
          <div>
            <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-amber-100 text-amber-700">
              DND
            </span>
            <p className="text-[10px] text-gray-400 mt-0.5">
              until {new Date(c.dnd_until).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', year: 'numeric' })}
            </p>
          </div>
        ) : (
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
            c.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'
          }`}>
            {c.is_active ? 'Active' : 'Inactive'}
          </span>
        )}
      </td>
      <td className="px-4 py-3 max-w-[180px]">
        {(c.active_mappings ?? []).length === 0 ? (
          <span className="text-xs text-gray-300">—</span>
        ) : (
          <div className="flex flex-col gap-1">
            {(c.active_mappings ?? []).map((m, i) => (
              <div key={i} className="flex items-center gap-1 min-w-0">
                <span className="text-[10px] text-gray-700 font-medium truncate max-w-[110px]">{m.company_name}</span>
                <span className={`shrink-0 text-[9px] px-1 py-px rounded font-medium ${
                  m.stage === 'placed'                 ? 'bg-green-100 text-green-700' :
                  m.stage === 'interview_done'          ? 'bg-amber-100 text-amber-700' :
                  m.stage === 'slot_booked'             ? 'bg-teal-100 text-teal-700' :
                  m.stage === 'invited_for_interview'   ? 'bg-indigo-100 text-indigo-700' :
                  m.stage === 'client_approval_pending' ? 'bg-violet-100 text-violet-700' :
                  m.stage === 'interested'              ? 'bg-blue-100 text-blue-700' :
                  'bg-gray-100 text-gray-500'
                }`}>
                  {m.stage.replace(/_/g, ' ')}
                </span>
              </div>
            ))}
          </div>
        )}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap gap-1 max-w-[140px]">
          {(c.labels ?? []).length === 0 ? (
            <span className="text-xs text-gray-300">—</span>
          ) : (c.labels ?? []).map(label => (
            <span
              key={label}
              className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium whitespace-nowrap ${
                label === 'Quick Joiner'       ? 'bg-green-100 text-green-700' :
                label === 'High Stability'     ? 'bg-blue-100 text-blue-700' :
                label === 'Average Stability'  ? 'bg-amber-100 text-amber-700' :
                label === 'High Turnover Risk' ? 'bg-red-100 text-red-600' :
                'bg-gray-100 text-gray-600'
              }`}
            >
              {label}
            </span>
          ))}
        </div>
      </td>
      <td className="px-4 py-3">
        <div className="flex gap-2 items-center flex-wrap">
          <button
            onClick={e => { e.stopPropagation(); onView() }}
            className="text-xs text-blue-500 hover:underline"
          >
            View
          </button>
          <button
            onClick={e => { e.stopPropagation(); onEdit() }}
            className="text-xs text-gray-400 hover:text-gray-600"
          >
            Edit
          </button>
          {/* AI Eval Call */}
          <div className="relative" onClick={e => e.stopPropagation()}>
            {callTriggered ? (
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-green-100 text-green-700 font-medium whitespace-nowrap">
                ✓ Call {callTriggered === 'retry' ? '(w/ retry)' : '(once)'}
              </span>
            ) : (
              <button
                onClick={() => setShowCallMenu(v => !v)}
                disabled={triggerCall.isPending}
                className="text-[10px] px-2 py-0.5 rounded-full border border-purple-200 bg-purple-50 text-purple-700 font-medium hover:bg-purple-100 disabled:opacity-50 whitespace-nowrap"
              >
                {triggerCall.isPending ? '…' : '🤖 AI Eval'}
              </button>
            )}
            {showCallMenu && (
              <div className="absolute left-0 mt-1 w-40 bg-white rounded-xl shadow-lg border border-gray-200 z-20 p-1" onMouseLeave={() => setShowCallMenu(false)}>
                <button
                  onClick={() => triggerCall.mutate(true)}
                  className="w-full text-left px-3 py-2 text-xs rounded-lg hover:bg-purple-50 text-gray-700"
                >
                  🔄 With retries
                </button>
                <button
                  onClick={() => triggerCall.mutate(false)}
                  className="w-full text-left px-3 py-2 text-xs rounded-lg hover:bg-gray-50 text-gray-700"
                >
                  1× Once only
                </button>
              </div>
            )}
          </div>
          {!confirmDelete ? (
            <button
              onClick={e => { e.stopPropagation(); setConfirmDelete(true) }}
              className="text-xs text-red-300 hover:text-red-500"
            >
              ×
            </button>
          ) : (
            <span className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
              <button
                onClick={() => { onDelete(); setConfirmDelete(false) }}
                disabled={isDeleting}
                className="text-xs text-red-600 hover:text-red-800 font-medium disabled:opacity-50"
              >
                {isDeleting ? '…' : 'Delete'}
              </button>
              <button
                onClick={() => setConfirmDelete(false)}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Cancel
              </button>
            </span>
          )}
        </div>
      </td>
    </tr>
  )
}
