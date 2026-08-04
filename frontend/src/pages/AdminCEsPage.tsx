import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'
import {
  getTicketQueues, createTicketQueue, updateTicketQueue, deleteTicketQueue,
  getTicketCategories, createTicketCategory, updateTicketCategory, deleteTicketCategory,
  getExecutiveQueueAccess, setExecutiveQueueAccess, getTicketAnalytics,
} from '../api/tickets'
import type { TicketQueue, TicketCategory } from '../types'

interface CE {
  id: string
  name: string
  phone: string | null
  pin: string | null
  tier: 'top_end' | 'bottom_end'
  daily_target: number
  is_active: boolean
  today_pending: number
  claimed_today: number
  total_called: number
  positive: number
  negative: number
  wrong_poc: number
  onboarded: number
  open_tickets: number
  resolved_tickets: number
  created_at: string
}

const BLANK: Omit<CE, 'id' | 'today_pending' | 'claimed_today' | 'total_called' | 'positive' | 'negative' | 'wrong_poc' | 'onboarded' | 'open_tickets' | 'resolved_tickets' | 'created_at'> = {
  name: '', phone: '', pin: '', tier: 'top_end', daily_target: 20, is_active: true,
}

function SummaryTile({ label, value, cls }: { label: string; value: number | string; cls: string }) {
  return (
    <div className="bg-white rounded-xl border border-gray-200 px-4 py-3 text-center">
      <p className={`text-2xl font-bold ${cls}`}>{value}</p>
      <p className="text-[11px] text-gray-500 mt-0.5 uppercase tracking-wide">{label}</p>
    </div>
  )
}

function CEModal({ form, setForm, onSave, onClose, saving }: {
  form: any; setForm: (f: any) => void
  onSave: () => void; onClose: () => void; saving: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 max-h-[90vh] overflow-y-auto">
        <h3 className="text-base font-bold text-gray-900 mb-5">
          {form.id ? 'Edit Customer Executive' : 'Add Customer Executive'}
        </h3>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Name *</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.name} onChange={e => setForm((p: any) => ({ ...p, name: e.target.value }))}
              placeholder="Full name" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Phone</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.phone || ''} onChange={e => setForm((p: any) => ({ ...p, phone: e.target.value }))}
              placeholder="+91 XXXXX XXXXX" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Tier</label>
            <select className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.tier} onChange={e => setForm((p: any) => ({ ...p, tier: e.target.value }))}>
              <option value="top_end">Top End</option>
              <option value="bottom_end">Bottom End</option>
            </select>
            <p className="text-[10px] text-gray-400 mt-1">Top end = shared, claim-based lead-conversion queue. Bottom end = the ticket desk — grant queue access after saving.</p>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Daily Target</label>
            <input type="number" min={1} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.daily_target}
              onChange={e => setForm((p: any) => ({ ...p, daily_target: parseInt(e.target.value) || 1 }))} />
            <p className="text-[10px] text-gray-400 mt-1">Top end only — a minimum target to hit, not a claim cap. Claimed-today vs. this target is shown on the executive's page.</p>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Login PIN</label>
            <input type="password" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.pin || ''} placeholder="Set a PIN for CE login"
              onChange={e => setForm((p: any) => ({ ...p, pin: e.target.value }))} />
            <p className="text-[10px] text-gray-400 mt-1">Required to access the executive's page</p>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="ce_is_active" checked={form.is_active}
              onChange={e => setForm((p: any) => ({ ...p, is_active: e.target.checked }))}
              className="accent-blue-600 w-4 h-4" />
            <label htmlFor="ce_is_active" className="text-sm text-gray-700">Active</label>
          </div>
        </div>
        <div className="flex gap-3 mt-6">
          <button type="button" onClick={onClose}
            className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button type="button" onClick={onSave} disabled={saving || !form.name.trim()}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200 disabled:text-gray-400">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function ReleaseModal({ ce, onClose, onSave, saving }: {
  ce: CE; onClose: () => void; onSave: () => void; saving: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">Release {ce.name}'s claims</p>
        <p className="text-xs text-gray-500 mt-1 mb-4">
          Releases {ce.today_pending} still-uncalled claim{ce.today_pending !== 1 ? 's' : ''} back to the shared
          queue, where any active top_end executive can claim them. Call history stays with {ce.name}.
        </p>
        <div className="flex gap-3">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={onSave} disabled={saving}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200">
            {saving ? 'Releasing…' : 'Release'}
          </button>
        </div>
      </div>
    </div>
  )
}

