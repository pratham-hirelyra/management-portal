import { api } from './axios'
import type { Candidate } from '../types'

interface CandidateFilters {
  evaluation_status?: string
  industry?: string
  search?: string
  is_active?: boolean
  page?: number
  page_size?: number
}

export const getCandidates = (filters?: CandidateFilters) =>
  api.get<{ data: Candidate[]; count: number; total: number }>('/candidates', { params: filters })
    .then(r => ({ candidates: r.data.data, total: r.data.total }))

export const getCandidate = (id: string) =>
  api.get<{ data: Candidate }>(`/candidates/${id}`).then(r => r.data.data)

export interface CandidateOverridePayload {
  phone?: string | null
  is_active?: boolean
  dnd_until?: string | null
  current_salary?: number | null
  current_location?: string | null
  working_radius?: number | null
  work_preference?: string | null
  job_type?: string | null
  gst?: string | null
  tds?: string | null
  tools?: string | null
  technical_evaluation_score?: number | null
  total_score?: number | null
  recruiter_notes?: string | null
  notes?: string | null
  labels?: string[] | null
  photo_url?: string | null
  religion?: string | null
}

export const upsertCandidateOverride = (id: string, body: CandidateOverridePayload) =>
  api.patch<{ data: Candidate }>(`/candidates/${id}/override`, body).then(r => r.data.data)

export interface ImportResult {
  inserted: number
  updated: number
  skipped: number
  errors: { row: number; phone: string; error: string }[]
}

export const importFromSheet = (sheet_url: string) =>
  api.post<ImportResult & { ok: boolean }>('/candidates/import', { sheet_url }).then(r => r.data)

export const deleteCandidate = (id: string) =>
  api.delete(`/candidates/${id}`).then(r => r.data)

export interface CandidateCreatePayload {
  name: string
  phone: string
  age_years?: number | null
  gender?: string | null
  religion?: string | null
  email?: string | null
  experience?: string | null
  cv_url?: string | null
  source?: string | null
  current_location?: string | null
  working_radius?: number | null
  current_salary?: number | null
  expected_salary?: number | null
  job_type?: string | null
  industry?: string | null
  category?: string | null
  work_preference?: string | null
}

export const createCandidate = (body: CandidateCreatePayload) =>
  api.post<{ data: Candidate; ok: boolean }>('/candidates', body).then(r => r.data.data)

export const triggerAICall = (id: string, retry: boolean = true) =>
  api.post<{ data: unknown; ok: boolean }>(`/candidates/${id}/trigger-ai-call`, null, { params: { retry } }).then(r => r.data)
