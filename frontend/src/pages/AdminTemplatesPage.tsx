import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/axios'

interface Template {
  id: string
  template_name: string
  image_url: string | null
  segment: string
  is_active: boolean
  total_sent: number
  delivered: number
  read_count: number
  created_at: string
}

interface CronSchedule {
  id: string
  segment: string
  cadence: string
  is_active: boolean
  last_run_at: string | null
}

const SEGMENTS = [
  { value: 'ca_network',      label: 'CA Network' },
  { value: 'active_job_post', label: 'Active Job Posts' },
  { value: 'employer_lead',   label: 'Employer Leads' },
  { value: 'all',             label: 'All Segments' },
]

const SEGMENT_LABELS: Record<string, string> = {
  ca_network: 'CA Network', active_job_post: 'Active Job Posts',
  employer_lead: 'Employer Leads', all: 'All',
}

const SEGMENT_COLORS: Record<string, string> = {
  ca_network: 'bg-purple-100 text-purple-700',
  active_job_post: 'bg-blue-100 text-blue-700',
  employer_lead: 'bg-amber-100 text-amber-700',
  all: 'bg-gray-100 text-gray-600',
}

function pct(num: number, den: number) {
  if (!den) return '—'
  return `${Math.round((num / den) * 100)}%`
}

function Empty({ form, setForm, onSave, onClose, saving }: any) {
  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-md p-6">
        <h3 className="text-base font-bold text-gray-900 mb-5">{form.id ? 'Edit Template' : 'Add Template'}</h3>
        <div className="space-y-4">
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Template Name (MSG91) *</label>
            <input required className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.template_name} onChange={e => setForm((p: any) => ({ ...p, template_name: e.target.value }))}
              placeholder="e.g. client_reachout_v2" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Creative / Image URL</label>
            <input className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.image_url || ''} onChange={e => setForm((p: any) => ({ ...p, image_url: e.target.value }))}
              placeholder="https://…" />
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-700 mb-1">Segment *</label>
            <select className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
              value={form.segment} onChange={e => setForm((p: any) => ({ ...p, segment: e.target.value }))}>
              {SEGMENTS.map(s => <option key={s.value} value={s.value}>{s.label}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="is_active" checked={form.is_active}
              onChange={e => setForm((p: any) => ({ ...p, is_active: e.target.checked }))}
              className="accent-blue-600 w-4 h-4" />
            <label htmlFor="is_active" className="text-sm text-gray-700">Active (include in cron rotation)</label>
          </div>
        </div>
        <div className="flex gap-3 mt-6">
          <button type="button" onClick={onClose}
            className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
            Cancel
          </button>
          <button type="button" onClick={onSave} disabled={saving || !form.template_name || !form.segment}
            className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700
              disabled:bg-gray-200 disabled:text-gray-400">
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

export default function AdminTemplatesPage() {
  const qc = useQueryClient()
  const [modalForm, setModalForm] = useState<any>(null)
  const [triggerSegment, setTriggerSegment] = useState<string | null>(null)

  const { data: templates = [], isLoading: loadingTpl } = useQuery({
    queryKey: ['admin-templates'],
    queryFn: () => api.get('/admin/templates').then(r => r.data.data as Template[]),
  })

  const { data: crons = [], isLoading: loadingCrons } = useQuery({
    queryKey: ['admin-crons'],
    queryFn: () => api.get('/admin/cron-schedules').then(r => r.data.data as CronSchedule[]),
  })

  const saveMutation = useMutation({
    mutationFn: (form: any) => form.id
      ? api.patch(`/admin/templates/${form.id}`, form).then(r => r.data)
      : api.post('/admin/templates', form).then(r => r.data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['admin-templates'] }); setModalForm(null) },
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/admin/templates/${id}`).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-templates'] }),
  })

  const cronMutation = useMutation({
    mutationFn: ({ segment, patch }: { segment: string; patch: any }) =>
      api.patch(`/admin/cron-schedules/${segment}`, patch).then(r => r.data),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['admin-crons'] }),
  })

  const triggerMutation = useMutation({
    mutationFn: (segment: string) =>
      api.post(`/admin/cron-schedules/${segment}/trigger`).then(r => r.data),
    onSuccess: (data) => {
      alert(`Sent: ${data.data.sent}  Skipped: ${data.data.skipped}`)
      setTriggerSegment(null)
    },
  })

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Template Pool</h1>
          <p className="text-sm text-gray-500 mt-0.5">Manage WhatsApp outreach templates and delivery stats</p>
        </div>
        <button
          onClick={() => setModalForm({ template_name: '', image_url: '', segment: 'ca_network', is_active: true })}
          className="px-4 py-2 bg-blue-600 text-white text-sm font-semibold rounded-lg hover:bg-blue-700"
        >
          + Add Template
        </button>
      </div>

      {/* Templates table */}
      <div className="bg-white rounded-xl border border-gray-200 overflow-hidden mb-8">
        {loadingTpl ? (
          <div className="py-12 text-center text-gray-400 text-sm">Loading…</div>
        ) : templates.length === 0 ? (
          <div className="py-12 text-center text-gray-400 text-sm">No templates yet. Add one to get started.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b border-gray-200">
              <tr>
                {['Template Name', 'Segment', 'Creative', 'Sent', 'Delivered', 'Read', 'Status', ''].map(h => (
                  <th key={h} className="text-left px-4 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wider">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {templates.map(t => (
                <tr key={t.id} className="hover:bg-gray-50">
                  <td className="px-4 py-3 font-mono text-xs text-gray-800">{t.template_name}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${SEGMENT_COLORS[t.segment] || 'bg-gray-100 text-gray-600'}`}>
                      {SEGMENT_LABELS[t.segment] || t.segment}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    {t.image_url
                      ? <a href={t.image_url} target="_blank" rel="noreferrer" className="text-blue-600 underline text-xs">View</a>
                      : <span className="text-gray-300 text-xs">—</span>}
                  </td>
                  <td className="px-4 py-3 text-gray-700">{t.total_sent}</td>
                  <td className="px-4 py-3 text-gray-700">{t.delivered} <span className="text-gray-400 text-xs">({pct(t.delivered, t.total_sent)})</span></td>
                  <td className="px-4 py-3 text-gray-700">{t.read_count} <span className="text-gray-400 text-xs">({pct(t.read_count, t.total_sent)})</span></td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${t.is_active ? 'bg-green-100 text-green-700' : 'bg-gray-100 text-gray-500'}`}>
                      {t.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex gap-2">
                      <button onClick={() => setModalForm({ ...t })}
                        className="text-xs text-blue-600 hover:underline">Edit</button>
                      <button onClick={() => { if (confirm('Delete this template?')) deleteMutation.mutate(t.id) }}
                        className="text-xs text-red-400 hover:text-red-600 hover:underline">Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Cron schedules */}
      <div className="mb-2">
        <h2 className="text-base font-bold text-gray-900">Outreach Schedules</h2>
        <p className="text-sm text-gray-500 mt-0.5">Configure recurrence per segment. Point Google Cloud Scheduler to <code className="bg-gray-100 px-1 rounded text-xs">POST /cron/reachout?segment=…</code></p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        {loadingCrons
          ? [0, 1, 2].map(i => <div key={i} className="bg-white border border-gray-200 rounded-xl h-32 animate-pulse" />)
          : crons.map(c => (
            <div key={c.segment} className="bg-white border border-gray-200 rounded-xl p-4">
              <div className="flex items-start justify-between mb-3">
                <div>
                  <p className="text-sm font-semibold text-gray-900">{SEGMENT_LABELS[c.segment] || c.segment}</p>
                  {c.last_run_at && (
                    <p className="text-xs text-gray-400 mt-0.5">
                      Last run: {new Date(c.last_run_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}
                    </p>
                  )}
                </div>
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <div
                    onClick={() => cronMutation.mutate({ segment: c.segment, patch: { is_active: !c.is_active } })}
                    className={`w-9 h-5 rounded-full relative transition-colors cursor-pointer
                      ${c.is_active ? 'bg-blue-600' : 'bg-gray-300'}`}
                  >
                    <div className={`absolute top-0.5 w-4 h-4 bg-white rounded-full shadow transition-transform
                      ${c.is_active ? 'translate-x-4' : 'translate-x-0.5'}`} />
                  </div>
                </label>
              </div>

              <select
                className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 text-gray-700
                  focus:outline-none focus:ring-1 focus:ring-blue-400 mb-3"
                value={c.cadence}
                onChange={e => cronMutation.mutate({ segment: c.segment, patch: { cadence: e.target.value } })}
              >
                <option value="daily">Daily</option>
                <option value="weekly">Weekly</option>
              </select>

              <button
                onClick={() => setTriggerSegment(c.segment)}
                className="w-full py-1.5 text-xs font-medium border border-blue-200 text-blue-600
                  rounded-lg hover:bg-blue-50 transition-colors"
              >
                Trigger Now
              </button>
            </div>
          ))}
      </div>

      {/* Modals */}
      {modalForm && (
        <Empty
          form={modalForm}
          setForm={setModalForm}
          saving={saveMutation.isPending}
          onClose={() => setModalForm(null)}
          onSave={() => saveMutation.mutate(modalForm)}
        />
      )}

      {triggerSegment && (
        <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-sm p-6 text-center">
            <p className="text-base font-semibold text-gray-900 mb-2">Trigger Manual Send</p>
            <p className="text-sm text-gray-500 mb-6">
              This will immediately send WhatsApp messages to all unsent clients in the
              <strong> {SEGMENT_LABELS[triggerSegment]} </strong> segment.
            </p>
            <div className="flex gap-3">
              <button onClick={() => setTriggerSegment(null)}
                className="flex-1 py-2.5 rounded-lg border border-gray-300 text-sm font-medium text-gray-700 hover:bg-gray-50">
                Cancel
              </button>
              <button
                onClick={() => triggerMutation.mutate(triggerSegment)}
                disabled={triggerMutation.isPending}
                className="flex-1 py-2.5 rounded-lg bg-blue-600 text-white text-sm font-semibold hover:bg-blue-700 disabled:bg-gray-200"
              >
                {triggerMutation.isPending ? 'Sending…' : 'Send Now'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
