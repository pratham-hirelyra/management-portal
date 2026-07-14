import { api } from './axios'
import type { WhatsAppMessage, WATemplate } from '../types'

export const getPendingMessages = () =>
  api.get<{ data: WhatsAppMessage[] }>('/whatsapp/pending').then(r => r.data.data)

export const approveMessage = (messageId: string) =>
  api.post(`/whatsapp/approve/${messageId}`).then(r => r.data)

export const getTemplates = (type: 'client' | 'candidate' = 'client') =>
  api.get<{ data: WATemplate[] }>('/whatsapp/templates', { params: { type } }).then(r => r.data.data)

export const sendClientReachout = (
  clientId: string,
  body: { template_name: string; lang: string; variables: string[]; header_url?: string; header_type?: string }
) => api.post(`/whatsapp/send/client-reachout/${clientId}`, body).then(r => r.data)

export const sendClientAgreement = (clientId: string) =>
  api.post(`/whatsapp/send/client-agreement/${clientId}`).then(r => r.data)

export const sendClientBulkReachout = (
  client_ids: string[],
  body: { template_name: string; lang: string; variables: string[]; header_url?: string; header_type?: string }
) => api.post('/whatsapp/send/client-bulk-reachout', { client_ids, ...body }).then(r => r.data)

export const sendCandidateIntent = (
  mappingId: string,
  body: { template_name: string; lang: string; variables: string[] }
) => api.post(`/whatsapp/send/candidate-intent/${mappingId}`, body).then(r => r.data)

export const sendCandidateBulk = (mapping_ids: string[], message_text: string) =>
  api.post('/whatsapp/send/candidate-bulk', { mapping_ids, message_text }).then(r => r.data)

export const sendCandidateIntentBulk = (mapping_ids: string[]) =>
  api.post<{ data: { sent: number }; ok: boolean }>('/whatsapp/send/candidate-intent-bulk', { mapping_ids }).then(r => r.data)

export const sendClientAvailability = (clientId: string) =>
  api.post(`/whatsapp/send/client-availability/${clientId}`).then(r => r.data)

export const simulateCandidateIntent = (mappingId: string, intent: 'INTERESTED' | 'NOT_INTERESTED') =>
  api.post('/whatsapp/intent-callback', { mapping_id: mappingId, intent }).then(r => r.data)
