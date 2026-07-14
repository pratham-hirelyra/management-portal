import { api } from './axios'

export interface ReachoutQueueItem {
  id: string
  company_name: string | null
  segment: string | null
  stage: string
  outreach_step: number
  last_outreach_at: string | null
  next_due_at: string | null
  is_overdue: boolean
  poc_phone: string | null
}

export const getReachoutQueue = () =>
  api.get<{ data: ReachoutQueueItem[]; count: number }>('/cron/reachout-queue')
    .then(r => r.data.data)

export const sendNextReachout = (clientId: string) =>
  api.post<{ data: { sent: number; step: number }; ok: boolean }>(
    `/cron/reachout-queue/${clientId}/send-next`
  ).then(r => r.data.data)

export const bulkSendReachout = (clientIds: string[]) =>
  api.post<{ data: { sent: number; failed: number; skipped: number }; ok: boolean }>(
    '/cron/reachout-queue/bulk-send',
    { client_ids: clientIds }
  ).then(r => r.data.data)
