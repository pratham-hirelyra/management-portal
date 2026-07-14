import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

interface RM {
  id: string
  name: string
  phone: string | null
  email: string | null
  location: string | null
  city: string | null
  max_clients: number
  is_active: boolean
  active_clients: number
  created_at: string
}

interface AssignedClient {
  id: string
  company_name: string
  stage: string
  job_title: string | null
  city: string | null
  location: string | null
}

const BLANK: Omit<RM, 'id' | 'active_clients' | 'created_at'> = {
  name: '', phone: '', email: '', location: '', city: '', max_clients: 20, is_active: true,
}

const STAGE_COLORS: Record<string, string> = {
  lead:            'bg-gray-100 text-gray-600',
  scraping:        'bg-blue-100 text-blue-700',
  scraped:         'bg-blue-100 text-blue-700',
  reachout_sent:   'bg-amber-100 text-amber-700',
  interested:      'bg-orange-100 text-orange-700',
  agreement_sent:  'bg-purple-100 text-purple-700',
  onboarded:       'bg-green-100 text-green-700',
}

function RMModal({ form, setForm, onSave, onClose, saving }: {
  form: any; setForm: (f: any) => void
  onSave: () => void; onClose: () => void; saving: boolean
}) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6 max-h-[90vh] overflow-y-auto">
        <h3 className="text-base font-bold text-gray-900 mb-5">
          {form.id ? 'Edit RM' : 'Add RM'}
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
            <label className="block text-xs font-semibold text-gray-700 mb-1">Email</label>
            <input type="email" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.email || ''} onChange={e => setForm((p: any) => ({ ...p, email: e.target.value }))}
              placeholder="rm@company.com" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">City</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.city || ''} onChange={e => setForm((p: any) => ({ ...p, city: e.target.value }))}
              placeholder="e.g. Ahmedabad" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Zone / Area</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.location || ''} onChange={e => setForm((p: any) => ({ ...p, location: e.target.value }))}
              placeholder="e.g. West Ahmedabad" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Max Clients (Capacity)</label>
            <input type="number" min={1} className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.max_clients}
              onChange={e => setForm((p: any) => ({ ...p, max_clients: parseInt(e.target.value) || 1 }))} />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Dashboard PIN</label>
            <input type="password" className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.pin || ''} placeholder="Set a PIN for RM login"
              onChange={e => setForm((p: any) => ({ ...p, pin: e.target.value }))} />
            <p className="text-[10px] text-gray-400 mt-1">Required to access the RM dashboard page</p>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="rm_is_active" checked={form.is_active}
              onChange={e => setForm((p: any) => ({ ...p, is_active: e.target.checked }))}
              className="accent-blue-600 w-4 h-4" />
            <label htmlFor="rm_is_active" className="text-sm text-gray-700">Active</label>
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

function ExpandedClients({ rmId }: { rmId: string }) {
  const { data, isLoading } = useQuery({
    queryKey: ['rm-clients', rmId],
    queryFn: () => api.get(`/admin/rms/${rmId}/clients`).then(r => r.data.data as AssignedClient[]),
  })

  if (isLoading) return (
    <tr><td colSpan={7} className="px-8 py-3 bg-blue-50 text-xs text-gray-400">Loading clients…</td></tr>
  )
  if (!data || data.length === 0) return (
    <tr><td colSpan={7} className="px-8 py-3 bg-blue-50 text-xs text-gray-400">No active clients assigned.</td></tr>
  )
  return (
    <>
      {data.map(c => (
        <tr key={c.id} className="bg-blue-50 border-b border-blue-100">
          <td className="px-8 py-2" colSpan={2}>
            <span className="text-xs font-medium text-gray-800">{c.company_name}</span>
            {c.job_title && <span className="ml-2 text-xs text-gray-500">— {c.job_title}</span>}
          </td>
          <td className="px-4 py-2 text-xs text-gray-500">{c.city || c.location || '—'}</td>
          <td className="px-4 py-2" colSpan={4}>
            <span className={`px-2 py-0.5 rounded-full text-[10px] font-medium ${STAGE_COLORS[c.stage] || 'bg-gray-100 text-gray-500'}`}>
              {c.stage.replace(/_/g, ' ')}
            </span>
          </td>
        </tr>
      ))}
    </>
  )
}

