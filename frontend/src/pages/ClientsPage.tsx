import { useState, useEffect } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { getClients, createClient, bulkScrape, importClients, deleteClient, importCaLeads, bulkAIVoiceReachout, bulkFunnelStuckReachout } from '../api/clients'
import { api } from '../api/axios'
import { sendClientBulkReachout } from '../api/whatsapp'
import type { Client, ClientStage } from '../types'
import { clientStageBadge, stageLabel } from '../types'
import ClientForm from '../components/ClientForm'
import TemplatePickerModal from '../components/TemplatePickerModal'

const ALL_STAGES: ClientStage[] = [
  'lead', 'scraping', 'scraped', 'reachout_sent',
  'interested', 'agreement_sent', 'onboarded', 'disqualified', 'churned', 'deal_won',
]

const SCRAPER_TYPES = ['job_portal', 'apollo', 'google_maps', 'custom'] as const

export default function ClientsPage() {
  const qc = useQueryClient()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const stageFilter = searchParams.get('stage') ?? ''
  const segmentFilter = searchParams.get('segment') ?? ''
  const recruiterFilter = searchParams.get('recruiter') === '1'
  const page = parseInt(searchParams.get('page') ?? '1', 10)
  const search = searchParams.get('search') ?? ''
  const [searchInput, setSearchInput] = useState(() => searchParams.get('search') ?? '')
  const [showForm, setShowForm] = useState(false)
  const [showCaModal, setShowCaModal] = useState(false)
  const [caCsv, setCaCsv] = useState('')
  const [caResult, setCaResult] = useState<{ inserted: number; skipped: number; errors: number; error_lines: { line: number; text: string; error: string }[] } | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [showScrapeMenu, setShowScrapeMenu] = useState(false)
  const [showBulkReachoutPicker, setShowBulkReachoutPicker] = useState(false)
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const [showBulkDeleteConfirm, setShowBulkDeleteConfirm] = useState(false)
  const [showAIReachoutConfirm, setShowAIReachoutConfirm] = useState(false)
  const [aiReachoutResult, setAIReachoutResult] = useState<{ client_id: string; call_id?: string; phone?: string; ok: boolean; error?: string }[] | null>(null)
  const [showFunnelStuckConfirm, setShowFunnelStuckConfirm] = useState(false)
  const [funnelStuckResult, setFunnelStuckResult] = useState<{ client_id: string; call_id?: string; phone?: string; ok: boolean; error?: string }[] | null>(null)
  const [showImportModal, setShowImportModal] = useState(false)
  const [importSheetUrl, setImportSheetUrl] = useState('')
  const [importResult, setImportResult] = useState<{ inserted: number; updated: number; skipped: number; errors: { row: number; company: string; error: string }[] } | null>(null)

  useEffect(() => {
    const t = setTimeout(() => {
      setSearchParams(p => {
        const n = new URLSearchParams(p)
        searchInput ? n.set('search', searchInput) : n.delete('search')
        n.delete('page')
        return n
      })
    }, 300)
    return () => clearTimeout(t)
  }, [searchInput])

  const updateParams = (updates: Record<string, string | null>, resetPage = true) => {
    setSearchParams(p => {
      const n = new URLSearchParams(p)
      for (const [k, v] of Object.entries(updates)) {
        v !== null ? n.set(k, v) : n.delete(k)
      }
      if (resetPage) n.delete('page')
      return n
    })
  }

  const PAGE_SIZE = 30
  const { data: clientsData, isLoading } = useQuery({
    queryKey: ['clients', stageFilter, search, segmentFilter, recruiterFilter, page],
    queryFn: () => getClients(stageFilter || undefined, search || undefined, segmentFilter || undefined, recruiterFilter ? true : undefined, page, PAGE_SIZE),
  })
  const clients = clientsData?.data ?? []
  const total = clientsData?.total ?? 0
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const create = useMutation({
    mutationFn: createClient,
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['clients'] }); setShowForm(false) },
  })

  const bulkScrapeAction = useMutation({
    mutationFn: (scraper_type: string) => bulkScrape([...selected], scraper_type),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['clients'] }); setSelected(new Set()); setShowScrapeMenu(false) },
  })

  const scrapeJobs = useMutation({
    mutationFn: () => api.post('/cron/scrape-jobs').then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clients'] }),
  })

  const enrichClients = useMutation({
    mutationFn: () => api.post('/cron/enrich-clients').then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['clients'] }),
  })

  const runImport = useMutation({
    mutationFn: (url: string) => importClients(url),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setImportResult(res)
    },
  })

  const runCaImport = useMutation({
    mutationFn: (csv: string) => importCaLeads(csv),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setCaResult(res)
    },
  })

  const bulkReachout = useMutation({
    mutationFn: (body: { template_name: string; lang: string; variables: string[]; header_url?: string; header_type?: string }) =>
      sendClientBulkReachout([...selected], body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setSelected(new Set())
      setShowBulkReachoutPicker(false)
    },
  })

  const deleteSingle = useMutation({
    mutationFn: (id: string) => deleteClient(id),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setDeleteConfirmId(null)
      setSelected(prev => { const next = new Set(prev); next.delete(id); return next })
    },
  })

  const bulkDelete = useMutation({
    mutationFn: (ids: string[]) => Promise.all(ids.map(id => deleteClient(id))),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setSelected(new Set())
      setShowBulkDeleteConfirm(false)
    },
  })

  const aiVoiceReachoutAction = useMutation({
    mutationFn: () => bulkAIVoiceReachout([...selected]),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['clients'] })
      setAIReachoutResult(res.data)
      setShowAIReachoutConfirm(false)
      setSelected(new Set())
    },
  })

  const funnelStuckReachoutAction = useMutation({
    mutationFn: () => bulkFunnelStuckReachout([...selected]),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['funnel-stuck-reachout'] })
      setFunnelStuckResult(res.data)
      setShowFunnelStuckConfirm(false)
      setSelected(new Set())
    },
  })

  const toggleSelect = (id: string, e: React.MouseEvent) => {
    e.stopPropagation()
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const toggleAll = () => {
    setSelected(prev => prev.size === clients.length ? new Set() : new Set(clients.map(c => c.id)))
  }

  const selectedClients = clients.filter(c => selected.has(c.id))
  const selectedWithPhone = selectedClients.filter(c =>
    (c.phone_numbers && c.phone_numbers.length > 0) || c.poc_phone
  )
  const selectedFunnelStuck = selectedWithPhone.filter(c =>
    c.stage === 'interested' || c.stage === 'agreement_sent'
  )
  const totalPhonesSelected = selectedClients.reduce((sum, c) => {
    const count = c.phone_numbers?.length || (c.poc_phone ? 1 : 0)
    return sum + count
  }, 0)

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-900">Clients</h1>
        <div className="flex items-center gap-2">
          <input
            className="w-56 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
            placeholder="Search company, POC, job…"
            value={searchInput}
            onChange={e => setSearchInput(e.target.value)}
          />
          <button
            onClick={() => scrapeJobs.mutate()}
            disabled={scrapeJobs.isPending}
            title="Scrape job portals and create new client leads"
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50 text-gray-700 disabled:opacity-50 flex items-center gap-1.5"
          >
            {scrapeJobs.isPending
              ? <span className="inline-block w-3.5 h-3.5 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
              : <span>🕷️</span>}
            {scrapeJobs.isPending ? 'Scraping…' : 'Scrape Jobs'}
          </button>
          <button
            onClick={() => enrichClients.mutate()}
            disabled={enrichClients.isPending}
            title="Fetch phone numbers for lead-stage clients"
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50 text-gray-700 disabled:opacity-50 flex items-center gap-1.5"
          >
            {enrichClients.isPending
              ? <span className="inline-block w-3.5 h-3.5 border-2 border-gray-500 border-t-transparent rounded-full animate-spin" />
              : <span>📞</span>}
            {enrichClients.isPending ? 'Enriching…' : 'Enrich Phones'}
          </button>
          <button
            onClick={() => { setShowImportModal(true); setImportResult(null) }}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50 text-gray-700"
          >
            Import Sheet
          </button>
          <button
            onClick={() => { setShowCaModal(true); setCaResult(null); setCaCsv('') }}
            className="px-4 py-2 text-sm rounded-lg border border-purple-300 hover:bg-purple-50 text-purple-700"
          >
            CA Leads
          </button>
          <button
            onClick={() => setShowForm(true)}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700"
          >
            + New Client
          </button>
        </div>
      </div>

      {/* Segment filters */}
      <div className="flex flex-wrap gap-2 mb-3">
        {[
          { value: '', label: 'All Segments' },
          { value: 'ca_network', label: 'CA Network' },
          { value: 'active_job_post', label: 'Job Post' },
          { value: 'employer_lead', label: 'Employer Lead' },
        ].map(({ value, label }) => (
          <button
            key={value}
            onClick={() => updateParams({ segment: segmentFilter === value ? null : value, recruiter: null })}
            className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors ${
              segmentFilter === value && !recruiterFilter
                ? value === 'ca_network'      ? 'bg-purple-600 text-white'
                  : value === 'active_job_post' ? 'bg-blue-600 text-white'
                  : value === 'employer_lead'   ? 'bg-amber-500 text-white'
                  : 'bg-gray-700 text-white'
                : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {label}
          </button>
        ))}
        <button
          onClick={() => updateParams({ recruiter: !recruiterFilter ? '1' : null, segment: null })}
          className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors ${
            recruiterFilter ? 'bg-orange-500 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          Recruiters
        </button>
      </div>

      {/* Stage filters */}
      <div className="flex flex-wrap gap-2 mb-6">
        <button
          onClick={() => updateParams({ stage: null })}
          className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors ${
            stageFilter === '' ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          All ({stageFilter === '' ? total : '…'})
        </button>
        {ALL_STAGES.map(s => (
          <button
            key={s}
            onClick={() => updateParams({ stage: stageFilter === s ? null : s })}
            className={`px-3 py-1.5 text-xs rounded-full font-medium transition-colors ${
              stageFilter === s ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            {stageLabel(s)}{stageFilter === s ? ` (${total})` : ''}
          </button>
        ))}
      </div>

      {/* Select all row */}
      {clients.length > 0 && (
        <div className="flex items-center gap-3 mb-3 px-1">
          <input
            type="checkbox"
            className="rounded"
            checked={selected.size === clients.length && clients.length > 0}
            onChange={toggleAll}
          />
          <span className="text-xs text-gray-500">
            {selected.size > 0 ? `${selected.size} selected` : 'Select all'}
          </span>
        </div>
      )}

      {isLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {[...Array(6)].map((_, i) => (
            <div key={i} className="h-28 rounded-xl bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : clients.length === 0 ? (
        <p className="text-sm text-gray-400">No clients found.</p>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {clients.map(c => (
            <SelectableClientCard
              key={c.id}
              client={c}
              selected={selected.has(c.id)}
              onToggle={e => toggleSelect(c.id, e)}
              onClick={() => navigate(`/clients/${c.id}`)}
              onDelete={e => { e.stopPropagation(); setDeleteConfirmId(c.id) }}
              deleteConfirming={deleteConfirmId === c.id}
              onDeleteConfirm={e => { e.stopPropagation(); deleteSingle.mutate(c.id) }}
              onDeleteCancel={e => { e.stopPropagation(); setDeleteConfirmId(null) }}
              deleteLoading={deleteSingle.isPending && deleteConfirmId === c.id}
            />
          ))}
        </div>
      )}

      {/* Pagination */}
      {!isLoading && total > PAGE_SIZE && (
        <div className="flex items-center justify-center gap-4 mt-6">
          <button
            onClick={() => setSearchParams(p => { const n = new URLSearchParams(p); n.set('page', String(page - 1)); return n })}
            disabled={page <= 1}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            ← Prev
          </button>
          <span className="text-sm text-gray-600">
            {(page - 1) * PAGE_SIZE + 1}–{Math.min(page * PAGE_SIZE, total)} of {total} clients
          </span>
          <button
            onClick={() => setSearchParams(p => { const n = new URLSearchParams(p); n.set('page', String(page + 1)); return n })}
            disabled={page >= totalPages}
            className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            Next →
          </button>
        </div>
      )}

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 flex items-center gap-3 bg-white rounded-2xl shadow-xl border border-gray-200 px-5 py-3 z-30">
          <span className="text-sm font-medium text-gray-700">{selected.size} selected</span>
          <div className="h-4 w-px bg-gray-200" />

          {/* Bulk scrape */}
          <div className="relative">
            <button
              onClick={() => setShowScrapeMenu(v => !v)}
              disabled={bulkScrapeAction.isPending}
              className="px-3 py-1.5 text-xs rounded-lg bg-gray-100 text-gray-700 hover:bg-gray-200 disabled:opacity-50"
            >
              {bulkScrapeAction.isPending ? 'Scraping…' : '🔍 Bulk Scrape ▾'}
            </button>
            {showScrapeMenu && (
              <div className="absolute bottom-full mb-2 left-0 bg-white rounded-xl shadow-lg border border-gray-200 p-1 min-w-[130px]">
                {SCRAPER_TYPES.map(t => (
                  <button
                    key={t}
                    onClick={() => bulkScrapeAction.mutate(t)}
                    className="w-full text-left px-3 py-1.5 text-xs rounded-lg hover:bg-gray-50 text-gray-700"
                  >
                    {t.replace(/_/g, ' ')}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Bulk reachout */}
          <button
            onClick={() => setShowBulkReachoutPicker(true)}
            disabled={selectedWithPhone.length === 0}
            title={selectedWithPhone.length === 0 ? 'No selected clients have phone numbers' : `${totalPhonesSelected} messages will be sent`}
            className="px-3 py-1.5 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            📲 Reachout ({selectedWithPhone.length} clients · {totalPhonesSelected} msgs)
          </button>

          {/* AI voice reachout */}
          <button
            onClick={() => setShowAIReachoutConfirm(true)}
            disabled={selectedWithPhone.length === 0 || aiVoiceReachoutAction.isPending}
            title={selectedWithPhone.length === 0 ? 'No selected clients have phone numbers' : `Schedule AI voice calls to ${selectedWithPhone.length} clients`}
            className="px-3 py-1.5 text-xs rounded-lg bg-teal-50 text-teal-700 hover:bg-teal-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {aiVoiceReachoutAction.isPending ? 'Scheduling…' : `📞 AI Voice Reachout (${selectedWithPhone.length})`}
          </button>

          {/* Funnel stuck AI voice reachout — only for interested / agreement_sent */}
          <button
            onClick={() => setShowFunnelStuckConfirm(true)}
            disabled={selectedFunnelStuck.length === 0 || funnelStuckReachoutAction.isPending}
            title={selectedFunnelStuck.length === 0 ? 'Select clients in Interested or Agreement Sent stage with a phone number' : `Schedule Funnel Stuck AI voice calls to ${selectedFunnelStuck.length} clients`}
            className="px-3 py-1.5 text-xs rounded-lg bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {funnelStuckReachoutAction.isPending ? 'Scheduling…' : `📞 Funnel Stuck Reachout (${selectedFunnelStuck.length})`}
          </button>

          <button
            onClick={() => setShowBulkDeleteConfirm(true)}
            disabled={bulkDelete.isPending}
            className="px-3 py-1.5 text-xs rounded-lg bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
          >
            🗑 Delete ({selected.size})
          </button>

          <button
            onClick={() => setSelected(new Set())}
            className="text-gray-400 hover:text-gray-600 text-lg leading-none"
          >
            ×
          </button>
        </div>
      )}

      {/* Bulk reachout template picker */}
      {showBulkReachoutPicker && (
        <TemplatePickerModal
          title={`Bulk Reachout — ${selectedWithPhone.length} clients`}
          loading={bulkReachout.isPending}
          onSend={(name, lang, variables, header) => bulkReachout.mutate({ template_name: name, lang, variables, header_url: header?.url, header_type: header?.type })}
          onClose={() => setShowBulkReachoutPicker(false)}
        />
      )}

      {/* Import from Google Sheets modal */}
      {showImportModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">Import from Google Sheets</h2>
              <button onClick={() => setShowImportModal(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            {!importResult ? (
              <>
                <p className="text-xs text-gray-500 mb-3">
                  Paste the Google Sheet URL (must be publicly accessible or shared as CSV).
                  Clients are upserted by Source Job Id — existing records will be updated.
                </p>
                <input
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-blue-400"
                  placeholder="https://docs.google.com/spreadsheets/d/…"
                  value={importSheetUrl}
                  onChange={e => setImportSheetUrl(e.target.value)}
                />
                {runImport.isError && (
                  <p className="text-xs text-red-500 mb-3">Import failed. Check the sheet URL and try again.</p>
                )}
                <div className="flex justify-end gap-3">
                  <button onClick={() => setShowImportModal(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
                  <button
                    onClick={() => runImport.mutate(importSheetUrl)}
                    disabled={runImport.isPending || !importSheetUrl.trim()}
                    className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
                  >
                    {runImport.isPending ? 'Importing…' : 'Import'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="space-y-3 mb-4">
                  <div className="flex items-center gap-3 p-3 bg-green-50 rounded-lg">
                    <span className="text-green-600 text-lg">✓</span>
                    <div>
                      <p className="text-sm font-medium text-green-800">Import complete</p>
                      <p className="text-xs text-green-600">{importResult.inserted} created · {importResult.updated} updated · {importResult.skipped} skipped</p>
                    </div>
                  </div>
                  {importResult.errors.length > 0 && (
                    <div className="p-3 bg-red-50 rounded-lg">
                      <p className="text-xs font-medium text-red-700 mb-1">{importResult.errors.length} rows had errors:</p>
                      <ul className="text-xs text-red-600 space-y-0.5 max-h-32 overflow-y-auto">
                        {importResult.errors.map((e, i) => <li key={i}>• Row {e.row} ({e.company}): {e.error}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
                <div className="flex justify-end">
                  <button onClick={() => setShowImportModal(false)} className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700">Done</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* AI voice reachout confirm */}
      {showAIReachoutConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
            <h2 className="text-base font-semibold mb-2">Schedule AI voice calls?</h2>
            <p className="text-sm text-gray-500 mb-6">
              This will place an outbound AI voice call to {selectedWithPhone.length} client{selectedWithPhone.length !== 1 ? 's' : ''} (POC's first phone number on file).
              We'll automatically follow up based on how each call goes.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowAIReachoutConfirm(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => aiVoiceReachoutAction.mutate()}
                disabled={aiVoiceReachoutAction.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-teal-600 text-white hover:bg-teal-700 disabled:opacity-50"
              >
                {aiVoiceReachoutAction.isPending ? 'Scheduling…' : 'Schedule calls'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* AI voice reachout result */}
      {aiReachoutResult && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">AI Voice Reachout</h2>
              <button onClick={() => setAIReachoutResult(null)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <div className="max-h-72 overflow-y-auto space-y-1 mb-4">
              {aiReachoutResult.map(r => (
                <div key={r.client_id} className={`text-xs px-2 py-1.5 rounded-lg ${r.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
                  {r.ok ? `✓ Call scheduled${r.phone ? ` to ${r.phone}` : ''}` : `✗ ${r.error || 'Failed'}`}
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <button onClick={() => setAIReachoutResult(null)} className="px-4 py-2 text-sm rounded-lg bg-gray-100 hover:bg-gray-200">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Funnel stuck AI voice reachout confirm */}
      {showFunnelStuckConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
            <h2 className="text-base font-semibold mb-2">Schedule Funnel Stuck AI voice calls?</h2>
            <p className="text-sm text-gray-500 mb-6">
              This will place an outbound AI voice call to {selectedFunnelStuck.length} client{selectedFunnelStuck.length !== 1 ? 's' : ''} (POC's first phone number on file)
              about completing the Job Requirement Form or Agreement Signing, depending on their stage.
            </p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowFunnelStuckConfirm(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => funnelStuckReachoutAction.mutate()}
                disabled={funnelStuckReachoutAction.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {funnelStuckReachoutAction.isPending ? 'Scheduling…' : 'Schedule calls'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Funnel stuck AI voice reachout result */}
      {funnelStuckResult && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">Funnel Stuck Reachout</h2>
              <button onClick={() => setFunnelStuckResult(null)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <div className="max-h-72 overflow-y-auto space-y-1 mb-4">
              {funnelStuckResult.map(r => (
                <div key={r.client_id} className={`text-xs px-2 py-1.5 rounded-lg ${r.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-600'}`}>
                  {r.ok ? `✓ Call scheduled${r.phone ? ` to ${r.phone}` : ''}` : `✗ ${r.error || 'Failed'}`}
                </div>
              ))}
            </div>
            <div className="flex justify-end">
              <button onClick={() => setFunnelStuckResult(null)} className="px-4 py-2 text-sm rounded-lg bg-gray-100 hover:bg-gray-200">Close</button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk delete confirm */}
      {showBulkDeleteConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
            <h2 className="text-base font-semibold mb-2">Delete {selected.size} client{selected.size !== 1 ? 's' : ''}?</h2>
            <p className="text-sm text-gray-500 mb-6">This will permanently delete the selected clients and all their mappings.</p>
            <div className="flex justify-end gap-3">
              <button onClick={() => setShowBulkDeleteConfirm(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => bulkDelete.mutate([...selected])}
                disabled={bulkDelete.isPending}
                className="px-4 py-2 text-sm rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >
                {bulkDelete.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* CA Leads import modal */}
      {showCaModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">Import CA Network Leads</h2>
              <button onClick={() => setShowCaModal(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            {!caResult ? (
              <>
                <p className="text-xs text-gray-500 mb-3">
                  Paste one lead per line in the format: <code className="bg-gray-100 px-1 rounded">source_id, poc_name, phone</code><br />
                  Leads are inserted as CA Network source, scraped stage, ready for reachout.
                </p>
                <textarea
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-4 focus:outline-none focus:ring-2 focus:ring-purple-400 font-mono h-48 resize-none"
                  placeholder={"CA001, Rajesh Shah, 9876543210\nCA002, Priya Mehta, 9123456789"}
                  value={caCsv}
                  onChange={e => setCaCsv(e.target.value)}
                />
                {runCaImport.isError && (
                  <p className="text-xs text-red-500 mb-3">Import failed. Please check your input and try again.</p>
                )}
                <div className="flex justify-end gap-3">
                  <button onClick={() => setShowCaModal(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
                  <button
                    onClick={() => runCaImport.mutate(caCsv)}
                    disabled={runCaImport.isPending || !caCsv.trim()}
                    className="px-4 py-2 text-sm rounded-lg bg-purple-600 text-white hover:bg-purple-700 disabled:opacity-50"
                  >
                    {runCaImport.isPending ? 'Importing…' : 'Import'}
                  </button>
                </div>
              </>
            ) : (
              <>
                <div className="space-y-3 mb-4">
                  <div className="flex items-center gap-3 p-3 bg-green-50 rounded-lg">
                    <span className="text-green-600 text-lg">✓</span>
                    <div>
                      <p className="text-sm font-medium text-green-800">Import complete</p>
                      <p className="text-xs text-green-600">
                        {caResult.inserted} inserted · {caResult.skipped} skipped · {caResult.errors} errors
                      </p>
                    </div>
                  </div>
                  {caResult.error_lines.length > 0 && (
                    <div className="p-3 bg-red-50 rounded-lg">
                      <p className="text-xs font-medium text-red-700 mb-1">{caResult.error_lines.length} lines had errors:</p>
                      <ul className="text-xs text-red-600 space-y-0.5 max-h-32 overflow-y-auto font-mono">
                        {caResult.error_lines.map((e, i) => (
                          <li key={i}>• Line {e.line}: {e.text} — {e.error}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </div>
                <div className="flex justify-end gap-3">
                  <button onClick={() => { setCaResult(null); setCaCsv('') }} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Import More</button>
                  <button onClick={() => setShowCaModal(false)} className="px-4 py-2 text-sm rounded-lg bg-purple-600 text-white hover:bg-purple-700">Done</button>
                </div>
              </>
            )}
          </div>
        </div>
      )}

      {/* New client form modal */}
      {showForm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">New Client</h2>
              <button onClick={() => setShowForm(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <ClientForm
              isCreate
              loading={create.isPending}
              onCancel={() => setShowForm(false)}
              onSubmit={data => create.mutate(data)}
            />
            {create.isError && <p className="mt-2 text-xs text-red-500">Failed to create client.</p>}
          </div>
        </div>
      )}
    </div>
  )
}

function SelectableClientCard({
  client, selected, onToggle, onClick,
  onDelete, deleteConfirming, onDeleteConfirm, onDeleteCancel, deleteLoading,
}: {
  client: Client
  selected: boolean
  onToggle: (e: React.MouseEvent) => void
  onClick: () => void
  onDelete: (e: React.MouseEvent) => void
  deleteConfirming: boolean
  onDeleteConfirm: (e: React.MouseEvent) => void
  onDeleteCancel: (e: React.MouseEvent) => void
  deleteLoading: boolean
}) {
  return (
    <div
      className={`bg-white rounded-xl border p-4 cursor-pointer transition-all hover:shadow-md group ${
        selected ? 'border-blue-400 ring-2 ring-blue-100' : 'border-gray-200'
      }`}
      onClick={onClick}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2 min-w-0">
          <div onClick={onToggle} className="mt-0.5 shrink-0">
            <input
              type="checkbox"
              checked={selected}
              readOnly
              className="rounded pointer-events-none"
            />
          </div>
          <div className="min-w-0">
            <p className="font-semibold text-gray-900 truncate">{client.company_name}</p>
            <p className="text-sm text-gray-500 truncate">{client.job_title}</p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {deleteConfirming ? (
            <div className="flex items-center gap-1" onClick={e => e.stopPropagation()}>
              <button
                onClick={onDeleteCancel}
                className="text-xs px-2 py-0.5 rounded border border-gray-300 hover:bg-gray-50 text-gray-600"
              >Cancel</button>
              <button
                onClick={onDeleteConfirm}
                disabled={deleteLoading}
                className="text-xs px-2 py-0.5 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
              >{deleteLoading ? '…' : 'Delete'}</button>
            </div>
          ) : (
            <button
              onClick={onDelete}
              className="opacity-0 group-hover:opacity-100 transition-opacity p-1 rounded hover:bg-red-50 text-gray-400 hover:text-red-500"
              title="Delete client"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
              </svg>
            </button>
          )}
          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${clientStageBadge(client.stage)}`}>
            {stageLabel(client.stage)}
          </span>
        </div>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
        {client.industry && <span>{client.industry}</span>}
        {client.location && <span>{client.location}</span>}
        {client.poc_phone
          ? <span className="text-green-600">📞 {client.poc_phone}</span>
          : <span className="text-amber-500">No phone</span>}
      </div>
      {(client.segment || client.is_dnd || (client.labels ?? []).length > 0) && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {client.segment && (
            <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              client.segment === 'ca_network'      ? 'bg-purple-50 text-purple-700' :
              client.segment === 'active_job_post'  ? 'bg-blue-50 text-blue-700' :
              'bg-amber-50 text-amber-700'
            }`}>
              {client.segment === 'ca_network' ? 'CA Network' :
               client.segment === 'active_job_post' ? 'Job Post' : 'Employer Lead'}
            </span>
          )}
          {client.is_recruiter && (
            <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-orange-50 text-orange-600">Recruiter</span>
          )}
          {client.is_dnd && (
            <span className="text-[10px] px-2 py-0.5 rounded-full font-medium bg-red-50 text-red-600">🚫 DND</span>
          )}
          {(client.labels ?? []).map(label => (
            <span key={label} className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
              label === 'Sourcing Needed' ? 'bg-red-100 text-red-600' :
              label === 'Priority'        ? 'bg-orange-100 text-orange-700' :
              label === 'High Value'      ? 'bg-green-100 text-green-700' :
              label === 'On Hold'         ? 'bg-gray-100 text-gray-600' :
              label === 'VIP'             ? 'bg-purple-100 text-purple-700' :
              'bg-blue-100 text-blue-700'
            }`}>
              {label}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}
