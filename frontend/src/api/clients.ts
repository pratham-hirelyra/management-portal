import { api } from './axios'
import type { Client, ClientStage, PipelineEvent, ClientAICall, AIVoiceReachoutClient, ClientComment } from '../types'

export const getClients = (stage?: string, search?: string, segment?: string, is_recruiter?: boolean, page?: number, page_size?: number) =>
  api.get<{ data: Client[]; count: number; total: number }>('/clients', {
    params: {
      ...(stage        ? { stage }        : {}),
      ...(search       ? { search }       : {}),
      ...(segment      ? { segment }      : {}),
      ...(is_recruiter !== undefined ? { is_recruiter } : {}),
      ...(page         ? { page }         : {}),
      ...(page_size    ? { page_size }    : {}),
    },
  }).then(r => r.data)

export const importCaLeads = (csv: string) =>
  api.post<{ data: { inserted: number; skipped: number; errors: number; error_lines: { line: number; text: string; error: string }[] } }>(
    '/clients/ca-leads-import', { csv }
  ).then(r => r.data.data)

export const getClient = (id: string) =>
  api.get<{ data: Client }>(`/clients/${id}`).then(r => r.data.data)

export const createClient = (body: Partial<Client> & { initial_stage?: string; agreement_text?: string }) =>
  api.post<{ data: Client }>('/clients', body).then(r => r.data.data)

export const updateClient = (id: string, body: Partial<Client>) =>
  api.patch<{ data: Client }>(`/clients/${id}`, body).then(r => r.data.data)

export const deleteClient = (id: string) =>
  api.delete(`/clients/${id}`).then(r => r.data)

export const updateClientStage = (id: string, stage: ClientStage, note?: string) =>
  api.patch(`/clients/${id}/stage`, { to_stage: stage, note }).then(r => r.data)

export const getClientTimeline = (id: string) =>
  api.get<{ data: PipelineEvent[] }>(`/clients/${id}/timeline`).then(r => r.data.data)

export const getClientAICalls = (id: string) =>
  api.get<{ data: ClientAICall[] }>(`/clients/${id}/ai-calls`).then(r => r.data.data)

export const getStuckClients = (days = 7) =>
  api.get<{ data: Client[] }>('/clients/stuck', { params: { days } }).then(r => r.data.data)

export const bulkScrape = (client_ids: string[], scraper_type: string) =>
  api.post('/clients/bulk-scrape', { client_ids, scraper_type }).then(r => r.data)

export const getAIVoiceReachoutClients = () =>
  api.get<{ data: AIVoiceReachoutClient[]; count: number }>('/clients/ai-voice-reachout').then(r => r.data)

export const bulkAIVoiceReachout = (client_ids: string[]) =>
  api.post<{ data: { client_id: string; call_id?: string; phone?: string; ok: boolean; error?: string }[]; count: number; ok: boolean }>(
    '/clients/bulk-ai-voice-reachout', { client_ids }
  ).then(r => r.data)

export const getFunnelStuckReachoutClients = () =>
  api.get<{ data: AIVoiceReachoutClient[]; count: number }>('/clients/funnel-stuck-reachout').then(r => r.data)

export const bulkFunnelStuckReachout = (client_ids: string[]) =>
  api.post<{ data: { client_id: string; call_id?: string; phone?: string; ok: boolean; error?: string }[]; count: number; ok: boolean }>(
    '/clients/bulk-funnel-stuck-reachout', { client_ids }
  ).then(r => r.data)

export const setRecruiterSlots = (
  id: string,
  slots: Array<{ id?: string; label: string; iso?: string }>
) => api.patch<{ data: Array<{ id: string; label: string; iso: string }> }>(`/clients/${id}/recruiter-slots`, { slots }).then(r => r.data.data)

export const importClients = (sheet_url: string) =>
  api.post<{ inserted: number; updated: number; skipped: number; errors: { row: number; company: string; error: string }[]; ok: boolean }>('/clients/import', { sheet_url }).then(r => r.data)

export const getLockedCandidates = (id: string) =>
  api.get<{ data: { id: string; name: string }[]; count: number }>(`/clients/${id}/locked-candidates`).then(r => r.data.data)

export const agreePhone = (id: string, phone: string) =>
  api.patch<{ data: Client; ok: boolean }>(`/clients/${id}/agree-phone`, { phone }).then(r => r.data.data)

export const sendAgreement = (id: string) =>
  api.post<{ data: Client; ok: boolean }>(`/clients/${id}/send-agreement`).then(r => r.data.data)

export const getClientComments = (id: string) =>
  api.get<{ data: ClientComment[]; count: number }>(`/clients/${id}/comments`).then(r => r.data.data)

export const addClientComment = (
  id: string,
  body: { comment_text?: string; experience_criteria?: string; created_by?: string }
) =>
  api.post<{ data: ClientComment }>(`/clients/${id}/comments`, body).then(r => r.data.data)

export const deleteClientComment = (id: string, commentId: string) =>
  api.delete(`/clients/${id}/comments/${commentId}`).then(r => r.data)