export default function AdminRMsPage() {
  const qc = useQueryClient()
  const [modalForm, setModalForm] = useState<any>(null)
  const [expandedRm, setExpandedRm] = useState<string | null>(null)

  const { data: rms = [], isLoading } = useQuery({
    queryKey: ['admin-rms'],
    queryFn: () => api.get('/admin/rms').then(r => r.data.data as RM[]),
  })

  const saveMutation = useMutation({
    mutationFn: (form: any) =>
      form.id ? api.patch(`/admin/rms/${form.id}`, form).then(r => r.data)
              : api.post('/admin/rms', form).then(r => r.data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-rms'] }); setModalForm(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/rms/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-rms'] }),
  })

  const toggleActive = (rm: RM) => saveMutation.mutate({ id: rm.id, is_active: !rm.is_active })

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Relationship Managers</h1>
          <p className="text-sm text-gray-500 mt-0.5">Auto-assigned to new JDs by city + capacity</p>
        </div>
        <button onClick={() => setModalForm({ ...BLANK })}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700">
          + Add RM
        </button>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
        {isLoading ? (
          <div className="py-12 text-center text-gray-400 text-sm">Loading…</div>
        ) : rms.length === 0 ? (
          <div className="py-12 text-center text-gray-400 text-sm">No RMs yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['Name', 'City / Zone', 'Contact', 'Clients / Cap', 'Status', ''].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {rms.map(rm => (
                <>
                  <tr key={rm.id}
                    className={`hover:bg-gray-50 cursor-pointer ${expandedRm === rm.id ? 'bg-blue-50/40' : ''}`}
                    onClick={() => setExpandedRm(expandedRm === rm.id ? null : rm.id)}
                  >
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-gray-400">{expandedRm === rm.id ? '▾' : '▸'}</span>
                        <span className="font-medium text-gray-900">{rm.name}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="text-xs">
                        {rm.city && <span className="font-medium text-gray-800">{rm.city}</span>}
                        {rm.location && <span className="text-gray-400 ml-1">· {rm.location}</span>}
                        {!rm.city && !rm.location && <span className="text-gray-300">—</span>}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-600">
                      <div>{rm.phone || <span className="text-gray-300">—</span>}</div>
                      <div className="text-gray-400">{rm.email || ''}</div>
                    </td>
                    <td className="px-4 py-3">
                      <div className="flex items-center gap-2">
                        <span className={`text-sm font-semibold ${rm.active_clients >= rm.max_clients ? 'text-red-600' : 'text-gray-700'}`}>
                          {rm.active_clients}/{rm.max_clients}
                        </span>
                        <div className="w-16 h-1.5 bg-gray-100 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all ${
                              rm.active_clients >= rm.max_clients ? 'bg-red-500' :
                              rm.active_clients / rm.max_clients >= 0.75 ? 'bg-amber-400' : 'bg-blue-500'
                            }`}
                            style={{ width: `${Math.min(100, (rm.active_clients / rm.max_clients) * 100)}%` }}
                          />
                        </div>
                      </div>
                    </td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      <button onClick={() => toggleActive(rm)}
                        className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer ${rm.is_active ? 'bg-blue-600' : 'bg-gray-300'}`}>
                        <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform ${rm.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                      </button>
                    </td>
                    <td className="px-4 py-3" onClick={e => e.stopPropagation()}>
                      <div className="flex gap-3">
                        <button onClick={() => window.open(`/rm/${rm.id}`, '_blank')} className="text-xs px-2.5 py-1 rounded-lg bg-indigo-50 border border-indigo-200 text-indigo-700 font-medium hover:bg-indigo-100 transition-colors">Open Page ↗</button>
                        <button onClick={() => setModalForm({ ...rm })} className="text-xs text-blue-600 hover:underline">Edit</button>
                        <button onClick={() => { if (confirm(`Delete ${rm.name}?`)) deleteMutation.mutate(rm.id) }}
                          className="text-xs text-red-400 hover:text-red-600 hover:underline">Delete</button>
                      </div>
                    </td>
                  </tr>
                  {expandedRm === rm.id && <ExpandedClients rmId={rm.id} />}
                </>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {modalForm && (
        <RMModal form={modalForm} setForm={setModalForm} saving={saveMutation.isPending}
          onClose={() => setModalForm(null)} onSave={() => saveMutation.mutate(modalForm)} />
      )}
    </div>
  )
}
