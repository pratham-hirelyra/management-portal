import { api } from './axios'

export const sendBatch = (clientId: string) =>
  api.post<{ data: { sent: number; batch_number: number; message?: string; sourcing_count_needed?: number } }>(
    `/outreach/${clientId}/send-batch`
  ).then(r => r.data.data)

export const rerunBatch = (clientId: string) =>
  api.post<{ data: { created: number; count: number; message?: string } }>(
    `/outreach/${clientId}/rerun-batch`
  ).then(r => r.data.data)

export interface ClientFeedbackCandidate {
  mapping_id: string
  stage: string
  interview_slot: string | null
  feedback: 'liked' | 'disliked' | null
  feedback_reason: string | null
  candidate_id: string
  name: string
  initials: string
  age_years: number | null
  current_location: string | null
  current_salary: number | null
  technical_score: number
  experience: string | null
  cv_url: string | null
  report_url: string | null
  photo_url: string | null
  labels: string[]
}

export interface ClientFeedbackData {
  client: { company_name: string; job_title: string }
  candidates: ClientFeedbackCandidate[]
}

export const getClientFeedbackPage = (token: string) =>
  api.get<{ data: ClientFeedbackData }>(`/client-feedback/${token}`).then(r => r.data.data)

export const submitClientFeedback = (
  token: string,
  mappingId: string,
  status: 'liked' | 'disliked',
  reason?: string
) =>
  api.post(`/client-feedback/${token}/candidate/${mappingId}`, { status, reason }).then(r => r.data)

export const reopenClientFeedback = (token: string, mappingId: string) =>
  api.post(`/client-feedback/${token}/candidate/${mappingId}/reopen`).then(r => r.data)

export interface CandidateFeedbackData {
  mapping_id: string
  feedback: 'join' | 'no_join' | null
  candidate_name: string
  company_name: string
  job_title: string
  job_location: string | null
}

export const getCandidateFeedbackPage = (token: string) =>
  api.get<{ data: CandidateFeedbackData }>(`/candidate-feedback/${token}`).then(r => r.data.data)

export const submitCandidateFeedback = (token: string, decision: 'join' | 'no_join') =>
  api.post(`/candidate-feedback/${token}`, { decision }).then(r => r.data)

// ── Client Portal ─────────────────────────────────────────────────────────────

export interface ApprovalCandidate {
  mapping_id: string
  stage: string
  candidate_id: string
  name: string
  initials: string
  age_years: number | null
  current_location: string | null
  current_salary: number | null
  ai_technical_score: number
  job_stability: string
  accounting_software: string | null
  notice_period: string | null
  cv_url: string | null
  evaluation_report_url: string | null
  photo_url: string | null
  labels: string[]
}

export interface FeedbackCandidate {
  mapping_id: string
  stage: string
  interview_slot: string | null
  interview_slot_label: string | null
  interview_done: boolean
  feedback: 'liked' | 'disliked' | null
  feedback_reason: string | null
  candidate_id: string
  name: string
  initials: string
  age_years: number | null
  current_location: string | null
  current_salary: number | null
  salary_vs_budget: number
  mms_score_pct: number
  ai_technical_score: number
  job_stability: string
  accounting_software: string | null
  notice_period: string | null
  cv_url: string | null
  evaluation_report_url: string | null
  photo_url: string | null
  labels: string[]
}

export interface InvitedCandidate {
  mapping_id: string
  name: string
  initials: string
  photo_url: string | null
  stage: string
  slot_sent_at: string | null
}

export interface ClientPortalData {
  client: { company_name: string; job_title: string; poc_name: string; stage: string; matching_state: string | null }
  has_recruiter_slots: boolean
  slot_labels: string[]
  active_tab: 'overview' | 'review' | 'feedback'
  overview: {
    sourced: number
    jd_sent: number
    replied: number
    passed_screen: number
    interested: number
    in_review: number
    invited: number
    interview_scheduled: number
    interview_done: number
    placed: number
  }
  activity_events: { type: string; at: string }[]
  approval_candidates: ApprovalCandidate[]
  invited_candidates: InvitedCandidate[]
  feedback_candidates: FeedbackCandidate[]
}

export const getClientPortal = (token: string) =>
  api.get<{ data: ClientPortalData }>(`/client-portal/${token}`).then(r => r.data.data)

export const submitSlotsFromPortal = (token: string, body: { client_name: string; slots: string[] }) =>
  api.post(`/client-portal/${token}/submit-slots`, body).then(r => r.data)

export const inviteCandidate = (token: string, mappingId: string) =>
  api.post(`/client-portal/${token}/invite/${mappingId}`).then(r => r.data)

export const rejectCandidateFromPortal = (token: string, mappingId: string, reason?: string) =>
  api.post(`/client-portal/${token}/reject/${mappingId}`, { reason }).then(r => r.data)

export const submitClientFeedbackFromPortal = (
  token: string,
  mappingId: string,
  status: 'liked' | 'disliked',
  reason?: string
) => api.post(`/client-feedback/${token}/candidate/${mappingId}`, { status, reason }).then(r => r.data)

export const reopenClientFeedbackFromPortal = (token: string, mappingId: string) =>
  api.post(`/client-feedback/${token}/candidate/${mappingId}/reopen`).then(r => r.data)