function QueueModal({ form, setForm, onSave, onClose, saving }: {
  form: any; setForm: (f: any) => void; onSave: () => void; onClose: () => void; saving: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-gray-900 mb-4">{form.id ? 'Edit Queue' : 'Add Queue'}</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Name *</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.name} onChange={e => setForm((p: any) => ({ ...p, name: e.target.value }))} placeholder="e.g. Client Queue" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Code *</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.code} onChange={e => setForm((p: any) => ({ ...p, code: e.target.value }))} placeholder="e.g. client_queue" disabled={!!form.id} />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Description</label>
            <textarea rows={2} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.description || ''} onChange={e => setForm((p: any) => ({ ...p, description: e.target.value }))} />
          </div>
        </div>
        <div className="flex gap-3 mt-5">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          <button onClick={onSave} disabled={saving || !form.name.trim() || !form.code.trim()}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function CategoryModal({ form, setForm, onSave, onClose, saving }: {
  form: any; setForm: (f: any) => void; onSave: () => void; onClose: () => void; saving: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <h3 className="text-sm font-bold text-gray-900 mb-4">{form.id ? 'Edit Category' : 'Add Category'}</h3>
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Name *</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.name} onChange={e => setForm((p: any) => ({ ...p, name: e.target.value }))} placeholder="e.g. Invoice" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Code *</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.code} onChange={e => setForm((p: any) => ({ ...p, code: e.target.value }))} placeholder="e.g. invoice" disabled={!!form.id} />
          </div>
        </div>
        <div className="flex gap-3 mt-5">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          <button onClick={onSave} disabled={saving || !form.name.trim() || !form.code.trim()}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function QueueAccessModal({ ce, queues, onClose, onSave, saving }: {
  ce: CE; queues: TicketQueue[]; onClose: () => void; onSave: (queueIds: string[]) => void; saving: boolean
}) {
  const { data: current = [] } = useQuery({
    queryKey: ['ce-queue-access', ce.id],
    queryFn: () => getExecutiveQueueAccess(ce.id),
  })
  const [selected, setSelected] = useState<string[] | null>(null)
  const active = selected ?? current

  const toggle = (id: string) => {
    const base = selected ?? current
    setSelected(base.includes(id) ? base.filter(x => x !== id) : [...base, id])
  }

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">Queue access — {ce.name}</p>
        <p className="text-xs text-gray-500 mt-1 mb-3">Which ticket queues can this executive see and claim from?</p>
        <div className="flex flex-col gap-2 max-h-64 overflow-y-auto">
          {queues.map(q => (
            <label key={q.id} className="flex items-center gap-2 text-sm text-gray-700">
              <input type="checkbox" checked={active.includes(q.id)} onChange={() => toggle(q.id)} className="accent-blue-600 w-4 h-4" />
              {q.name}
            </label>
          ))}
        </div>
        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">Cancel</button>
          <button onClick={() => onSave(active)} disabled={saving}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

function TicketQueuesTab() {
  const qc = useQueryClient()
  const [queueForm, setQueueForm] = useState<any>(null)
  const [categoryForm, setCategoryForm] = useState<any>(null)

  const { data: queues = [], isLoading } = useQuery({ queryKey: ['ticket-queues'], queryFn: getTicketQueues })
  const { data: categories = [] } = useQuery({ queryKey: ['ticket-categories'], queryFn: () => getTicketCategories() })
  const { data: analytics } = useQuery({ queryKey: ['ticket-analytics'], queryFn: () => getTicketAnalytics() })

  const saveQueue = useMutation({
    mutationFn: (form: any) => form.id ? updateTicketQueue(form.id, form) : createTicketQueue(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ticket-queues'] }); setQueueForm(null) },
  })
  const deleteQueue = useMutation({
    mutationFn: (id: string) => deleteTicketQueue(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ticket-queues'] }),
    onError: (e: any) => alert(e?.response?.data?.detail ?? 'Could not delete queue'),
  })
  const saveCategory = useMutation({
    mutationFn: (form: any) => form.id ? updateTicketCategory(form.id, form) : createTicketCategory(form),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ticket-categories'] }); setCategoryForm(null) },
  })
  const deleteCategory = useMutation({
    mutationFn: (id: string) => deleteTicketCategory(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['ticket-categories'] }),
    onError: (e: any) => alert(e?.response?.data?.detail ?? 'Could not delete category'),
  })

  const categoriesByQueue = (queueId: string): TicketCategory[] => categories.filter(c => c.queue_id === queueId)

  return (
    <>
      {analytics && (
        <div className="grid grid-cols-4 gap-3 mb-6">
          <SummaryTile label="Open Tickets" value={analytics.open_count} cls="text-amber-600" />
          <SummaryTile label="Resolved Today" value={analytics.resolved_today} cls="text-green-600" />
          <SummaryTile label="Avg First Response" value={analytics.avg_first_response_minutes != null ? `${Math.round(analytics.avg_first_response_minutes)}m` : '—'} cls="text-gray-800" />
          <SummaryTile label="Avg Resolution" value={analytics.avg_resolution_minutes != null ? `${Math.round(analytics.avg_resolution_minutes)}m` : '—'} cls="text-gray-800" />
        </div>
      )}

      <div className="bg-white rounded-xl border border-gray-200">
        {isLoading ? (
          <div className="py-12 text-center text-gray-400 text-sm">Loading…</div>
        ) : queues.length === 0 ? (
          <div className="py-12 text-center text-gray-400 text-sm">No ticket queues yet.</div>
        ) : (
          <div className="divide-y divide-gray-100">
            {queues.map(q => (
              <div key={q.id} className="p-4">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{q.name} <span className="text-xs text-gray-400 font-normal">({q.code})</span></p>
                    {q.description && <p className="text-xs text-gray-500 mt-0.5">{q.description}</p>}
                  </div>
                  <div className="flex gap-3 shrink-0">
                    <button onClick={() => setQueueForm({ ...q })} className="text-xs text-blue-600 hover:underline">Edit</button>
                    <button onClick={() => { if (confirm(`Delete queue "${q.name}"?`)) deleteQueue.mutate(q.id) }}
                      className="text-xs text-red-400 hover:text-red-600 hover:underline">Delete</button>
                  </div>
                </div>
                <div className="flex flex-wrap gap-1.5 mt-2">
                  {categoriesByQueue(q.id).map(c => (
                    <span key={c.id} className="group inline-flex items-center gap-1 text-[11px] bg-gray-100 text-gray-600 px-2 py-0.5 rounded-full">
                      {c.name}
                      <button onClick={() => setCategoryForm({ ...c })} className="text-gray-400 hover:text-blue-600">✎</button>
                      <button onClick={() => { if (confirm(`Delete category "${c.name}"?`)) deleteCategory.mutate(c.id) }} className="text-gray-400 hover:text-red-600">✕</button>
                    </span>
                  ))}
                  <button onClick={() => setCategoryForm({ queue_id: q.id, name: '', code: '', is_active: true })}
                    className="text-[11px] text-blue-600 hover:underline px-1">+ Category</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <button onClick={() => setQueueForm({ name: '', code: '', description: '', is_active: true })}
        className="mt-4 px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700">
        + Add Queue
      </button>

      {queueForm && (
        <QueueModal form={queueForm} setForm={setQueueForm} saving={saveQueue.isPending}
          onClose={() => setQueueForm(null)} onSave={() => saveQueue.mutate(queueForm)} />
      )}
      {categoryForm && (
        <CategoryModal form={categoryForm} setForm={setCategoryForm} saving={saveCategory.isPending}
          onClose={() => setCategoryForm(null)} onSave={() => saveCategory.mutate(categoryForm)} />
      )}
    </>
  )
}

export default function AdminCEsPage() {
  const qc = useQueryClient()
  const [adminTab, setAdminTab] = useState<'executives' | 'queues'>('executives')
  const [modalForm, setModalForm] = useState<any>(null)
  const [releaseFrom, setReleaseFrom] = useState<CE | null>(null)
  const [queueAccessFor, setQueueAccessFor] = useState<CE | null>(null)

  const { data: ces = [], isLoading } = useQuery({
    queryKey: ['admin-ces'],
    queryFn: () => api.get('/admin/ces').then(r => r.data.data as CE[]),
  })
  const { data: allQueues = [] } = useQuery({ queryKey: ['ticket-queues'], queryFn: getTicketQueues })

  const saveMutation = useMutation({
    mutationFn: (form: any) =>
      form.id ? api.patch(`/admin/ces/${form.id}`, form).then(r => r.data)
              : api.post('/admin/ces', form).then(r => r.data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-ces'] }); setModalForm(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/ces/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-ces'] }),
    onError: (e: any) => alert(e?.response?.data?.detail ?? 'Could not delete executive'),
  })

  const releaseMutation = useMutation({
    mutationFn: (ceId: string) => api.post(`/admin/ces/${ceId}/release-pending`).then(r => r.data),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['admin-ces'] })
      setReleaseFrom(null)
      alert(`Released ${res.data.released} client(s) back to the shared queue.`)
    },
  })

  const queueAccessMutation = useMutation({
    mutationFn: ({ ceId, queueIds }: { ceId: string; queueIds: string[] }) => setExecutiveQueueAccess(ceId, queueIds),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['ce-queue-access'] }); setQueueAccessFor(null) },
  })

  const toggleActive = (ce: CE) => saveMutation.mutate({ id: ce.id, is_active: !ce.is_active })

  const totals = ces.reduce((a, ce) => ({
    today_pending: a.today_pending + ce.today_pending,
    total_called: a.total_called + ce.total_called,
    positive: a.positive + ce.positive,
    negative: a.negative + ce.negative,
    wrong_poc: a.wrong_poc + ce.wrong_poc,
    onboarded: a.onboarded + ce.onboarded,
  }), { today_pending: 0, total_called: 0, positive: 0, negative: 0, wrong_poc: 0, onboarded: 0 })

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Customer Executives</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            {adminTab === 'executives'
              ? 'Top end: daily lead-conversion call queue, auto-assigned by capacity. Bottom end: the ticket desk.'
              : 'Ticket queues and categories — configure what bottom-end executives can see and claim.'}
          </p>
        </div>
        {adminTab === 'executives' && (
          <button
            onClick={() => setModalForm({ ...BLANK })}
            className="px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700"
          >
            + Add Executive
          </button>
        )}
      </div>

      <div className="flex bg-white rounded-xl border border-gray-200 p-1 gap-1 mb-6 max-w-xs">
        <button onClick={() => setAdminTab('executives')}
          className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-colors ${adminTab === 'executives' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
          Executives
        </button>
        <button onClick={() => setAdminTab('queues')}
          className={`flex-1 py-2 rounded-lg text-xs font-semibold transition-colors ${adminTab === 'queues' ? 'bg-gray-900 text-white' : 'text-gray-500'}`}>
          Ticket Queues
        </button>
      </div>

      {adminTab === 'queues' ? (
        <TicketQueuesTab />
      ) : (
        <>
          {!isLoading && ces.length > 0 && (
            <div className="grid grid-cols-6 gap-3 mb-6">
              <SummaryTile label="Today's Queue" value={totals.today_pending} cls="text-amber-600" />
              <SummaryTile label="Total Called" value={totals.total_called} cls="text-gray-800" />
              <SummaryTile label="Positive" value={totals.positive} cls="text-green-600" />
              <SummaryTile label="Negative" value={totals.negative} cls="text-red-600" />
              <SummaryTile label="Wrong POC" value={totals.wrong_poc} cls="text-orange-600" />
              <SummaryTile label="Onboarded" value={totals.onboarded} cls="text-blue-600" />
            </div>
          )}

          <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
            {isLoading ? (
              <div className="py-12 text-center text-gray-400 text-sm">Loading…</div>
            ) : ces.length === 0 ? (
              <div className="py-12 text-center text-gray-400 text-sm">No customer executives yet.</div>
            ) : (
              <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    {['Name', 'Tier', 'Contact', 'Claimed Today / Target', 'Called', 'Positive', 'Negative', 'Wrong POC', 'Onboarded', 'Open Tickets', 'Resolved Tickets', 'Status', ''].map(h => (
                      <th key={h} className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider whitespace-nowrap">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {ces.map(ce => (
                    <tr key={ce.id} className="hover:bg-gray-50">
                      <td className="px-3 py-3 font-medium text-gray-900 whitespace-nowrap">{ce.name}</td>
                      <td className="px-3 py-3">
                        <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${ce.tier === 'top_end' ? 'bg-indigo-100 text-indigo-700' : 'bg-gray-100 text-gray-600'}`}>
                          {ce.tier === 'top_end' ? 'Top End' : 'Bottom End'}
                        </span>
                      </td>
                      <td className="px-3 py-3 text-xs text-gray-600 whitespace-nowrap">{ce.phone || <span className="text-gray-300">—</span>}</td>
                      <td className="px-3 py-3">
                        {ce.tier === 'top_end' ? (
                          <div className="flex items-center gap-2">
                            <span className={`text-sm font-semibold whitespace-nowrap ${ce.claimed_today >= ce.daily_target ? 'text-green-600' : 'text-gray-700'}`}>
                              {ce.claimed_today}/{ce.daily_target}
                            </span>
                            <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                              <div
                                className={`h-full rounded-full transition-all ${
                                  ce.claimed_today >= ce.daily_target ? 'bg-green-500' :
                                  ce.claimed_today / ce.daily_target >= 0.75 ? 'bg-amber-400' : 'bg-blue-500'
                                }`}
                                style={{ width: `${Math.min(100, (ce.claimed_today / ce.daily_target) * 100)}%` }}
                              />
                            </div>
                          </div>
                        ) : <span className="text-gray-300">—</span>}
                      </td>
                      {ce.tier === 'top_end' ? (
                        <>
                          <td className="px-3 py-3 text-sm text-gray-700 text-center">{ce.total_called}</td>
                          <td className="px-3 py-3 text-sm text-green-600 font-semibold text-center">{ce.positive}</td>
                          <td className="px-3 py-3 text-sm text-red-600 font-semibold text-center">{ce.negative}</td>
                          <td className="px-3 py-3 text-sm text-orange-600 font-semibold text-center">{ce.wrong_poc}</td>
                          <td className="px-3 py-3 text-sm text-blue-600 font-semibold text-center">{ce.onboarded}</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                        </>
                      ) : (
                        <>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-center text-gray-300">—</td>
                          <td className="px-3 py-3 text-sm text-amber-600 font-semibold text-center">{ce.open_tickets}</td>
                          <td className="px-3 py-3 text-sm text-green-600 font-semibold text-center">{ce.resolved_tickets}</td>
                        </>
                      )}
                      <td className="px-3 py-3">
                        <button onClick={() => toggleActive(ce)}
                          className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${ce.is_active ? 'bg-blue-600' : 'bg-gray-300'}`}>
                          <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ce.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                        </button>
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex gap-3">
                          <button onClick={() => window.open(`/ce/${ce.id}`, '_blank')} className="text-xs px-2.5 py-1 rounded-lg bg-indigo-50 border border-indigo-200 text-indigo-700 font-medium hover:bg-indigo-100 transition-colors">Open Page ↗</button>
                          {ce.tier === 'bottom_end' && (
                            <button onClick={() => setQueueAccessFor(ce)} className="text-xs text-teal-600 hover:underline">Queues</button>
                          )}
                          {ce.today_pending > 0 && (
                            <button onClick={() => setReleaseFrom(ce)} className="text-xs text-amber-600 hover:underline">Release</button>
                          )}
                          <button onClick={() => setModalForm({ ...ce })} className="text-xs text-blue-600 hover:underline">Edit</button>
                          <button onClick={() => { if (confirm(`Delete ${ce.name}?`)) deleteMutation.mutate(ce.id) }}
                            className="text-xs text-red-400 hover:text-red-600 hover:underline">Delete</button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              </div>
            )}
          </div>
        </>
      )}

      {modalForm && (
        <CEModal form={modalForm} setForm={setModalForm} saving={saveMutation.isPending}
          onClose={() => setModalForm(null)} onSave={() => saveMutation.mutate(modalForm)} />
      )}

      {releaseFrom && (
        <ReleaseModal ce={releaseFrom} saving={releaseMutation.isPending}
          onClose={() => setReleaseFrom(null)}
          onSave={() => releaseMutation.mutate(releaseFrom.id)} />
      )}

      {queueAccessFor && (
        <QueueAccessModal ce={queueAccessFor} queues={allQueues} saving={queueAccessMutation.isPending}
          onClose={() => setQueueAccessFor(null)}
          onSave={(queueIds) => queueAccessMutation.mutate({ ceId: queueAccessFor.id, queueIds })} />
      )}
    </div>
  )
}
