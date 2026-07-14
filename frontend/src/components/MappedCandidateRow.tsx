import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { deleteMapping } from '../api/mappings'
import { upsertCandidateOverride } from '../api/candidates'
import type { Mapping } from '../types'
import { mappingStageBadge, stageLabel, declineReasonLabel, declineReasonBadge, rejectionReasonLabel, rejectionReasonBadge, pipelineProgress } from '../types'
import MappingActionButton from './MappingActionButton'
import ScoreBadgeWithTooltip from './ScoreBadgeWithTooltip'
import type { ScoreBreakdown } from './ScoreBadgeWithTooltip'

interface Props {
  mapping: Mapping & {
    candidate_name?: string
    candidate_phone?: string
    candidate_age?: number
    candidate_gender?: string
    current_location?: string
    category?: string
    current_salary?: number
    total_score?: number
    total_mapped_count?: number
    evaluation_status?: string
    form_submitted?: boolean | null
    call_status?: string | null
    distance_km?: number | null
    score_breakdown?: ScoreBreakdown | null
  }
  clientId: string
  selected: boolean
  onToggle: () => void
  onBookInterview?: (mappingId: string) => void
  clientSlots?: { label: string; iso: string | null }[]
}

export default function MappedCandidateRow({ mapping, clientId, selected, onToggle, onBookInterview, clientSlots }: Props) {
  const qc = useQueryClient()
  const [confirmRemove, setConfirmRemove] = useState(false)
  const [confirmDnd, setConfirmDnd] = useState(false)

  const isDnd = (() => {
    const d = (mapping as any).candidate_dnd_until
    return !!d && new Date(d) > new Date()
  })()

  const markDnd = useMutation({
    mutationFn: () => {
      const until = new Date()
      until.setDate(until.getDate() + 180)
      return upsertCandidateOverride(mapping.candidate_id, {
        dnd_until: until.toISOString().split('T')[0],
        is_active: false,
      })
    },
    onSuccess: () => {
      setConfirmDnd(false)
      qc.invalidateQueries({ queryKey: ['mappings', clientId] })
    },
  })

  const remove = useMutation({
    mutationFn: () => deleteMapping(clientId, mapping.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['mappings', clientId] }),
  })

  const name        = (mapping as any).candidate_name ?? '—'
  const phone       = (mapping as any).candidate_phone ?? ''
  const category    = (mapping as any).category ?? ''
  const location    = (mapping as any).current_location ?? ''
  const currentSal  = (mapping as any).current_salary
  const matchScore  = mapping.match_score
  const mappedCount = (mapping as any).total_mapped_count ?? 0
  const distanceKm  = (mapping as any).distance_km ?? null
  const breakdown   = (mapping as any).score_breakdown ?? null
  const candLabels: string[] = (mapping as any).candidate?.labels ?? (mapping as any).labels ?? []
  const progress = pipelineProgress({
    form_submitted: (mapping as any).form_submitted,
    call_status: (mapping as any).call_status,
    evaluation_status: (mapping as any).evaluation_status,
  })

  const activeMappingCount  = (mapping as any).active_mapping_count ?? 0
  const activeMappingClients: string = (mapping as any).active_mapping_clients ?? ''
  const [showMappingInfo, setShowMappingInfo] = useState(false)

  const alreadyContacted = !!mapping.wa_sent_at

  return (
    <tr className={`border-b border-gray-100 ${selected ? 'bg-blue-50' : alreadyContacted ? 'opacity-60 bg-gray-50' : 'hover:bg-gray-50'}`}>
      <td className="px-3 py-3">
        <input
          type="checkbox"
          checked={selected}
          onChange={onToggle}
          className="rounded cursor-pointer"
        />
      </td>
      <td className="px-3 py-3">
        <div className="flex items-center gap-1.5 flex-wrap">
          <p className="text-sm font-medium text-gray-800">{name}</p>
          {/* Active mappings info button */}
          <div className="relative" onMouseEnter={() => setShowMappingInfo(true)} onMouseLeave={() => setShowMappingInfo(false)}>
            <span
              className={`w-4 h-4 rounded-full text-[9px] font-bold flex items-center justify-center border cursor-default ${
                activeMappingCount > 1
                  ? 'bg-amber-100 border-amber-300 text-amber-700'
                  : 'bg-gray-100 border-gray-300 text-gray-500'
              }`}
            >
              i
            </span>
            {showMappingInfo && (
              <div className="absolute left-0 top-5 z-50 bg-white border border-gray-200 rounded-lg shadow-lg p-2.5 min-w-[160px] max-w-[220px]">
                <p className="text-[11px] font-semibold text-gray-700 mb-1">Active mappings</p>
                <p className="text-lg font-bold text-gray-900">{activeMappingCount}</p>
                {activeMappingClients && (
                  <p className="text-[10px] text-gray-500 mt-1 leading-tight">{activeMappingClients}</p>
                )}
              </div>
            )}
          </div>
          {mapping.is_locked && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-full bg-blue-100 text-blue-700 font-medium">🔒 Locked</span>
          )}
        </div>
        <p className="text-xs text-gray-400">{phone}</p>
        <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded-full font-medium mt-1 ${progress.badge}`}>
          {progress.label}
        </span>
        {candLabels.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {candLabels.map(label => (
              <span
                key={label}
                className={`text-[9px] px-1.5 py-0.5 rounded-full font-medium ${
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
        )}
      </td>
      <td className="px-3 py-3 text-xs text-gray-600">{category}</td>
      <td className="px-3 py-3 text-xs text-gray-600">
        <span>{location}</span>
        {distanceKm != null && (
          <p className="text-[10px] text-gray-400 mt-0.5">{distanceKm} km away</p>
        )}
      </td>
      <td className="px-3 py-3 text-xs text-gray-600 whitespace-nowrap">
        {currentSal != null ? `₹${Number(currentSal).toLocaleString()}` : '—'}
      </td>
      <td className="px-3 py-3">
        {matchScore != null
          ? <ScoreBadgeWithTooltip score={matchScore} breakdown={breakdown} />
          : '—'}
      </td>
      <td className="px-3 py-3">
        {mapping.stage === 'not_interested' && mapping.decline_reason ? (
          <span className={`text-xs px-2 py-0.5 rounded-full ${declineReasonBadge(mapping.decline_reason)}`}>
            {declineReasonLabel(mapping.decline_reason)}
          </span>
        ) : mapping.stage === 'rejected' && mapping.rejection_reason ? (
          <span className={`text-xs px-2 py-0.5 rounded-full ${rejectionReasonBadge(mapping.rejection_reason)}`}>
            {rejectionReasonLabel(mapping.rejection_reason)}
          </span>
        ) : (
          <span className={`text-xs px-2 py-0.5 rounded-full ${mappingStageBadge(mapping.stage)}`}>
            {stageLabel(mapping.stage)}
          </span>
        )}
        {mapping.feedback_client && (
          <span className={`ml-1 text-[10px] px-1.5 py-0.5 rounded-full font-medium ${
            mapping.feedback_client === 'liked'
              ? 'bg-green-100 text-green-700'
              : 'bg-red-100 text-red-600'
          }`}>
            {mapping.feedback_client === 'liked' ? '👍' : '👎'}
          </span>
        )}
        {mapping.interview_slot && (
          <p className="text-[10px] text-teal-600 mt-0.5 whitespace-nowrap">
            📅 {new Date(mapping.interview_slot).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
          </p>
        )}
      </td>
      <td className="px-3 py-3">
        <MappingActionButton mapping={mapping} clientId={clientId} clientSlots={clientSlots} />
        {mapping.stage === 'invited_for_interview' && onBookInterview && (
          <button
            onClick={() => onBookInterview(mapping.id)}
            className="mt-1 text-[10px] px-2 py-1 rounded-lg bg-indigo-50 border border-indigo-200 text-indigo-700 font-semibold hover:bg-indigo-100 transition-colors whitespace-nowrap"
          >
            Book Interview
          </button>
        )}
      </td>
      <td className="px-3 py-3">
        <div className="flex flex-col gap-1.5 items-start">
          {/* DND button */}
          {isDnd ? (
            <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-orange-100 text-orange-600 font-medium">
              DND
            </span>
          ) : !confirmDnd ? (
            <button
              onClick={() => setConfirmDnd(true)}
              className="text-[10px] text-orange-400 hover:text-orange-600 font-medium"
            >
              DND
            </button>
          ) : (
            <div className="flex items-center gap-1 whitespace-nowrap">
              <button
                onClick={() => markDnd.mutate()}
                disabled={markDnd.isPending}
                className="text-[10px] text-orange-600 hover:text-orange-800 font-medium disabled:opacity-50"
              >
                {markDnd.isPending ? '…' : 'Mark DND'}
              </button>
              <button
                onClick={() => setConfirmDnd(false)}
                className="text-[10px] text-gray-400 hover:text-gray-600"
              >
                ×
              </button>
            </div>
          )}

          {/* Remove button */}
          {!confirmRemove ? (
            <button
              onClick={() => setConfirmRemove(true)}
              className="text-xs text-red-300 hover:text-red-500"
            >
              ×
            </button>
          ) : (
            <div className="flex items-center gap-1 whitespace-nowrap">
              <button
                onClick={() => remove.mutate()}
                disabled={remove.isPending}
                className="text-xs text-red-600 hover:text-red-800 font-medium disabled:opacity-50"
              >
                {remove.isPending ? '…' : 'Remove'}
              </button>
              <button
                onClick={() => setConfirmRemove(false)}
                className="text-xs text-gray-400 hover:text-gray-600"
              >
                Cancel
              </button>
            </div>
          )}
        </div>
      </td>
    </tr>
  )
}
