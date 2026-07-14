import { useState } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'
import { getClient, updateClient, deleteClient, updateClientStage, getClientTimeline, getClientAICalls, getLockedCandidates, agreePhone, sendAgreement } from '../api/clients'
import { getMappings, createMapping, autoMatchCandidates, deleteMapping, updateMappingStage, getPoolSummary, getFilterPreview } from '../api/mappings'
import type { FilterPreviewCandidate } from '../api/mappings'
import { sendClientReachout, sendClientAvailability } from '../api/whatsapp'
import { sendBatch, rerunBatch } from '../api/outreach'
import { getCandidates } from '../api/candidates'
import type { ClientStage, Candidate } from '../types'
import { clientStageBadge, stageLabel, scoreBadge, pipelineProgress, aiCallStatusBadge, aiCallClassificationBadge, aiCallTypeBadge, aiCallTypeLabel } from '../types'
import ClientForm from '../components/ClientForm'
import ScrapeButtons from '../components/ScrapeButtons'
import StageTimeline from '../components/StageTimeline'
import MappedCandidateRow from '../components/MappedCandidateRow'
import BulkWAModal from '../components/BulkWAModal'
import TemplatePickerModal from '../components/TemplatePickerModal'
import AgreementModal from '../components/AgreementModal'
import ScoreBadgeWithTooltip from '../components/ScoreBadgeWithTooltip'
import AddSlotModal from '../components/AddSlotModal'
import ClientComments from '../components/ClientComments'

const CLIENT_STAGES: ClientStage[] = [
  'lead', 'scraping', 'scraped', 'reachout_sent',
  'interested', 'agreement_sent', 'onboarded', 'disqualified', 'churned',
]

