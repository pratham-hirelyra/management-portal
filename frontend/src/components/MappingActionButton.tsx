import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { updateMappingStage, modifyInterviewSlot } from '../api/mappings'
import { simulateCandidateIntent } from '../api/whatsapp'
import { api } from '../api/axios'
import type { Mapping, MappingStage } from '../types'

interface Props {
  mapping: Mapping
  clientId: string
  clientSlots?: { label: string; iso: string | null }[]
}

const REJECTION_REASONS = [
  'Salary too high',
  'Skill mismatch',
  'Communication issues',
  'Culture fit',
  'Under-qualified',
  'No show',
  'Client preference',
]

export default function MappingActionButton({ mapping, clientId, clientSlots = [] }: Props) {
  const qc = useQueryClient()
  const [showSlotInput, setShowSlotInput] = useState(false)
  const [slotDatetime, setSlotDatetime] = useState('')
  const [showReschedule, setShowReschedule] = useState(false)
  const [showDisqualify, setShowDisqualify] = useState(false)
  const [selectedReasons, setSelectedReasons] = useState<Set<string>>(new Set())
  const [otherReason, setOtherReason] = useState('')

  const advance = useMutation({
    mutationFn: ({ stage, interview_slot, notes }: { stage: MappingStage; interview_slot?: string; notes?: string }) =>
      updateMappingStage(clientId, mapping.id, stage, { interview_slot, notes }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings', clientId] })
      setShowSlotInput(false)
      setShowDisqualify(false)
      setSelectedReasons(new Set())
      setOtherReason('')
    },
  })

  const reschedule = useMutation({
    mutationFn: () => api.post(`/admin/rms/resend-slot/${mapping.id}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['mappings', clientId] })
      setShowReschedule(false)
    },
  })

  const toggleReason = (r: string) =>
    setSelectedReasons(prev => {
      const next = new Set(prev)
      next.has(r) ? next.delete(r) : next.add(r)
      return next
    })

  const confirmDisqualify = () => {
    const parts = [...selectedReasons]
    if (otherReason.trim()) parts.push(otherReason.trim())
    advance.mutate({ stage: 'rejected', notes: parts.join(', ') || undefined })
  }

  // ── Disqualify reason picker ───────────────────────────────────────────────
  if (showDisqualify) {
    return (
      <div className="space-y-2 min-w-[220px]">
        <p className="text-xs font-medium text-gray-500">Reason for disqualification</p>
        <div className="grid grid-cols-1 gap-1">
          {REJECTION_REASONS.map(r => (
            <label key={r} className="flex items-center gap-2 text-xs text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={selectedReasons.has(r)}
                onChange={() => toggleReason(r)}
                className="rounded text-red-500"
              />
              {r}
            </label>
          ))}
          <label className="flex items-center gap-2 text-xs text-gray-700 mt-1">
            <input
              type="checkbox"
              checked={!!otherReason}
              onChange={() => setOtherReason(prev => prev ? '' : ' ')}
              className="rounded text-red-500"
            />
            Other:
            <input
              type="text"
              value={otherReason}
              onChange={e => setOtherReason(e.target.value)}
              placeholder="Specify…"
              className="flex-1 border-b border-gray-300 text-xs focus:outline-none focus:border-red-400 bg-transparent"
            />
          </label>
        </div>
        <div className="flex gap-2 pt-1">
          <button
            onClick={confirmDisqualify}
            disabled={advance.isPending || (selectedReasons.size === 0 && !otherReason.trim())}
            className="px-2.5 py-1 text-xs rounded-md bg-red-600 text-white hover:bg-red-700 disabled:opacity-50"
          >
            {advance.isPending ? '…' : 'Confirm'}
          </button>
          <button
            onClick={() => { setShowDisqualify(false); setSelectedReasons(new Set()); setOtherReason('') }}
            className="px-2.5 py-1 text-xs rounded-md border border-gray-300 text-gray-500 hover:bg-gray-50"
          >
            Cancel
          </button>
        </div>
      </div>
    )
  }

  // ── slot_booked + interview_done: Hired / Disqualified / Reschedule ──────
  if (mapping.stage === 'slot_booked' || mapping.stage === 'interview_done') {
    if (showReschedule) {
      return (
        <div className="flex flex-col gap-1.5 min-w-[200px]">
          <p className="text-xs text-gray-600 font-medium">Resend slot-selection link to candidate?</p>
          <p className="text-[10px] text-gray-400">Candidate picks from client's available slots on their portal.</p>
          <div className="flex gap-1.5">
            <button
              disabled={reschedule.isPending}
              onClick={() => reschedule.mutate()}
              className="flex-1 text-xs px-2 py-1.5 bg-orange-500 text-white rounded-lg hover:bg-orange-600 disabled:opacity-50 font-medium"
            >
              {reschedule.isPending ? '…' : '📤 Send Link'}
            </button>
            <button onClick={() => setShowReschedule(false)} className="text-xs px-2 py-1.5 border border-gray-200 rounded-lg text-gray-500 hover:bg-gray-50">
              Cancel
            </button>
          </div>
          {reschedule.isError && <p className="text-[10px] text-red-500">{(reschedule.error as any)?.response?.data?.detail || 'Failed'}</p>}
        </div>
      )
    }
    return (
      <div className="flex flex-wrap gap-1">
        <button
          disabled={advance.isPending}
          onClick={() => advance.mutate({ stage: 'placed' })}
          className="px-2.5 py-1 text-xs rounded-md font-medium bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
        >
          ✓ Hired
        </button>
        <button
          disabled={advance.isPending}
          onClick={() => setShowDisqualify(true)}
          className="px-2.5 py-1 text-xs rounded-md font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
        >
          ✗ Disqualified
        </button>
        <button
          onClick={() => setShowReschedule(true)}
          className="px-2.5 py-1 text-xs rounded-md font-medium bg-orange-50 text-orange-600 hover:bg-orange-100"
        >
          ✎ Reschedule
        </button>
      </div>
    )
  }

  // ── client_approval_pending: approve (Book Slot) or decline ─────────────
  if (mapping.stage === 'client_approval_pending') {
    return (
      <div className="flex flex-col gap-1">
        {!showSlotInput ? (
          <div className="flex flex-wrap gap-1">
            <button
              onClick={() => setShowSlotInput(true)}
              className="px-2 py-1 text-xs rounded-md font-medium bg-teal-50 text-teal-700 hover:bg-teal-100"
            >
              Approve + Book Slot
            </button>
            <button
              disabled={advance.isPending}
              onClick={() => setShowDisqualify(true)}
              className="px-2 py-1 text-xs rounded-md font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
            >
              Decline
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-1">
            <input
              type="datetime-local"
              className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-teal-400"
              value={slotDatetime}
              onChange={e => setSlotDatetime(e.target.value)}
            />
            <button
              disabled={!slotDatetime || advance.isPending}
              onClick={() => advance.mutate({ stage: 'slot_booked', interview_slot: slotDatetime })}
              className="text-xs px-2 py-1 bg-teal-600 text-white rounded hover:bg-teal-700 disabled:opacity-50"
            >
              {advance.isPending ? '…' : 'OK'}
            </button>
            <button onClick={() => setShowSlotInput(false)} className="text-xs text-gray-400 hover:text-gray-600">×</button>
          </div>
        )}
      </div>
    )
  }

  // ── interested stage: Book Slot (manual) ──────────────────────────────────
  if (mapping.stage === 'interested') {
    return (
      <div className="flex flex-col gap-1">
        {!showSlotInput ? (
          <button
            onClick={() => setShowSlotInput(true)}
            className="px-2 py-1 text-xs rounded-md font-medium bg-teal-50 text-teal-700 hover:bg-teal-100"
          >
            Book Slot
          </button>
        ) : (
          <div className="flex items-center gap-1">
            <input
              type="datetime-local"
              className="text-xs border border-gray-300 rounded px-2 py-1 focus:outline-none focus:ring-1 focus:ring-teal-400"
              value={slotDatetime}
              onChange={e => setSlotDatetime(e.target.value)}
            />
            <button
              disabled={!slotDatetime || advance.isPending}
              onClick={() => advance.mutate({ stage: 'slot_booked', interview_slot: slotDatetime })}
              className="text-xs px-2 py-1 bg-teal-600 text-white rounded hover:bg-teal-700 disabled:opacity-50"
            >
              {advance.isPending ? '…' : 'OK'}
            </button>
            <button onClick={() => setShowSlotInput(false)} className="text-xs text-gray-400 hover:text-gray-600">×</button>
          </div>
        )}
      </div>
    )
  }

  // ── intent_ask stage: simulate WA reply (runs full flow engine) ───────────
  if (mapping.stage === 'intent_ask') {
    return (
      <IntentAskButtons mappingId={mapping.id} clientId={clientId} />
    )
  }

  return null
}


function IntentAskButtons({ mappingId, clientId }: { mappingId: string; clientId: string }) {
  const qc = useQueryClient()
  const [result, setResult] = useState<string | null>(null)

  const simulate = useMutation({
    mutationFn: (intent: 'INTERESTED' | 'NOT_INTERESTED') =>
      simulateCandidateIntent(mappingId, intent),
    onSuccess: (_, intent) => {
      qc.invalidateQueries({ queryKey: ['mappings', clientId] })
      setResult(intent === 'INTERESTED' ? 'Flow triggered — check WhatsApp' : 'Marked not interested')
      setTimeout(() => setResult(null), 4000)
    },
  })

  if (result) {
    return <p className="text-xs text-green-600 font-medium">{result}</p>
  }

  return (
    <div className="flex flex-wrap gap-1">
      <button
        disabled={simulate.isPending}
        onClick={() => simulate.mutate('INTERESTED')}
        className="px-2 py-1 text-xs rounded-md font-medium bg-green-50 text-green-700 hover:bg-green-100 disabled:opacity-50"
        title="Runs the full flow: checks evaluation status, triggers RinggAI call or sends slot link"
      >
        {simulate.isPending ? '…' : '✓ Interested'}
      </button>
      <button
        disabled={simulate.isPending}
        onClick={() => simulate.mutate('NOT_INTERESTED')}
        className="px-2 py-1 text-xs rounded-md font-medium bg-red-50 text-red-600 hover:bg-red-100 disabled:opacity-50"
      >
        ✗ Not Interested
      </button>
    </div>
  )
}
