import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

interface CE {
  id: string
  name: string
  phone: string | null
  pin: string | null
  tier: 'top_end' | 'bottom_end'
  max_clients_per_day: number
  is_active: boolean
  today_pending: number
  total_called: number
  positive: number
  negative: number
  wrong_poc: number
  onboarded: number
  created_at: string
}

const BLANK: Omit<CE, 'id' | 'today_pending' | 'total_called' | 'positive' | 'negative' | 'wrong_poc' | 'onboarded' | 'created_at'> = {
  name: '', phone: '', pin: '', tier: 'top_end', max_clients_per_day: 20, is_active: true,
}

function SummaryTile({ label, value, cls }: { label: string; value: number; cls: string }) {
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
            <p className="text-[10px] text-gray-400 mt-1">Bottom-end auto-assignment isn't built yet — top-end only for now.</p>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Max Clients / Day</label>
            <input type="number" min={1} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.max_clients_per_day}
              onChange={e => setForm((p: any) => ({ ...p, max_clients_per_day: parseInt(e.target.value) || 1 }))} />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Login PIN</label>
            <input type="password" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.pin || ''} placeholder="Set a PIN for CE login"
              onChange={e => setForm((p: any) => ({ ...p, pin: e.target.value }))} />
            <p className="text-[10px] text-gray-400 mt-1">Required to access the executive's call page</p>
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

function ReassignModal({ ce, others, onClose, onSave, saving }: {
  ce: CE; others: CE[]; onClose: () => void; onSave: (toId: string) => void; saving: boolean
}) {
  const [toId, setToId] = useState('')
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-5" onClick={e => e.stopPropagation()}>
        <p className="text-sm font-bold text-gray-900">Reassign {ce.name}'s queue</p>
        <p className="text-xs text-gray-500 mt-1 mb-4">
          Moves {ce.today_pending} still-uncalled client{ce.today_pending !== 1 ? 's' : ''} to another executive.
          Call history stays with {ce.name}.
        </p>
        <select className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
          value={toId} onChange={e => setToId(e.target.value)}>
          <option value="">Select executive…</option>
          {others.filter(o => o.id !== ce.id).map(o => (
            <option key={o.id} value={o.id}>{o.name} ({o.today_pending}/{o.max_clients_per_day} today)</option>
          ))}
        </select>
        <div className="flex gap-3 mt-4">
          <button onClick={onClose} className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button onClick={() => toId && onSave(toId)} disabled={!toId || saving}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200">
            {saving ? 'Moving…' : 'Reassign'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function AdminCEsPage() {
  const qc = useQueryClient()
  const [modalForm, setModalForm] = useState<any>(null)
  const [reassignFrom, setReassignFrom] = useState<CE | null>(null)

  const { data: ces = [], isLoading } = useQuery({
    queryKey: ['admin-ces'],
    queryFn: () => api.get('/admin/ces').then(r => r.data.data as CE[]),
  })

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

  const reassignMutation = useMutation({
    mutationFn: ({ fromId, toId }: { fromId: string; toId: string }) =>
      api.post(`/admin/ces/${fromId}/reassign`, null, { params: { to_ce_id: toId } }).then(r => r.data),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['admin-ces'] })
      setReassignFrom(null)
      alert(`Reassigned ${res.data.reassigned} client(s).`)
    },
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
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Customer Executives</h1>
          <p className="text-sm text-gray-500 mt-0.5">Daily call queue — auto-assigned by capacity (top end only, for now)</p>
        </div>
        <button onClick={() => setModalForm({ ...BLANK })}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700">
          + Add Executive
        </button>
      </div>

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
                {['Name', 'Tier', 'Contact', "Queue / Cap", 'Called', 'Positive', 'Negative', 'Wrong POC', 'Onboarded', 'Status', ''].map(h => (
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
                    <div className="flex items-center gap-2">
                      <span className={`text-sm font-semibold whitespace-nowrap ${ce.today_pending >= ce.max_clients_per_day ? 'text-red-600' : 'text-gray-700'}`}>
                        {ce.today_pending}/{ce.max_clients_per_day}
                      </span>
                      <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                        <div
                          className={`h-full rounded-full transition-all ${
                            ce.today_pending >= ce.max_clients_per_day ? 'bg-red-500' :
                            ce.today_pending / ce.max_clients_per_day >= 0.75 ? 'bg-amber-400' : 'bg-blue-500'
                          }`}
                          style={{ width: `${Math.min(100, (ce.today_pending / ce.max_clients_per_day) * 100)}%` }}
                        />
                      </div>
                    </div>
                  </td>
                  <td className="px-3 py-3 text-sm text-gray-700 text-center">{ce.total_called}</td>
                  <td className="px-3 py-3 text-sm text-green-600 font-semibold text-center">{ce.positive}</td>
                  <td className="px-3 py-3 text-sm text-red-600 font-semibold text-center">{ce.negative}</td>
                  <td className="px-3 py-3 text-sm text-orange-600 font-semibold text-center">{ce.wrong_poc}</td>
                  <td className="px-3 py-3 text-sm text-blue-600 font-semibold text-center">{ce.onboarded}</td>
                  <td className="px-3 py-3">
                    <button onClick={() => toggleActive(ce)}
                      className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${ce.is_active ? 'bg-blue-600' : 'bg-gray-300'}`}>
                      <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${ce.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                    </button>
                  </td>
                  <td className="px-3 py-3">
                    <div className="flex gap-3">
                      <button onClick={() => window.open(`/ce/${ce.id}`, '_blank')} className="text-xs px-2.5 py-1 rounded-lg bg-indigo-50 border border-indigo-200 text-indigo-700 font-medium hover:bg-indigo-100 transition-colors">Open Page ↗</button>
                      {ce.today_pending > 0 && (
                        <button onClick={() => setReassignFrom(ce)} className="text-xs text-amber-600 hover:underline">Reassign</button>
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

      {modalForm && (
        <CEModal form={modalForm} setForm={setModalForm} saving={saveMutation.isPending}
          onClose={() => setModalForm(null)} onSave={() => saveMutation.mutate(modalForm)} />
      )}

      {reassignFrom && (
        <ReassignModal ce={reassignFrom} others={ces} saving={reassignMutation.isPending}
          onClose={() => setReassignFrom(null)}
          onSave={(toId) => reassignMutation.mutate({ fromId: reassignFrom.id, toId })} />
      )}
    </div>
  )
}