export default function ClientDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const qc = useQueryClient()

  const [activeTab, setActiveTab] = useState<'overview' | 'candidates' | 'timeline' | 'comments'>('overview')
  const [editMode, setEditMode] = useState(false)
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false)
  const [showChurnConfirm, setShowChurnConfirm] = useState(false)
  const [showBulkWA, setShowBulkWA] = useState(false)
  const [showAgreementModal, setShowAgreementModal] = useState(false)
  const [selectedMappings, setSelectedMappings] = useState<Set<string>>(new Set())
  const [showStageMenu, setShowStageMenu] = useState(false)
  const [showSegmentMenu, setShowSegmentMenu] = useState(false)
  const [showAddCandidate, setShowAddCandidate] = useState(false)
  const [showReachoutPicker, setShowReachoutPicker] = useState(false)
  const [candidateSearch, setCandidateSearch] = useState('')
  const [linkCopied, setLinkCopied] = useState(false)
  const [showAddPhone, setShowAddPhone] = useState(false)
  const [newPhoneInput, setNewPhoneInput] = useState('')
  const [editingPocPhone, setEditingPocPhone] = useState(false)
  const [pocPhoneInput, setPocPhoneInput] = useState('')
  const [showLabelMenu, setShowLabelMenu] = useState(false)
  const [batchResult, setBatchResult] = useState<string | null>(null)
  const [showAllScores, setShowAllScores] = useState(false)
  const [statusFilter, setStatusFilter] = useState('all')
  const [reasonFilter, setReasonFilter] = useState('all')
  const [progressFilter, setProgressFilter] = useState('all')
  const [showFilterPool, setShowFilterPool] = useState(false)
  const [showWaitlisted, setShowWaitlisted] = useState(false)
  const [showAddSlot, setShowAddSlot] = useState(false)
  const [bookingMappingId, setBookingMappingId] = useState<string | null>(null)
  const [selectedBookingSlot, setSelectedBookingSlot] = useState<{ label: string; iso: string } | null>(null)
  const [expandedCalls, setExpandedCalls] = useState<Set<string>>(new Set())
  const [showAutoMatchModal, setShowAutoMatchModal] = useState(false)
  const [autoMatchIncludeMuslim, setAutoMatchIncludeMuslim] = useState(false)

  const { data: client, isLoading } = useQuery({
    queryKey: ['client', id],
    queryFn: () => getClient(id!),
    enabled: !!id,
  })

  const { data: timeline = [] } = useQuery({
    queryKey: ['timeline', id],
    queryFn: () => getClientTimeline(id!),
    enabled: !!id && activeTab === 'timeline',
  })

  const { data: aiCalls = [] } = useQuery({
    queryKey: ['ai-calls', id],
    queryFn: () => getClientAICalls(id!),
    enabled: !!id && activeTab === 'overview',
  })

  const { data: lockedCandidates = [] } = useQuery({
    queryKey: ['locked-candidates', id],
    queryFn: () => getLockedCandidates(id!),
    enabled: !!id && showChurnConfirm,
  })

  const { data: mappings = [], isLoading: mappingsLoading } = useQuery({
    queryKey: ['mappings', id],
    queryFn: () => getMappings(id!),
    enabled: !!id && activeTab === 'candidates',
  })

  const { data: pool } = useQuery({
    queryKey: ['pool', id],
    queryFn: () => getPoolSummary(id!),
    enabled: !!id && activeTab === 'candidates',
  })

  const { data: filterPool, isFetching: filterPoolLoading } = useQuery({
    queryKey: ['filter-preview', id],
    queryFn: () => getFilterPreview(id!),
    enabled: !!id && activeTab === 'candidates' && showFilterPool,
    staleTime: 60_000,
  })

  // Candidates for manual add
  const { data: candidateResult } = useQuery({
    queryKey: ['candidates', candidateSearch, '', ''],
    queryFn: () => getCandidates({ search: candidateSearch || undefined }),
    enabled: showAddCandidate,
  })
  const allCandidates = candidateResult?.candidates ?? []

  const update = useMutation({
    mutationFn: (body: Parameters<typeof updateClient>[1]) => updateClient(id!, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['client', id] })
      qc.invalidateQueries({ queryKey: ['clients'] })
      setEditMode(false)
    },
  })

  const remove = useMutation({
    mutationFn: () => deleteClient(id!),
    onSuccess: () => navigate('/clients'),
  })

  const [bulkRemoving, setBulkRemoving] = useState(false)
  const bulkRemoveMappings = async () => {
    if (!selectedMappings.size) return
    setBulkRemoving(true)
    try {
      await Promise.all([...selectedMappings].map(mid => deleteMapping(id!, mid)))
      setSelectedMappings(new Set())
      qc.invalidateQueries({ queryKey: ['mappings', id] })
    } finally {
      setBulkRemoving(false)
    }
  }


  const [sendingClientReview, setSendingClientReview] = useState(false)
  const [clientReviewResult, setClientReviewResult] = useState<string | null>(null)
  const sendToClientReview = async () => {
    const eligible = mappings.filter(
      (m: any) => m.stage === 'interested' && (m.match_score ?? 0) >= 70
    )
    if (!eligible.length) {
      setClientReviewResult('No interested candidates with score ≥ 70 found')
      setTimeout(() => setClientReviewResult(null), 3000)
      return
    }
    setSendingClientReview(true)
    setClientReviewResult(null)
    try {
      await Promise.all(
        eligible.map((m: any) =>
          updateMappingStage(id!, m.id, 'client_approval_pending')
        )
      )
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      setClientReviewResult(`${eligible.length} candidate${eligible.length > 1 ? 's' : ''} sent for client review`)
    } catch {
      setClientReviewResult('Failed — check backend logs')
    } finally {
      setSendingClientReview(false)
      setTimeout(() => setClientReviewResult(null), 4000)
    }
  }

  const changeStage = useMutation({
    mutationFn: ({ stage }: { stage: ClientStage }) => updateClientStage(id!, stage),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['client', id] })
      qc.invalidateQueries({ queryKey: ['clients'] })
      qc.invalidateQueries({ queryKey: ['timeline', id] })
      setShowStageMenu(false)
    },
  })

  const patchClient = useMutation({
    mutationFn: (body: Record<string, unknown>) => updateClient(id!, body as any),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['client', id] })
      qc.invalidateQueries({ queryKey: ['clients'] })
      setShowSegmentMenu(false)
    },
  })

  const bookInterview = useMutation({
    mutationFn: ({ mappingId, slotIso, candidateId }: { mappingId: string; slotIso: string; candidateId: string }) =>
      api.post(`/matching/${id}/candidate/${candidateId}/book-slot`, null, { params: { slot_iso: slotIso, mapping_id: mappingId } }).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      setBookingMappingId(null)
      setSelectedBookingSlot(null)
    },
  })

  const reachout = useMutation({
    mutationFn: (body: { template_name: string; lang: string; variables: string[]; header_url?: string; header_type?: string }) =>
      sendClientReachout(id!, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['client', id] })
      setShowReachoutPicker(false)
    },
  })

  const markPhoneAgreed = useMutation({
    mutationFn: (phone: string) => agreePhone(id!, phone),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['client', id] }),
  })

  const askAvailability = useMutation({
    mutationFn: () => sendClientAvailability(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['client', id] }),
  })

  const sendAgreementMsg = useMutation({
    mutationFn: () => sendAgreement(id!),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['client', id] }),
  })

  const autoMatch = useMutation({
    mutationFn: (includeMuslim: boolean) => autoMatchCandidates(id!, { include_muslim: includeMuslim }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mappings', id] }),
  })

  const runBatch = useMutation({
    mutationFn: () => sendBatch(id!),
    onSuccess: (data) => {
      setBatchResult(data.message ?? `Sent to ${data.sent} candidates`)
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      qc.invalidateQueries({ queryKey: ['pool', id] })
      qc.invalidateQueries({ queryKey: ['client', id] })
      qc.invalidateQueries({ queryKey: ['candidates'] })
    },
  })

  const rerun = useMutation({
    mutationFn: () => rerunBatch(id!),
    onSuccess: (data) => {
      setBatchResult(data.message ?? `Re-run: ${data.created} new candidates added`)
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      qc.invalidateQueries({ queryKey: ['pool', id] })
      qc.invalidateQueries({ queryKey: ['client', id] })
    },
  })

  const toggleClientLabel = (label: string) => {
    const current = client?.labels ?? []
    const next = current.includes(label)
      ? current.filter(l => l !== label)
      : [...current, label]
    patchClient.mutate({ labels: next })
  }

  const [lockedWarning, setLockedWarning] = useState<string | null>(null)

  const addManual = useMutation({
    mutationFn: (candidateId: string) => createMapping(id!, { candidate_id: candidateId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      setShowAddCandidate(false)
    },
  })

  const addFromPool = useMutation({
    mutationFn: (candidate: FilterPreviewCandidate) =>
      createMapping(id!, { candidate_id: candidate.id, match_score: candidate.mms_score }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings', id] })
      qc.invalidateQueries({ queryKey: ['filter-preview', id] })
    },
  })

  const toggleMapping = (mappingId: string) => {
    setSelectedMappings(prev => {
      const next = new Set(prev)
      next.has(mappingId) ? next.delete(mappingId) : next.add(mappingId)
      return next
    })
  }

  const mappedCandidateIds = new Set(mappings.map(m => String(m.candidate_id)))

  if (isLoading) return <div className="p-6"><div className="h-32 animate-pulse bg-gray-100 rounded-xl" /></div>
  if (!client) return <div className="p-6 text-gray-400">Client not found.</div>

  return (
    <div className="p-6 max-w-5xl">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 mb-6">
        <div>
          <button onClick={() => navigate('/clients')} className="text-xs text-gray-400 hover:text-gray-600 mb-1">
            ← Clients
          </button>
          <h1 className="text-2xl font-bold text-gray-900">{client.company_name}</h1>
          <p className="text-sm text-gray-500">{client.job_title} · {client.industry}</p>
          {(client.labels ?? []).length > 0 && (
            <div className="flex flex-wrap gap-1 mt-1.5">
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
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {/* DND badge */}
          {client.is_dnd && (
            <span className="text-xs px-2.5 py-1 rounded-full font-medium bg-red-100 text-red-600 border border-red-200">
              🚫 DND
            </span>
          )}

          {/* Stage badge */}
          <span className={`text-xs px-3 py-1 rounded-full font-medium ${clientStageBadge(client.stage)}`}>
            {stageLabel(client.stage)}
          </span>

          {/* Segment badge + picker */}
          <div className="relative">
            <button
              onClick={() => setShowSegmentMenu(v => !v)}
              className={`px-2.5 py-1 text-xs rounded-full font-medium border transition-colors ${
                client.segment === 'ca_network'     ? 'bg-purple-50 text-purple-700 border-purple-200' :
                client.segment === 'active_job_post' ? 'bg-blue-50 text-blue-700 border-blue-200' :
                client.segment === 'employer_lead'   ? 'bg-amber-50 text-amber-700 border-amber-200' :
                'bg-gray-100 text-gray-500 border-gray-200'
              }`}
            >
              {client.segment === 'ca_network'     ? 'CA Network' :
               client.segment === 'active_job_post' ? 'Job Post' :
               client.segment === 'employer_lead'   ? 'Employer Lead' :
               '+ Segment'} ▾
            </button>
            {showSegmentMenu && (
              <div className="absolute right-0 mt-1 w-44 bg-white rounded-xl shadow-lg border border-gray-200 z-20 p-1">
                {[
                  { value: 'ca_network',     label: 'CA Network' },
                  { value: 'active_job_post', label: 'Active Job Post' },
                  { value: 'employer_lead',   label: 'Employer Lead' },
                  { value: null,              label: 'Untagged' },
                ].map(opt => (
                  <button
                    key={String(opt.value)}
                    onClick={() => patchClient.mutate({ segment: opt.value })}
                    disabled={client.segment === opt.value || patchClient.isPending}
                    className={`w-full text-left px-3 py-1.5 text-xs rounded-lg hover:bg-gray-50 disabled:text-gray-300 ${
                      client.segment === opt.value ? 'font-medium text-blue-600' : 'text-gray-700'
                    }`}
                  >
                    {opt.label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Client labels */}
          <div className="relative">
            <button
              onClick={() => setShowLabelMenu(v => !v)}
              className="px-2.5 py-1 text-xs rounded-full font-medium border border-gray-200 bg-gray-50 text-gray-600 hover:bg-gray-100"
            >
              {(client.labels ?? []).length > 0 ? `${client.labels!.length} label${client.labels!.length > 1 ? 's' : ''}` : '+ Labels'} ▾
            </button>
            {showLabelMenu && (
              <div className="absolute right-0 mt-1 w-52 bg-white rounded-xl shadow-lg border border-gray-200 z-20 p-2">
                <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-wider px-1 mb-1.5">Client Labels</p>
                {(['Sourcing Needed', 'Priority', 'High Value', 'On Hold', 'Needs Follow Up', 'VIP'] as const).map(label => (
                  <button
                    key={label}
                    onClick={() => toggleClientLabel(label)}
                    className={`w-full flex items-center gap-2 px-2 py-1.5 text-xs rounded-lg hover:bg-gray-50 ${
                      (client.labels ?? []).includes(label) ? 'text-blue-600 font-medium' : 'text-gray-700'
                    }`}
                  >
                    <span className={`w-3 h-3 rounded border flex-shrink-0 flex items-center justify-center ${
                      (client.labels ?? []).includes(label) ? 'bg-blue-600 border-blue-600 text-white' : 'border-gray-300'
                    }`}>
                      {(client.labels ?? []).includes(label) && <span className="text-[8px]">✓</span>}
                    </span>
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* DND toggle */}
          <button
            onClick={() => patchClient.mutate({ is_dnd: !client.is_dnd })}
            disabled={patchClient.isPending}
            className={`px-2.5 py-1 text-xs rounded-lg border transition-colors disabled:opacity-40 ${
              client.is_dnd
                ? 'bg-red-50 text-red-600 border-red-200 hover:bg-red-100'
                : 'border-gray-300 text-gray-500 hover:bg-gray-50'
            }`}
          >
            {client.is_dnd ? 'Remove DND' : 'Mark DND'}
          </button>

          {/* Bot toggle */}
          <button
            onClick={() => patchClient.mutate({ is_bot: !client.is_bot })}
            disabled={patchClient.isPending}
            title={client.is_bot ? 'Marked as bot — click to unmark' : 'Mark this client as a bot to stop all automated replies'}
            className={`px-2.5 py-1 text-xs rounded-lg border transition-colors disabled:opacity-40 ${
              client.is_bot
                ? 'bg-yellow-50 text-yellow-700 border-yellow-300 hover:bg-yellow-100'
                : 'border-gray-300 text-gray-500 hover:bg-gray-50'
            }`}
          >
            {client.is_bot ? '🤖 Unmark Bot' : '🤖 Mark Bot'}
          </button>

          {/* Stop Sourcing toggle */}
          <button
            onClick={() => patchClient.mutate({ stop_sourcing: !client.stop_sourcing })}
            disabled={patchClient.isPending}
            title={client.stop_sourcing ? 'Sourcing paused — click to resume' : 'Click to stop adding new candidates'}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-lg border transition-colors disabled:opacity-40 ${
              client.stop_sourcing
                ? 'bg-orange-50 text-orange-700 border-orange-300 hover:bg-orange-100'
                : 'border-gray-300 text-gray-500 hover:bg-gray-50'
            }`}
          >
            <span className={`w-6 h-3.5 rounded-full transition-colors flex items-center px-0.5 ${client.stop_sourcing ? 'bg-orange-400' : 'bg-gray-300'}`}>
              <span className={`w-2.5 h-2.5 bg-white rounded-full shadow transition-transform ${client.stop_sourcing ? 'translate-x-2.5' : 'translate-x-0'}`} />
            </span>
            {client.stop_sourcing ? 'Sourcing Paused' : 'Sourcing On'}
          </button>

          {/* Mark as churned */}
          {client.stage !== 'churned' && (
            <button
              onClick={() => setShowChurnConfirm(true)}
              className="px-2.5 py-1 text-xs rounded-lg border border-gray-300 text-gray-500 hover:bg-gray-50"
            >
              Mark Churned
            </button>
          )}

          <div className="relative">
            <button
              onClick={() => setShowStageMenu(v => !v)}
              className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50"
            >
              Change Stage
            </button>
            {showStageMenu && (
              <div className="absolute right-0 mt-1 w-44 bg-white rounded-xl shadow-lg border border-gray-200 z-20 p-1">
                {CLIENT_STAGES.map(s => (
                  <button
                    key={s}
                    onClick={() => changeStage.mutate({ stage: s })}
                    disabled={s === client.stage || changeStage.isPending}
                    className={`w-full text-left px-3 py-1.5 text-xs rounded-lg hover:bg-gray-50 disabled:text-gray-300 ${
                      s === client.stage ? 'font-medium text-blue-600' : 'text-gray-700'
                    }`}
                  >
                    {stageLabel(s)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <button onClick={() => setEditMode(true)} className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50">Edit</button>
          <button onClick={() => setShowDeleteConfirm(true)} className="px-3 py-1.5 text-xs rounded-lg border border-red-200 text-red-500 hover:bg-red-50">Delete</button>
        </div>
      </div>

      {/* WA Action buttons */}
      <div className="flex flex-wrap gap-2 mb-6">
        <button
          onClick={() => setShowReachoutPicker(true)}
          className="px-3 py-1.5 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100"
        >
          📲 Send Reachout
        </button>
        <button
          onClick={() => setShowAgreementModal(true)}
          className="px-3 py-1.5 text-xs rounded-lg bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
        >
          📝 Generate Agreement
        </button>
        <button
          onClick={() => sendAgreementMsg.mutate()}
          disabled={sendAgreementMsg.isPending}
          className="px-3 py-1.5 text-xs rounded-lg bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
        >
          {sendAgreementMsg.isPending ? 'Sending…' : '📤 Send Agreement'}
        </button>
        {sendAgreementMsg.isError && (
          <span className="text-xs text-red-500 self-center">
            {(sendAgreementMsg.error as any)?.response?.data?.detail || 'Failed to send agreement'}
          </span>
        )}
        {sendAgreementMsg.isSuccess && (
          <span className="text-xs text-green-600 self-center">Agreement sent ✓</span>
        )}
        {client.stage === 'onboarded' && (
          <button
            onClick={() => askAvailability.mutate()}
            disabled={askAvailability.isPending}
            className="px-3 py-1.5 text-xs rounded-lg bg-teal-50 text-teal-700 hover:bg-teal-100 disabled:opacity-50"
          >
            {askAvailability.isPending ? 'Sending…' : '📅 Ask Availability'}
          </button>
        )}
        {askAvailability.isError && (
          <span className="text-xs text-red-500 self-center">Failed — set CLIENT_AVAILABILITY_TEMPLATE env var</span>
        )}
      </div>


      {/* Tabs */}
      <div className="flex gap-1 border-b border-gray-200 mb-6">
        {(['overview', 'candidates', 'timeline', 'comments'] as const).map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab ? 'border-b-2 border-blue-600 text-blue-600' : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
            {tab === 'candidates' && mappings.length > 0 && (
              <span className="ml-1.5 text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">
                {mappings.length}
              </span>
            )}
          </button>
        ))}
      </div>

      {/* Overview */}
      {activeTab === 'overview' && (
        <div className="space-y-6">
          <div className="grid grid-cols-2 gap-4 text-sm">
            <InfoRow label="Location" value={client.location} />
            <InfoRow label="Job Location" value={client.job_location} />
            <InfoRow label="POC" value={client.poc_name ?? null} />
            <div>
              <p className="text-xs text-gray-400">POC Phone</p>
              {editingPocPhone ? (
                <div className="flex items-center gap-2 mt-0.5">
                  <input
                    autoFocus
                    type="tel"
                    maxLength={10}
                    value={pocPhoneInput}
                    onChange={e => setPocPhoneInput(e.target.value.replace(/\D/g, ''))}
                    className="w-36 border border-gray-300 rounded-lg px-2 py-1 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 font-mono"
                  />
                  <button
                    onClick={() => {
                      if (pocPhoneInput.length < 10) return
                      patchClient.mutate({ poc_phone: pocPhoneInput })
                      setEditingPocPhone(false)
                    }}
                    disabled={pocPhoneInput.length < 10 || patchClient.isPending}
                    className="text-xs px-2.5 py-1 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                  >
                    Save
                  </button>
                  <button
                    onClick={() => setEditingPocPhone(false)}
                    className="text-xs text-gray-400 hover:text-gray-600"
                  >
                    Cancel
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-2 mt-0.5">
                  <p className="text-sm text-gray-800">{client.poc_phone ?? '—'}</p>
                  <button
                    onClick={() => { setPocPhoneInput(client.poc_phone ?? ''); setEditingPocPhone(true) }}
                    className="text-blue-500 hover:text-blue-700"
                    title="Edit phone"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" className="w-3.5 h-3.5" viewBox="0 0 20 20" fill="currentColor">
                      <path d="M13.586 3.586a2 2 0 112.828 2.828l-.793.793-2.828-2.828.793-.793zM11.379 5.793L3 14.172V17h2.828l8.38-8.379-2.83-2.828z" />
                    </svg>
                  </button>
                </div>
              )}
            </div>
            <InfoRow label="Industry" value={client.industry} />
            <InfoRow label="Business Type" value={client.business_type} />
            {client.office_type && (
              <div>
                <p className="text-xs text-gray-400">Office Type</p>
                <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full mt-0.5 ${
                  client.office_type === 'Corporate' ? 'bg-blue-100 text-blue-700' :
                  client.office_type === 'Industrial' ? 'bg-orange-100 text-orange-700' :
                  'bg-gray-100 text-gray-600'
                }`}>{client.office_type}</span>
              </div>
            )}
            <div>
              <p className="text-xs text-gray-400">CA Firm</p>
              <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full mt-0.5 ${
                client.is_ca_firm ? 'bg-purple-100 text-purple-700' : 'bg-gray-100 text-gray-600'
              }`}>{client.is_ca_firm ? 'Yes' : 'No'}</span>
            </div>
            <InfoRow label="Job Type" value={client.job_type} />
            <InfoRow label="Employees" value={client.num_employees?.toString()} />
            {client.female_employee_pct != null && (
              <InfoRow label="Female Employees" value={`${client.female_employee_pct}%`} />
            )}
            {client.diwali_bonus && (
              <div>
                <p className="text-xs text-gray-400">Diwali Bonus</p>
                <span className={`inline-block text-xs font-medium px-2 py-0.5 rounded-full mt-0.5 ${
                  client.diwali_bonus === 'yes' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'
                }`}>
                  {client.diwali_bonus === 'yes' ? 'Yes' : 'No'}
                </span>
              </div>
            )}
            <InfoRow label="Salary Range" value={client.min_salary != null ? `₹${client.min_salary?.toLocaleString()} – ₹${client.max_salary?.toLocaleString()}` : null} />
            <InfoRow label="Salary Deductions" value={client.salary_deductions} />
            <InfoRow label="Job Timings" value={client.job_timings} />
            <InfoRow label="Candidate Travel Radius" value={(client as any).travel_radius != null ? `${(client as any).travel_radius} km` : null} />
            <InfoRow label="Gender" value={client.gender_requirements} />
            {client.gst_tds && (
              <InfoRow
                label="GST / TDS"
                value={[
                  client.gst_tds.gst ? `GST: ${client.gst_tds.gst}` : '',
                  client.gst_tds.tds ? `TDS: ${client.gst_tds.tds}` : '',
                ].filter(Boolean).join(' · ')}
              />
            )}
            <InfoRow label="Source" value={client.source} />
            {client.segment && (
              <InfoRow
                label="Segment"
                value={
                  client.segment === 'ca_network'     ? 'CA Network' :
                  client.segment === 'active_job_post' ? 'Active Job Post' :
                  client.segment === 'employer_lead'   ? 'Employer Lead' : client.segment
                }
              />
            )}
            {client.company_website && <InfoRow label="Website" value={client.company_website} />}
            {client.age_requirements && <InfoRow label="Age Requirements" value={client.age_requirements} />}
            {client.open_positions != null && <InfoRow label="Open Positions" value={client.open_positions.toString()} />}
            {client.fee_amount != null && <InfoRow label="Fee per Hire" value={`₹${client.fee_amount?.toLocaleString()}`} />}
            {client.payment_terms && <InfoRow label="Payment Terms" value={client.payment_terms} />}
            {client.replacement_period && <InfoRow label="Replacement Period" value={client.replacement_period} />}
          </div>

          {client.other_details && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-1">Other Details</p>
              <p className="text-sm text-gray-700 whitespace-pre-wrap">{client.other_details}</p>
            </div>
          )}

          {client.tools_requirements && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2">Accounting Software</p>
              <div className="flex flex-wrap gap-1">
                {client.tools_requirements.split(',').map(t => t.trim()).filter(Boolean).map(t => (
                  <span key={t} className="px-2 py-0.5 text-xs bg-purple-50 text-purple-700 rounded-full">{t}</span>
                ))}
              </div>
            </div>
          )}

          {/* Phone numbers */}
          <div>
            <p className="text-xs font-medium text-gray-500 mb-2">Phone Numbers</p>
            {client.phone_numbers && client.phone_numbers.length > 0 ? (
              <div className="space-y-1.5">
                {client.phone_numbers.map((p, i) => (
                  <div key={i} className="flex items-center gap-2">
                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                      p.source === 'Google'    ? 'bg-blue-50 text-blue-700' :
                      p.source === 'Apollo'    ? 'bg-purple-50 text-purple-700' :
                      p.source === 'Website'   ? 'bg-teal-50 text-teal-700' :
                      p.source === 'IndiaMart' ? 'bg-orange-50 text-orange-700' :
                      p.source === 'Manual'    ? 'bg-gray-100 text-gray-700' :
                      'bg-gray-100 text-gray-600'
                    }`}>{p.source}</span>
                    <span className="text-sm text-gray-800 font-mono">{p.number}</span>
                    {p.agreed || client.agreed_phone === p.number ? (
                      <span className="text-xs text-green-600 font-medium">✓ Agreed</span>
                    ) : (
                      <button
                        onClick={() => markPhoneAgreed.mutate(p.number)}
                        disabled={markPhoneAgreed.isPending}
                        className="text-xs px-2 py-0.5 rounded border border-gray-300 hover:border-blue-400 hover:text-blue-600 text-gray-500 disabled:opacity-40"
                      >
                        Mark Agreed
                      </button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex items-center gap-3">
                <span className="text-xs text-gray-400">No numbers found</span>
                {!showAddPhone && (
                  <button
                    onClick={() => setShowAddPhone(true)}
                    className="text-xs px-2 py-1 rounded border border-dashed border-gray-300 text-gray-500 hover:border-blue-400 hover:text-blue-600"
                  >
                    + Add number
                  </button>
                )}
              </div>
            )}
            {showAddPhone && (
              <div className="flex items-center gap-2 mt-2">
                <input
                  autoFocus
                  type="tel"
                  placeholder="10-digit number"
                  maxLength={10}
                  value={newPhoneInput}
                  onChange={e => setNewPhoneInput(e.target.value.replace(/\D/g, ''))}
                  className="w-40 border border-gray-300 rounded-lg px-2.5 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
                <button
                  onClick={() => {
                    if (newPhoneInput.length < 10) return
                    const existing = client.phone_numbers ?? []
                    update.mutate({
                      poc_phone: newPhoneInput,
                      phone_numbers: [...existing, { source: 'Manual', number: newPhoneInput }],
                    })
                    setShowAddPhone(false)
                    setNewPhoneInput('')
                  }}
                  disabled={newPhoneInput.length < 10 || update.isPending}
                  className="text-xs px-3 py-1.5 rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
                >
                  Save
                </button>
                <button
                  onClick={() => { setShowAddPhone(false); setNewPhoneInput('') }}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>
          {client.key_skills?.length > 0 && (
            <div>
              <p className="text-xs font-medium text-gray-500 mb-2">Key Skills</p>
              <div className="flex flex-wrap gap-1">
                {client.key_skills.map(s => (
                  <span key={s} className="px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded-full">{s}</span>
                ))}
              </div>
            </div>
          )}
          {client.agreement_url && (
            <a href={client.agreement_url} target="_blank" rel="noreferrer" className="text-xs text-blue-500 hover:underline">
              View Agreement ↗
            </a>
          )}
          {/* Scheduling section */}
          <div className="border border-gray-200 rounded-xl p-5">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold text-gray-800">Interview Scheduling</h3>
                <p className="text-xs text-gray-400 mt-0.5">Share this link with the client to collect their availability</p>
              </div>
              <button
                onClick={() => {
                  const scheduleUrl = `${window.location.protocol}//${window.location.host}/schedule/${client.id}`
                  navigator.clipboard.writeText(scheduleUrl)
                  setLinkCopied(true)
                  setTimeout(() => setLinkCopied(false), 2000)
                }}
                className="text-xs px-2.5 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-600 whitespace-nowrap"
              >
                {linkCopied ? '✓ Copied' : 'Copy Link'}
              </button>
            </div>

            <p className="text-xs text-gray-400 font-mono break-all bg-gray-50 rounded-lg px-3 py-2 mb-4">
              {`${window.location.protocol}//${window.location.host}/schedule/${client.id}`}
            </p>

            {client.available_slots && client.available_slots.length > 0 ? (
              <div>
                <div className="flex items-center justify-between mb-2">
                  <p className="text-xs font-medium text-gray-500">Client's Available Times</p>
                  <button
                    onClick={() => setShowAddSlot(true)}
                    className="text-xs px-2 py-1 rounded-lg border border-dashed border-gray-300 text-gray-500 hover:border-blue-400 hover:text-blue-600"
                  >
                    + Add Slot
                  </button>
                </div>
                <div className="space-y-1.5">
                  {client.available_slots.map((s, i) => (
                    <div key={i} className="flex items-center gap-2 text-xs">
                      <button
                        onClick={() => patchClient.mutate({
                          available_slots: client.available_slots!.filter((_, j) => j !== i)
                        })}
                        disabled={patchClient.isPending}
                        className="text-gray-400 hover:text-red-500 disabled:opacity-30 leading-none text-sm font-medium shrink-0"
                        title="Remove slot"
                      >
                        ×
                      </button>
                      <span className="w-2 h-2 rounded-full bg-green-400 shrink-0" />
                      <span className="text-gray-700 flex-1">{s.label}</span>
                      {s.client_name && <span className="text-gray-400">— {s.client_name}</span>}
                      {s.received_at && !s.client_name && (
                        <span className="text-gray-400">{new Date(s.received_at).toLocaleString()}</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <p className="text-xs text-gray-400">No availability submitted yet.</p>
                <button
                  onClick={() => setShowAddSlot(true)}
                  className="text-xs px-2 py-1 rounded-lg border border-dashed border-gray-300 text-gray-500 hover:border-blue-400 hover:text-blue-600"
                >
                  + Add Slot
                </button>
              </div>
            )}

          </div>

          {/* AI Call History */}
          {aiCalls.length > 0 && (
            <div className="border border-gray-200 rounded-xl p-5">
              <h3 className="text-sm font-semibold text-gray-800 mb-3">AI Call History</h3>
              <div className="space-y-3">
                {aiCalls.map(call => {
                  const isExpanded = expandedCalls.has(call.id)
                  return (
                    <div key={call.id} className="border border-gray-100 rounded-lg p-3">
                      <div className="flex flex-wrap items-center gap-2 mb-1.5">
                        <span className={`px-2 py-0.5 text-xs rounded-full ${aiCallTypeBadge(call.call_type)}`}>
                          {aiCallTypeLabel(call.call_type)}
                        </span>
                        <span className={`px-2 py-0.5 text-xs rounded-full ${aiCallStatusBadge(call.status)}`}>
                          {stageLabel(call.status)}
                        </span>
                        {call.classification && (
                          <span className={`px-2 py-0.5 text-xs rounded-full ${aiCallClassificationBadge(call.classification)}`}>
                            {stageLabel(call.classification)}
                          </span>
                        )}
                        <span className="text-xs text-gray-400 ml-auto">
                          {new Date(call.triggered_at).toLocaleString()}
                          {call.completed_at && ` → ${new Date(call.completed_at).toLocaleString()}`}
                        </span>
                      </div>

                      {call.classification_reason && (
                        <p className="text-xs text-gray-600 mb-1.5">{call.classification_reason}</p>
                      )}

                      {call.extracted_info && (
                        <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500 mb-1.5">
                          {call.extracted_info.alt_poc_name && (
                            <span>Alt POC: <span className="text-gray-700">{call.extracted_info.alt_poc_name}</span></span>
                          )}
                          {call.extracted_info.alt_poc_phone && (
                            <span>Alt Phone: <span className="text-gray-700 font-mono">{call.extracted_info.alt_poc_phone}</span></span>
                          )}
                          {call.extracted_info.callback_note && (
                            <span>Callback: <span className="text-gray-700">{call.extracted_info.callback_note}</span></span>
                          )}
                          {call.extracted_info.query_note && (
                            <span>Query: <span className="text-gray-700">{call.extracted_info.query_note}</span></span>
                          )}
                        </div>
                      )}

                      <div className="flex items-center gap-3 mt-1.5">
                        {call.recording_url && (
                          <a href={call.recording_url} target="_blank" rel="noreferrer" className="text-xs text-blue-500 hover:underline">
                            Recording ↗
                          </a>
                        )}
                        {call.transcript && (
                          <button
                            onClick={() => setExpandedCalls(prev => {
                              const next = new Set(prev)
                              if (next.has(call.id)) next.delete(call.id)
                              else next.add(call.id)
                              return next
                            })}
                            className="text-xs text-gray-500 hover:text-gray-700"
                          >
                            {isExpanded ? 'Hide transcript' : 'Show transcript'}
                          </button>
                        )}
                      </div>

                      {isExpanded && call.transcript && (
                        <pre className="mt-2 text-xs text-gray-600 whitespace-pre-wrap bg-gray-50 rounded-lg p-3 max-h-64 overflow-y-auto font-sans">
                          {call.transcript}
                        </pre>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          <div>
            <p className="text-xs font-medium text-gray-500 mb-2">Scraping</p>
            <ScrapeButtons clientId={client.id} />
          </div>
        </div>
      )}

      {/* Candidates */}
      {activeTab === 'candidates' && (
        <div>
          {/* Pool health bar */}
          <div className="flex flex-wrap items-center gap-3 mb-4 p-3 rounded-xl bg-gray-50 border border-gray-200">
            <div className="flex items-center gap-1.5">
              <span className={`w-2 h-2 rounded-full ${client?.sourcing_needed ? 'bg-red-500' : 'bg-green-500'}`} />
              <span className={`text-xs font-medium ${client?.sourcing_needed ? 'text-red-600' : 'text-green-700'}`}>
                {client?.sourcing_needed ? 'Needs sourcing' : 'Pool OK'}
              </span>
            </div>
            <span className="text-gray-300 text-xs">|</span>
            <span className="text-xs text-gray-600">
              <span className="font-semibold text-orange-600">{pool?.locked_count ?? 0}</span> locked by us
            </span>
            <span className="text-xs text-gray-600">
              <span className="font-semibold text-blue-600">{pool?.free_count ?? 0}</span> free (score ≥ 70)
            </span>
            {(pool?.locked_by_other ?? 0) > 0 && (
              <span className="text-xs text-gray-500">
                <span className="font-semibold">{pool!.locked_by_other}</span> taken by other client
              </span>
            )}
            <span className="text-xs text-gray-600 flex items-center gap-1.5">
              <span className={`font-semibold ${mappings.length >= 270 ? 'text-red-600' : mappings.length >= 210 ? 'text-amber-600' : 'text-gray-800'}`}>
                {mappings.length}
              </span>
              <span className="text-gray-400">/ 300 reached out</span>
              <span className="w-16 h-1.5 bg-gray-200 rounded-full overflow-hidden inline-block">
                <span
                  className={`h-full block rounded-full ${mappings.length >= 270 ? 'bg-red-500' : mappings.length >= 210 ? 'bg-amber-500' : 'bg-blue-500'}`}
                  style={{ width: `${Math.min((mappings.length / 300) * 100, 100)}%` }}
                />
              </span>
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-3 mb-4">
            <button
              onClick={() => { setAutoMatchIncludeMuslim(false); setShowAutoMatchModal(true) }}
              disabled={autoMatch.isPending}
              className="px-3 py-1.5 text-xs rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              {autoMatch.isPending ? 'Matching…' : '🔍 Auto-Match Candidates'}
            </button>
            <button
              onClick={() => { setBatchResult(null); runBatch.mutate() }}
              disabled={runBatch.isPending}
              className="px-3 py-1.5 text-xs rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
            >
              {runBatch.isPending ? 'Sending…' : '📤 Send Outreach Batch'}
            </button>
            <button
              onClick={() => { setBatchResult(null); rerun.mutate() }}
              disabled={rerun.isPending}
              className="px-3 py-1.5 text-xs rounded-lg bg-amber-500 text-white hover:bg-amber-600 disabled:opacity-50"
            >
              {rerun.isPending ? 'Running…' : '🔄 Re-run Matching'}
            </button>
            <button
              onClick={sendToClientReview}
              disabled={sendingClientReview}
              title="Moves all Interested candidates with score ≥ 70 to Client Approval Pending"
              className="px-3 py-1.5 text-xs rounded-lg bg-indigo-50 text-indigo-700 hover:bg-indigo-100 disabled:opacity-50"
            >
              {sendingClientReview ? 'Sending…' : '✓ Send to Client Review'}
            </button>
            <button
              onClick={() => setShowAddCandidate(true)}
              className="px-3 py-1.5 text-xs rounded-lg border border-gray-300 hover:bg-gray-50"
            >
              + Add Manually
            </button>
            {selectedMappings.size > 0 && (
              <>
                <button
                  onClick={() => setShowBulkWA(true)}
                  className="px-3 py-1.5 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100"
                >
                  📲 Bulk WA ({selectedMappings.size})
                </button>
<button
                  onClick={bulkRemoveMappings}
                  disabled={bulkRemoving}
                  className="px-3 py-1.5 text-xs rounded-lg bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
                >
                  {bulkRemoving ? 'Removing…' : `Remove (${selectedMappings.size})`}
                </button>
              </>
            )}
          </div>
          {autoMatch.isError && (
            <p className="text-xs text-red-500 mb-2">Auto-match failed. Check candidate location data.</p>
          )}
          {batchResult && (
            <p className="text-xs text-teal-700 mb-2 bg-teal-50 px-3 py-1.5 rounded-lg">{batchResult}</p>
          )}
          {clientReviewResult && (
            <p className="text-xs text-indigo-700 mb-2 bg-indigo-50 px-3 py-1.5 rounded-lg">{clientReviewResult}</p>
          )}
          {(runBatch.isError || rerun.isError) && (
            <p className="text-xs text-red-500 mb-2">Batch send failed — check backend logs.</p>
          )}
          {client.feedback_token && (
            <div className="flex flex-col gap-1.5 mb-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-24 shrink-0">Portal link:</span>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(`${window.location.protocol}//${window.location.host}/client/${client.feedback_token}`)
                  }}
                  className="text-xs text-indigo-600 hover:underline font-mono"
                >
                  /client/{client.feedback_token?.slice(0, 8)}… (copy)
                </button>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-24 shrink-0">Feedback link:</span>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(`${window.location.protocol}//${window.location.host}/client-feedback/${client.feedback_token}`)
                  }}
                  className="text-xs text-blue-500 hover:underline font-mono"
                >
                  /client-feedback/{client.feedback_token?.slice(0, 8)}… (copy)
                </button>
              </div>
            </div>
          )}

          {mappingsLoading ? (
            <div className="space-y-2">{[...Array(3)].map((_, i) => <div key={i} className="h-12 bg-gray-100 animate-pulse rounded-lg" />)}</div>
          ) : mappings.length === 0 ? (
            <p className="text-sm text-gray-400">No candidates mapped. Click Auto-Match to find matches.</p>
          ) : (() => {
            // Top-level status groups — short list, no scrolling needed
            const statusGroups: { value: string; label: string; match: (m: any) => boolean }[] = [
              { value: 'all', label: 'All statuses', match: () => true },
              { value: 'interested', label: 'Interested', match: m => m.stage === 'interested' },
              { value: 'client_approval_pending', label: 'Client Review Pending', match: m => m.stage === 'client_approval_pending' },
              { value: 'not_interested', label: 'Not Interested', match: m => m.stage === 'not_interested' },
              { value: 'rejected', label: 'Rejected', match: m => m.stage === 'rejected' },
              { value: 'placed', label: 'Placed', match: m => m.stage === 'placed' },
              { value: 'in_progress', label: 'In Progress', match: m => ['matched', 'intent_ask', 'slot_booked', 'interview_done'].includes(m.stage) },
            ]
            const activeStatusGroup = statusGroups.find(f => f.value === statusFilter) ?? statusGroups[0]

            // Second-level "reason" dropdown — only shown for groups that have sub-reasons
            const reasonOptionsByGroup: Record<string, { value: string; label: string; match: (m: any) => boolean }[]> = {
              not_interested: [
                { value: 'all', label: 'All reasons', match: () => true },
                { value: 'not_interested_role', label: 'Not Interested — Role', match: m => m.decline_reason === 'not_interested_role' },
                { value: 'not_looking_for_job', label: 'Not Looking for Job', match: m => m.decline_reason === 'not_looking_for_job' },
                { value: 'evaluation_failed', label: 'Failed Evaluation', match: m => m.decline_reason === 'evaluation_failed' },
              ],
              rejected: [
                { value: 'all', label: 'All reasons', match: () => true },
                { value: 'client_disliked', label: 'Rejected by Client', match: m => m.rejection_reason === 'client_disliked' },
                { value: 'filters_not_matched', label: 'Filters Not Matched (Overqualified etc.)', match: m => m.rejection_reason === 'filters_not_matched' },
                { value: 'slot_expired', label: 'Slot Not Picked (Expired)', match: m => m.rejection_reason === 'slot_expired' },
                { value: 'position_filled', label: 'Position Filled (Waitlisted → Closed)', match: m => m.rejection_reason === 'position_filled' },
              ],
            }
            const activeReasonOptions = reasonOptionsByGroup[statusFilter]
            const activeReasonFilter = activeReasonOptions?.find(f => f.value === reasonFilter) ?? activeReasonOptions?.[0]
            const matchesReason = (m: any) => !activeReasonOptions || !activeReasonFilter || activeReasonFilter.match(m)

            const progressFilters: { value: string; label: string }[] = [
              { value: 'all', label: 'All progress' },
              { value: 'form_pending', label: 'Form Not Filled' },
              { value: 'form_filled', label: 'Form Filled — Awaiting Call' },
              { value: 'call_completed', label: 'AI Call Completed' },
              { value: 'call_dropped', label: 'AI Call Dropped' },
              { value: 'evaluated_pass', label: 'Evaluated — Pass' },
              { value: 'evaluated_fail', label: 'Evaluated — Fail' },
            ]
            const matchesProgress = (m: any) =>
              progressFilter === 'all' || pipelineProgress(m).key === progressFilter

            const activeMappings = mappings.filter((m: any) =>
              m.stage !== 'waitlisted' && activeStatusGroup.match(m) && matchesReason(m) && matchesProgress(m))
            const waitlistedMappings = mappings.filter((m: any) => m.stage === 'waitlisted')
            const _parseLabelIso = (label: string): string | null => {
              try {
                const [datePart, timePart] = label.split('·').map((s: string) => s.trim())
                const startTime = timePart.split('–')[0].trim()
                const dateStr = datePart.replace(/^[A-Za-z]+,\s*/, '').trim()
                const [dayStr, monthStr, yearStr] = dateStr.split(' ')
                const months = ['january','february','march','april','may','june','july','august','september','october','november','december']
                const month = months.indexOf(monthStr.toLowerCase())
                const day = parseInt(dayStr, 10); const year = parseInt(yearStr, 10)
                if (month === -1 || isNaN(day) || isNaN(year)) return null
                const m2 = startTime.match(/^(\d{1,2}):(\d{2})\s*(am|pm)$/i)
                if (!m2) return null
                let hour = parseInt(m2[1], 10); const minute = parseInt(m2[2], 10)
                if (m2[3].toLowerCase() === 'pm' && hour !== 12) hour += 12
                if (m2[3].toLowerCase() === 'am' && hour === 12) hour = 0
                return new Date(year, month, day, hour, minute, 0, 0).toISOString()
              } catch { return null }
            }
            const clientSlots: { label: string; iso: string | null }[] =
              client.recruiter_slots && client.recruiter_slots.length > 0
                ? client.recruiter_slots.map((s: any) => ({ label: s.label, iso: s.iso ?? null }))
                : (client.available_slots ?? []).map((s: any) => ({ label: s.label, iso: s.iso ?? _parseLabelIso(s.label) }))
            const visibleMappings = showAllScores
              ? activeMappings
              : activeMappings.filter(m => (m.match_score ?? 0) >= 70)
            const hiddenCount = activeMappings.length - visibleMappings.length
            return (
              <div>
                <div className="flex items-center justify-end gap-3 mb-3 flex-wrap">
                  <div>
                    <label className="text-xs text-gray-500 mr-2">Filter by status:</label>
                    <select
                      value={statusFilter}
                      onChange={e => { setStatusFilter(e.target.value); setReasonFilter('all') }}
                      className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
                    >
                      {statusGroups.map(f => {
                        const count = mappings.filter((m: any) => m.stage !== 'waitlisted' && f.match(m)).length
                        return <option key={f.value} value={f.value}>{f.label} ({count})</option>
                      })}
                    </select>
                  </div>
                  {activeReasonOptions && (
                    <div>
                      <label className="text-xs text-gray-500 mr-2">Reason:</label>
                      <select
                        value={reasonFilter}
                        onChange={e => setReasonFilter(e.target.value)}
                        className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
                      >
                        {activeReasonOptions.map(f => {
                          const count = mappings.filter((m: any) => m.stage !== 'waitlisted' && activeStatusGroup.match(m) && f.match(m)).length
                          return <option key={f.value} value={f.value}>{f.label} ({count})</option>
                        })}
                      </select>
                    </div>
                  )}
                  <div>
                    <label className="text-xs text-gray-500 mr-2">Filter by progress:</label>
                    <select
                      value={progressFilter}
                      onChange={e => setProgressFilter(e.target.value)}
                      className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700"
                    >
                      {progressFilters.map(f => {
                        const count = mappings.filter((m: any) => m.stage !== 'waitlisted' && (f.value === 'all' || pipelineProgress(m).key === f.value)).length
                        return <option key={f.value} value={f.value}>{f.label} ({count})</option>
                      })}
                    </select>
                  </div>
                </div>
                <div className="flex items-center justify-between mb-2">
                  {hiddenCount > 0 && !showAllScores ? (
                    <p className="text-xs text-gray-400">
                      Showing {visibleMappings.length} of {activeMappings.length} candidates (score ≥ 70) —{' '}
                      <button
                        onClick={() => setShowAllScores(true)}
                        className="text-blue-500 hover:underline"
                      >
                        show all {activeMappings.length}
                      </button>
                    </p>
                  ) : showAllScores && hiddenCount > 0 ? (
                    <p className="text-xs text-gray-400">
                      Showing all {activeMappings.length} candidates —{' '}
                      <button
                        onClick={() => setShowAllScores(false)}
                        className="text-blue-500 hover:underline"
                      >
                        hide low scores
                      </button>
                    </p>
                  ) : <span />}
                </div>
                <div className="overflow-x-auto rounded-xl border border-gray-200">
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        <th className="px-3 py-3 w-8">
                          <input
                            type="checkbox"
                            className="rounded"
                            checked={selectedMappings.size === visibleMappings.length && visibleMappings.length > 0}
                            onChange={() =>
                              setSelectedMappings(
                                selectedMappings.size === visibleMappings.length
                                  ? new Set()
                                  : new Set(visibleMappings.map(m => m.id))
                              )
                            }
                          />
                        </th>
                        {['Name', 'Category', 'Location', 'Salary', 'Match', 'Status', 'Actions', ''].map(h => (
                          <th key={h} className="px-3 py-3 text-left text-xs font-medium text-gray-500">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {visibleMappings.map(m => (
                        <MappedCandidateRow
                          key={m.id}
                          mapping={m}
                          clientId={client.id}
                          selected={selectedMappings.has(m.id)}
                          onToggle={() => toggleMapping(m.id)}
                          onBookInterview={id => setBookingMappingId(id)}
                          clientSlots={clientSlots}
                        />
                      ))}
                    </tbody>
                  </table>
                </div>

                {/* Waitlisted candidates — internal only, hidden from client */}
                <div className="mt-4 border border-amber-200 rounded-xl overflow-hidden">
                    <button
                      className="w-full flex items-center justify-between px-4 py-3 bg-amber-50 hover:bg-amber-100 text-left transition-colors"
                      onClick={() => setShowWaitlisted(v => !v)}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-amber-800 uppercase tracking-wide">
                          Waitlisted
                        </span>
                        <span className="text-xs bg-amber-200 text-amber-800 font-bold px-2 py-0.5 rounded-full">
                          {waitlistedMappings.length}
                        </span>
                        <span className="text-xs text-amber-600">— slots were full when they qualified</span>
                      </div>
                      <svg
                        className={`w-4 h-4 text-amber-500 transition-transform ${showWaitlisted ? 'rotate-180' : ''}`}
                        fill="none" viewBox="0 0 24 24" stroke="currentColor"
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                      </svg>
                    </button>
                    {showWaitlisted && (
                      waitlistedMappings.length === 0 ? (
                        <p className="px-4 py-3 text-xs text-amber-600">No waitlisted candidates yet.</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className="w-full text-sm">
                            <thead className="bg-amber-50 border-b border-amber-200">
                              <tr>
                                <th className="px-3 py-3 w-8" />
                                {['Name', 'Category', 'Location', 'Salary', 'Match', 'Status', 'Actions', ''].map(h => (
                                  <th key={h} className="px-3 py-3 text-left text-xs font-medium text-amber-700">{h}</th>
                                ))}
                              </tr>
                            </thead>
                            <tbody>
                              {waitlistedMappings.map(m => (
                                <MappedCandidateRow
                                  key={m.id}
                                  mapping={m}
                                  clientId={client.id}
                                  selected={selectedMappings.has(m.id)}
                                  onToggle={() => toggleMapping(m.id)}
                                  clientSlots={clientSlots}
                                />
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )
                    )}
                  </div>
              </div>
            )
          })()}

          {/* Filter pool — all candidates passing hard filters, not yet mapped */}
          <div className="mt-6 border border-gray-200 rounded-xl overflow-hidden">
            <button
              className="w-full flex items-center justify-between px-4 py-3 bg-gray-50 hover:bg-gray-100 text-left"
              onClick={() => setShowFilterPool(v => !v)}
            >
              <span className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
                Full filter pool — passed hard filters, not mapped
                {filterPool && ` (${filterPool.count})`}
              </span>
              <svg
                className={`w-4 h-4 text-gray-400 transition-transform ${showFilterPool ? 'rotate-180' : ''}`}
                fill="none" viewBox="0 0 24 24" stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
              </svg>
            </button>

            {showFilterPool && (
              <div className="overflow-x-auto">
                {filterPoolLoading ? (
                  <div className="p-4 space-y-2">
                    {[...Array(3)].map((_, i) => <div key={i} className="h-8 bg-gray-100 animate-pulse rounded" />)}
                  </div>
                ) : !filterPool || filterPool.count === 0 ? (
                  <p className="px-4 py-4 text-sm text-gray-400">No additional candidates pass the hard filters for this client.</p>
                ) : (
                  <table className="w-full text-sm">
                    <thead className="bg-gray-50 border-b border-gray-200">
                      <tr>
                        {['#', 'Name', 'Category', 'Location', 'Salary', 'Score', 'Dist', ''].map(h => (
                          <th key={h} className="px-3 py-2 text-left text-xs font-medium text-gray-500">{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {filterPool.data.map((c, i) => (
                        <tr key={c.id} className={`border-b border-gray-100 last:border-0 ${c.active_positions >= 5 ? 'opacity-55' : ''}`}>
                          <td className="px-3 py-2 text-xs text-gray-400">{i + 1}</td>
                          <td className="px-3 py-2 text-xs font-medium text-gray-800">
                            <div>{c.name ?? '—'}</div>
                            {c.active_positions >= 5 && (
                              <div className="text-[10px] text-orange-500 mt-0.5">🔒 5/5 active positions</div>
                            )}
                          </td>
                          <td className="px-3 py-2 text-xs text-gray-500">{c.category ?? '—'}</td>
                          <td className="px-3 py-2 text-xs text-gray-500">{c.current_location ?? '—'}</td>
                          <td className="px-3 py-2 text-xs text-gray-500">
                            {c.current_salary ? `₹${Number(c.current_salary).toLocaleString('en-IN')}` : '—'}
                          </td>
                          <td className="px-3 py-2">
                            <ScoreBadgeWithTooltip score={c.mms_score} breakdown={c.score_breakdown} />
                          </td>
                          <td className="px-3 py-2 text-xs text-gray-500">
                            {c.distance_km != null ? `${c.distance_km} km` : '—'}
                          </td>
                          <td className="px-3 py-2">
                            {c.active_positions < 5 && (
                              <button
                                onClick={() => addFromPool.mutate(c)}
                                disabled={addFromPool.isPending}
                                className="text-xs px-2.5 py-1 rounded-lg border border-blue-300 text-blue-600 hover:bg-blue-50 disabled:opacity-40 whitespace-nowrap"
                              >
                                + Add
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* Timeline */}
      {activeTab === 'timeline' && <StageTimeline events={timeline} />}

      {/* Comments */}
      {activeTab === 'comments' && <ClientComments clientId={id!} />}

      {/* Edit modal */}
      {editMode && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-2xl p-6 max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">Edit Client</h2>
              <button onClick={() => setEditMode(false)} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>
            <ClientForm initial={client} loading={update.isPending} onCancel={() => setEditMode(false)} onSubmit={data => update.mutate(data)} />
          </div>
        </div>
      )}

      {/* Auto-match options */}
      {showAutoMatchModal && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-base font-semibold mb-1">Auto-Match Candidates</h2>
            <p className="text-sm text-gray-500 mb-5">Choose which candidates to include in this match run.</p>
            <label className="flex items-center gap-3 cursor-pointer mb-6">
              <input
                type="checkbox"
                checked={autoMatchIncludeMuslim}
                onChange={e => setAutoMatchIncludeMuslim(e.target.checked)}
                className="w-4 h-4 rounded border-gray-300 text-blue-600"
              />
              <span className="text-sm text-gray-700">Include Muslim candidates</span>
            </label>
            <div className="flex gap-3 justify-end">
              <button
                onClick={() => setShowAutoMatchModal(false)}
                className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Cancel
              </button>
              <button
                onClick={() => { setShowAutoMatchModal(false); autoMatch.mutate(autoMatchIncludeMuslim) }}
                className="px-4 py-2 text-sm bg-blue-600 text-white rounded-lg hover:bg-blue-700"
              >
                Run Match
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete confirm */}
      {showDeleteConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-base font-semibold mb-2">Delete {client.company_name}?</h2>
            <p className="text-sm text-gray-500 mb-6">This will permanently delete the client and all mappings.</p>
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowDeleteConfirm(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
                className="px-4 py-2 text-sm bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50"
              >
                {remove.isPending ? 'Deleting…' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Churn confirm */}
      {showChurnConfirm && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl p-6 w-full max-w-sm">
            <h2 className="text-base font-semibold mb-2">Mark {client.company_name} as churned?</h2>
            <p className="text-sm text-gray-500 mb-3">
              This will move the client to the <strong>Churned</strong> stage.
            </p>
            {lockedCandidates.length > 0 ? (
              <div className="mb-6">
                <p className="text-sm text-gray-500 mb-2">
                  {lockedCandidates.length} candidate{lockedCandidates.length > 1 ? 's' : ''} currently locked to this
                  client will be <strong>unlocked</strong> and become available to other clients:
                </p>
                <ul className="text-sm text-gray-700 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2 max-h-32 overflow-y-auto list-disc list-inside">
                  {lockedCandidates.map(c => <li key={c.id}>{c.name}</li>)}
                </ul>
              </div>
            ) : (
              <p className="text-sm text-gray-500 mb-6">No candidates are currently locked to this client.</p>
            )}
            <div className="flex gap-3 justify-end">
              <button onClick={() => setShowChurnConfirm(false)} className="px-4 py-2 text-sm border border-gray-300 rounded-lg hover:bg-gray-50">Cancel</button>
              <button
                onClick={() => changeStage.mutate({ stage: 'churned' }, { onSuccess: () => setShowChurnConfirm(false) })}
                disabled={changeStage.isPending}
                className="px-4 py-2 text-sm bg-gray-700 text-white rounded-lg hover:bg-gray-800 disabled:opacity-50"
              >
                {changeStage.isPending ? 'Marking…' : 'Mark Churned'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Bulk WA modal */}
      {showBulkWA && (
        <BulkWAModal
          mappingIds={[...selectedMappings]}
          onClose={() => { setShowBulkWA(false); setSelectedMappings(new Set()) }}
        />
      )}

      {/* Agreement generator modal */}
      {showAgreementModal && (
        <AgreementModal client={client} onClose={() => setShowAgreementModal(false)} />
      )}

      {/* Add slot modal (available_slots) */}
      {showAddSlot && client && (
        <AddSlotModal
          currentSlots={client.available_slots}
          onSave={slots => {
            patchClient.mutate({ available_slots: slots })
            setShowAddSlot(false)
          }}
          onClose={() => setShowAddSlot(false)}
          isPending={patchClient.isPending}
        />
      )}

      {/* Book interview modal */}
      {bookingMappingId && client && (() => {
        // Normalise slots from either source into { label, iso }
        const parseLabelIso = (label: string): string | null => {
          try {
            // "Thursday, 18 June 2026 · 09:00 am – 10:00 am"
            const [datePart, timePart] = label.split('·').map(s => s.trim())
            const startTime = timePart.split('–')[0].trim() // "09:00 am"
            const dateStr = datePart.replace(/^[A-Za-z]+,\s*/, '').trim() // "18 June 2026"
            const [dayStr, monthStr, yearStr] = dateStr.split(' ')
            const months = ['january','february','march','april','may','june','july','august','september','october','november','december']
            const month = months.indexOf(monthStr.toLowerCase())
            const day = parseInt(dayStr, 10)
            const year = parseInt(yearStr, 10)
            if (month === -1 || isNaN(day) || isNaN(year)) return null
            const m = startTime.match(/^(\d{1,2}):(\d{2})\s*(am|pm)$/i)
            if (!m) return null
            let hour = parseInt(m[1], 10)
            const minute = parseInt(m[2], 10)
            if (m[3].toLowerCase() === 'pm' && hour !== 12) hour += 12
            if (m[3].toLowerCase() === 'am' && hour === 12) hour = 0
            return new Date(year, month, day, hour, minute, 0, 0).toISOString()
          } catch { return null }
        }
        const bookingSlots: { label: string; iso: string | null }[] =
          client.recruiter_slots && client.recruiter_slots.length > 0
            ? client.recruiter_slots.map(s => ({ label: s.label, iso: s.iso ?? null }))
            : (client.available_slots ?? []).map(s => ({ label: s.label, iso: s.iso ?? parseLabelIso(s.label) }))

        return (
          <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6">
              <div className="flex items-center justify-between mb-4">
                <h2 className="text-sm font-semibold text-gray-800">Book Interview</h2>
                <button onClick={() => { setBookingMappingId(null); setSelectedBookingSlot(null) }} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
              </div>
              {bookingSlots.length > 0 ? (
                <>
                  <div className="space-y-2 max-h-60 overflow-y-auto mb-4">
                    <p className="text-xs text-gray-500 mb-3">Select a slot from the client's available times:</p>
                    {bookingSlots.map((s, i) => (
                      <button
                        key={i}
                        onClick={() => setSelectedBookingSlot(s.iso ? { label: s.label, iso: s.iso } : null)}
                        disabled={!s.iso}
                        className={`w-full text-left px-4 py-3 rounded-xl border text-sm transition-colors disabled:opacity-40 ${
                          selectedBookingSlot?.iso === s.iso
                            ? 'border-indigo-500 bg-indigo-50 text-indigo-700 font-medium'
                            : 'border-gray-200 text-gray-700 hover:border-indigo-300 hover:bg-indigo-50'
                        }`}
                      >
                        {s.label}
                      </button>
                    ))}
                  </div>
                  <button
                    onClick={() => {
                      const mapping = mappings.find((m: any) => m.id === bookingMappingId)
                      if (!mapping || !selectedBookingSlot) return
                      bookInterview.mutate({ mappingId: bookingMappingId, slotIso: selectedBookingSlot.iso, candidateId: mapping.candidate_id })
                    }}
                    disabled={!selectedBookingSlot || bookInterview.isPending}
                    className="w-full py-2.5 rounded-xl bg-indigo-600 text-white text-sm font-medium hover:bg-indigo-700 disabled:opacity-40 transition-colors"
                  >
                    {bookInterview.isPending ? 'Booking…' : 'Confirm Booking'}
                  </button>
                </>
              ) : (
                <p className="text-xs text-gray-400">No interview slots set for this client yet. Set slots first.</p>
              )}
              {bookInterview.isError && (
                <p className="text-xs text-red-500 mt-3">{(bookInterview.error as any)?.response?.data?.detail || 'Booking failed'}</p>
              )}
            </div>
          </div>
        )
      })()}

      {/* Reachout template picker */}
      {showReachoutPicker && client && (
        <TemplatePickerModal
          title={`Send Reachout — ${client.company_name}`}
          prefill={{
            '1': client.poc_name ?? '',
            '2': client.company_name ?? '',
            '3': client.job_title ?? '',
          }}
          loading={reachout.isPending}
          onSend={(name, lang, variables, header) => reachout.mutate({ template_name: name, lang, variables, header_url: header?.url, header_type: header?.type })}
          onClose={() => setShowReachoutPicker(false)}
        />
      )}

      {/* Manual add candidate modal */}
      {showAddCandidate && (
        <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
          <div className="bg-white rounded-xl shadow-xl w-full max-w-xl p-6">
            <div className="flex items-center justify-between mb-4">
              <h2 className="text-base font-semibold">Add Candidate</h2>
              <button onClick={() => { setShowAddCandidate(false); setLockedWarning(null) }} className="text-gray-400 hover:text-gray-600 text-xl">×</button>
            </div>

            {lockedWarning && (
              <div className="mb-3 flex items-start gap-2 px-3 py-2.5 rounded-lg bg-orange-50 border border-orange-200 text-xs text-orange-700">
                <span className="text-base leading-none mt-0.5">🔒</span>
                <div>
                  <p className="font-medium">Cannot add locked candidate</p>
                  <p className="text-orange-600 mt-0.5">{lockedWarning}</p>
                </div>
                <button onClick={() => setLockedWarning(null)} className="ml-auto text-orange-400 hover:text-orange-600 text-base leading-none">×</button>
              </div>
            )}

            <input
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm mb-3 focus:outline-none focus:ring-2 focus:ring-blue-400"
              placeholder="Search by name or phone…"
              value={candidateSearch}
              onChange={e => { setCandidateSearch(e.target.value); setLockedWarning(null) }}
              autoFocus
            />
            <div className="max-h-72 overflow-y-auto space-y-1">
              {allCandidates.length === 0 ? (
                <p className="text-sm text-gray-400 py-4 text-center">No candidates found.</p>
              ) : allCandidates.map((c: Candidate) => {
                const alreadyMapped = mappedCandidateIds.has(c.id)
                return (
                  <div
                    key={c.id}
                    className={`flex items-center justify-between p-3 rounded-lg border ${
                      alreadyMapped ? 'border-gray-100 bg-gray-50 opacity-60' : 'border-gray-200 hover:border-blue-300 hover:bg-blue-50 cursor-pointer'
                    }`}
                    onClick={() => {
                      if (alreadyMapped) return
                      addManual.mutate(c.id)
                    }}
                  >
                    <div>
                      <p className="text-sm font-medium text-gray-800">{c.name}</p>
                      <p className="text-xs text-gray-500">{c.phone} · {c.category} · {c.current_location}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      {c.total_score != null && (
                        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${scoreBadge(c.total_score)}`}>
                          {Number(c.total_score).toFixed(0)}
                        </span>
                      )}
                      {alreadyMapped ? (
                        <span className="text-xs text-gray-400">Mapped</span>
                      ) : (
                        <span className="text-xs text-blue-600">{addManual.isPending ? '…' : '+ Add'}</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value?: string | null }) {
  if (!value) return null
  return (
    <div>
      <p className="text-xs text-gray-400">{label}</p>
      <p className="text-sm text-gray-800">{value}</p>
    </div>
  )
}
