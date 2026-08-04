import { api } from './axios'
import type { Ticket, TicketDetail, TicketTimelineEntry, TicketQueue, TicketCategory, TicketAnalytics } from '../types'

export interface TicketFilters {
  ce_id?: string
  queue_id?: string
  status?: string
  priority?: string
  category_id?: string
  assigned_to?: string
  source?: string
  search?: string
  date_from?: string
  date_to?: string
  tab?: 'unclaimed' | 'mine' | 'all' | 'resolved'
  page?: number
  page_size?: number
}

export const getTickets = (filters?: TicketFilters) =>
  api.get<{ data: Ticket[]; count: number; ok: boolean }>('/tickets', { params: filters }).then(r => r.data)

export const getTicket = (id: string) =>
  api.get<{ data: TicketDetail }>(`/tickets/${id}`).then(r => r.data.data)

export const getTicketTimeline = (id: string) =>
  api.get<{ data: TicketTimelineEntry[]; count: number }>(`/tickets/${id}/timeline`).then(r => r.data.data)

export interface TicketCreatePayload {
  phone: string
  channel: 'client' | 'candidate'
  queue_code: string
  category_code: string
  subject?: string
  priority?: string
  reason?: string
  client_id?: string
  candidate_id?: string
  mapping_id?: string
  created_by_ce_id?: string
}

export const createTicket = (body: TicketCreatePayload) =>
  api.post<{ data: Ticket }>('/tickets', body).then(r => r.data.data)

export const claimTicket = (id: string, ce_id: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/claim`, { ce_id }).then(r => r.data.data)

export const replyToTicket = (id: string, ce_id: string, text: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/reply`, { ce_id, text }).then(r => r.data.data)

export const addTicketNote = (id: string, ce_id: string, text: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/note`, { ce_id, text }).then(r => r.data.data)

export const reassignTicket = (id: string, to_ce_id: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/reassign`, { to_ce_id }).then(r => r.data.data)

export const resolveTicket = (id: string, ce_id: string, resolution_note?: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/resolve`, { ce_id, resolution_note }).then(r => r.data.data)

export const reopenTicket = (id: string, ce_id: string, reason?: string) =>
  api.post<{ data: Ticket }>(`/tickets/${id}/reopen`, { ce_id, reason }).then(r => r.data.data)

export const updateTicket = (id: string, body: { priority?: string; category_id?: string; subject?: string }) =>
  api.patch<{ data: Ticket }>(`/tickets/${id}`, body).then(r => r.data.data)

export const getTicketAnalytics = (params?: { queue_id?: string; date_from?: string; date_to?: string }) =>
  api.get<{ data: TicketAnalytics }>('/tickets/analytics', { params }).then(r => r.data.data)

// ── Admin: queues / categories / queue access ─────────────────────────────────

export const getTicketQueues = () =>
  api.get<{ data: TicketQueue[]; count: number }>('/admin/ticket-queues').then(r => r.data.data)

export const createTicketQueue = (body: { name: string; code: string; description?: string; is_active?: boolean }) =>
  api.post<{ data: TicketQueue }>('/admin/ticket-queues', body).then(r => r.data.data)

export const updateTicketQueue = (id: string, body: Partial<Pick<TicketQueue, 'name' | 'code' | 'description' | 'is_active'>>) =>
  api.patch<{ data: TicketQueue }>(`/admin/ticket-queues/${id}`, body).then(r => r.data.data)

export const deleteTicketQueue = (id: string) =>
  api.delete(`/admin/ticket-queues/${id}`).then(r => r.data)

export const getTicketCategories = (queue_id?: string) =>
  api.get<{ data: TicketCategory[]; count: number }>('/admin/ticket-categories', { params: queue_id ? { queue_id } : undefined }).then(r => r.data.data)

export const createTicketCategory = (body: { queue_id: string; name: string; code: string; is_active?: boolean }) =>
  api.post<{ data: TicketCategory }>('/admin/ticket-categories', body).then(r => r.data.data)

export const updateTicketCategory = (id: string, body: Partial<Pick<TicketCategory, 'name' | 'code' | 'is_active'>>) =>
  api.patch<{ data: TicketCategory }>(`/admin/ticket-categories/${id}`, body).then(r => r.data.data)

export const deleteTicketCategory = (id: string) =>
  api.delete(`/admin/ticket-categories/${id}`).then(r => r.data)

export const getExecutiveQueueAccess = (ceId: string) =>
  api.get<{ data: string[] }>(`/admin/ces/${ceId}/queue-access`).then(r => r.data.data)

export const setExecutiveQueueAccess = (ceId: string, queue_ids: string[]) =>
  api.put<{ data: string[] }>(`/admin/ces/${ceId}/queue-access`, { queue_ids }).then(r => r.data.data)
