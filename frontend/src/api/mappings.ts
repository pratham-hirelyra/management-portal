import { api } from './axios'
import type { Mapping, MappingStage } from '../types'

export const getMappings = (clientId: string) =>
  api.get<{ data: Mapping[] }>(`/clients/${clientId}/mappings`).then(r => r.data.data)

export const createMapping = (clientId: string, body: { candidate_id: string; match_score?: number; notes?: string }) =>
  api.post<{ data: Mapping }>(`/clients/${clientId}/mappings`, body).then(r => r.data.data)

export const deleteMapping = (clientId: string, mappingId: string) =>
  api.delete(`/clients/${clientId}/mappings/${mappingId}`).then(r => r.data)

export const updateMappingStage = (
  clientId: string,
  mappingId: string,
  stage: MappingStage,
  extra?: { interview_slot?: string; notes?: string }
) =>
  api.patch(`/clients/${clientId}/mappings/${mappingId}/stage`, { stage, ...extra }).then(r => r.data)

export const modifyInterviewSlot = (clientId: string, mappingId: string, interviewSlot: string) =>
  api.post(`/clients/${clientId}/mappings/${mappingId}/modify-slot`, { interview_slot: interviewSlot }).then(r => r.data)

export const autoMatchCandidates = (clientId: string, options: { include_muslim: boolean }) =>
  api.post<{ data: Mapping[]; count: number }>(`/clients/${clientId}/mappings/auto`, options).then(r => r.data)

export interface FilterPreviewCandidate {
  id: string
  name: string | null
  category: string | null
  current_location: string | null
  current_salary: number | null
  technical_evaluation_score: number | null
  mms_score: number
  distance_km: number | null
  score_breakdown: Record<string, { score: number; max: number }> | null
  is_locked: boolean
  locked_for_company: string | null
  active_positions: number
}

export const getFilterPreview = (clientId: string) =>
  api.get<{ data: FilterPreviewCandidate[]; count: number }>(`/clients/${clientId}/mappings/filter-preview`).then(r => r.data)

export interface PoolSummary {
  locked_count: number
  free_count: number
  locked_by_other: number
  total_count: number
  pool_status: 'proceed' | 'schedule_message' | 'trigger_sourcing'
  earliest_unlock: string | null
}

export const getPoolSummary = (clientId: string) =>
  api.get<{ data: PoolSummary }>(`/matching/${clientId}/pool`).then(r => r.data.data)
