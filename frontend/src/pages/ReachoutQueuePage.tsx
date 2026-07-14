import { useState, useMemo } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow, format, parseISO, isToday, isTomorrow } from 'date-fns'
import { getReachoutQueue, bulkSendReachout, type ReachoutQueueItem } from '../api/cronReachout'
import { Link } from 'react-router-dom'

const SEGMENT_LABEL: Record<string, string> = {
  active_job_post: 'Active Job Post',
  ca_network: 'CA Network',
  employer_lead: 'Employer Lead',
}

const SEGMENT_COLOR: Record<string, string> = {
  active_job_post: 'bg-blue-50 text-blue-700',
  ca_network: 'bg-purple-50 text-purple-700',
  employer_lead: 'bg-green-50 text-green-700',
}

function dateBucket(item: ReachoutQueueItem): string {
  if (!item.next_due_at || item.is_overdue) return 'Overdue'
  const d = parseISO(item.next_due_at)
  if (isToday(d)) return 'Today'
  if (isTomorrow(d)) return 'Tomorrow'
  return format(d, 'dd MMM')
}

export default function ReachoutQueuePage() {
  const qc = useQueryClient()
  const [segFilter, setSegFilter] = useState('all')
  const [stepFilter, setStepFilter] = useState('all')
  const [dateFilter, setDateFilter] = useState('all')
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [sentResult, setSentResult] = useState<{ sent: number; failed: number } | null>(null)

  const { data: items = [], isLoading, refetch } = useQuery({
    queryKey: ['reachout-queue'],
    queryFn: getReachoutQueue,
    refetchInterval: 60_000,
  })

  const bulkMut = useMutation({
    mutationFn: () => bulkSendReachout(Array.from(selected)),
    onSuccess: (res) => {
      setSentResult({ sent: res.sent, failed: res.failed })
      setSelected(new Set())
      qc.invalidateQueries({ queryKey: ['reachout-queue'] })
      setTimeout(() => setSentResult(null), 5000)
    },
  })

  // Derive filter options from data
  const steps = useMemo(() => {
    const s = Array.from(new Set(items.map(i => i.outreach_step))).sort((a, b) => a - b)
    return s
  }, [items])

  const dateBuckets = useMemo(() => {
    const order = ['Overdue', 'Today', 'Tomorrow']
    const buckets = Array.from(new Set(items.map(dateBucket)))
    return buckets.sort((a, b) => {
      const ai = order.indexOf(a), bi = order.indexOf(b)
      if (ai !== -1 && bi !== -1) return ai - bi
      if (ai !== -1) return -1
      if (bi !== -1) return 1
      return a.localeCompare(b)
    })
  }, [items])

  const filtered = useMemo(() => {
    return items.filter(i => {
      if (segFilter !== 'all' && i.segment !== segFilter) return false
      if (stepFilter !== 'all' && i.outreach_step !== Number(stepFilter)) return false
      if (dateFilter !== 'all' && dateBucket(i) !== dateFilter) return false
      return true
    })
  }, [items, segFilter, stepFilter, dateFilter])

  function toggleItem(id: string) {
    setSelected(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function toggleAll() {
    const allIds = filtered.map(i => i.id)
    const allSelected = allIds.every(id => selected.has(id))
    setSelected(allSelected ? new Set() : new Set(allIds))
  }

  const allSelected = filtered.length > 0 && filtered.every(i => selected.has(i.id))
  const someSelected = filtered.some(i => selected.has(i.id))

  function FilterChip({
    label, value, active, count, color,
  }: { label: string; value: string; active: boolean; count?: number; color?: string }) {
    return (
      <button
        className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors whitespace-nowrap ${
          active
            ? (color ?? 'bg-gray-900 text-white')
            : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
        }`}
      >
        {label}
        {count !== undefined && (
          <span className={`ml-1.5 ${active ? 'opacity-70' : 'text-gray-400'}`}>
            {count}
          </span>
        )}
      </button>
    )
  }

  return (
    <div className="p-6 max-w-6xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-gray-900">Client Reachout Queue</h1>
        <button onClick={() => refetch()} className="text-xs text-blue-500 hover:underline">Refresh</button>
      </div>

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 px-4 py-3 mb-4 space-y-2.5">
        {/* Segment */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400 w-14 flex-shrink-0">Segment</span>
          {['all', 'active_job_post', 'ca_network', 'employer_lead'].map(v => {
            const count = v === 'all' ? items.length : items.filter(i => i.segment === v).length
            return (
              <button
                key={v}
                onClick={() => { setSegFilter(v); setSelected(new Set()) }}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                  segFilter === v ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {v === 'all' ? 'All' : SEGMENT_LABEL[v]}
                <span className={`ml-1.5 ${segFilter === v ? 'opacity-60' : 'text-gray-400'}`}>{count}</span>
              </button>
            )
          })}
        </div>

        {/* Step */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400 w-14 flex-shrink-0">Step</span>
          <button
            onClick={() => { setStepFilter('all'); setSelected(new Set()) }}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              stepFilter === 'all' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All
            <span className={`ml-1.5 ${stepFilter === 'all' ? 'opacity-60' : 'text-gray-400'}`}>{items.length}</span>
          </button>
          {steps.map(s => {
            const count = items.filter(i => i.outreach_step === s).length
            return (
              <button
                key={s}
                onClick={() => { setStepFilter(String(s)); setSelected(new Set()) }}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                  stepFilter === String(s) ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                Step {s + 1}
                <span className={`ml-1.5 ${stepFilter === String(s) ? 'opacity-70' : 'text-gray-400'}`}>{count}</span>
              </button>
            )
          })}
        </div>

        {/* Date */}
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400 w-14 flex-shrink-0">Due</span>
          <button
            onClick={() => { setDateFilter('all'); setSelected(new Set()) }}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              dateFilter === 'all' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All
            <span className={`ml-1.5 ${dateFilter === 'all' ? 'opacity-60' : 'text-gray-400'}`}>{items.length}</span>
          </button>
          {dateBuckets.map(b => {
            const count = items.filter(i => dateBucket(i) === b).length
            const isOverdue = b === 'Overdue'
            return (
              <button
                key={b}
                onClick={() => { setDateFilter(b); setSelected(new Set()) }}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                  dateFilter === b
                    ? isOverdue ? 'bg-red-600 text-white' : 'bg-amber-500 text-white'
                    : isOverdue ? 'bg-red-50 text-red-600 hover:bg-red-100' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {b}
                <span className={`ml-1.5 ${dateFilter === b ? 'opacity-70' : isOverdue ? 'text-red-400' : 'text-gray-400'}`}>{count}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Bulk action bar */}
      {selected.size > 0 && (
        <div className="mb-4 flex items-center justify-between bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
          <span className="text-sm font-medium text-blue-800">
            {selected.size} client{selected.size > 1 ? 's' : ''} selected
          </span>
          <div className="flex items-center gap-3">
            <button onClick={() => setSelected(new Set())} className="text-xs text-blue-500 hover:underline">
              Clear
            </button>
            <button
              onClick={() => bulkMut.mutate()}
              disabled={bulkMut.isPending}
              className="px-4 py-1.5 text-xs bg-blue-600 text-white rounded-lg font-medium hover:bg-blue-700 disabled:opacity-50 transition-colors"
            >
              {bulkMut.isPending ? 'Sending…' : `Send to ${selected.size} client${selected.size > 1 ? 's' : ''}`}
            </button>
          </div>
        </div>
      )}

      {sentResult && (
        <div className="mb-4 px-4 py-3 bg-green-50 border border-green-200 rounded-xl text-sm text-green-800">
          Done — {sentResult.sent} sent{sentResult.failed > 0 ? `, ${sentResult.failed} failed` : ''}
        </div>
      )}

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <div key={i} className="h-14 rounded-xl bg-gray-100 animate-pulse" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-gray-400 text-sm">No clients match these filters.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="px-4 py-3 w-8">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={el => { if (el) el.indeterminate = someSelected && !allSelected }}
                    onChange={toggleAll}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                </th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Company</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Segment</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Step</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Last Sent</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Due</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filtered.map(item => (
                <tr
                  key={item.id}
                  className={`hover:bg-gray-50/60 transition-colors ${selected.has(item.id) ? 'bg-blue-50/30' : ''}`}
                >
                  <td className="px-4 py-3">
                    <input
                      type="checkbox"
                      checked={selected.has(item.id)}
                      onChange={() => toggleItem(item.id)}
                      className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                  </td>
                  <td className="px-3 py-3">
                    <Link to={`/clients/${item.id}`} className="font-medium text-gray-900 hover:text-blue-600 transition-colors">
                      {item.company_name || '—'}
                    </Link>
                    {item.poc_phone && <p className="text-xs text-gray-400 mt-0.5">{item.poc_phone}</p>}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${SEGMENT_COLOR[item.segment || ''] ?? 'bg-gray-100 text-gray-600'}`}>
                      {SEGMENT_LABEL[item.segment || ''] || item.segment || '—'}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className="text-xs font-medium text-gray-700">Step {item.outreach_step + 1}</span>
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-500">
                    {item.last_outreach_at
                      ? <span title={format(parseISO(item.last_outreach_at), 'dd MMM yyyy, HH:mm')}>
                          {formatDistanceToNow(parseISO(item.last_outreach_at), { addSuffix: true })}
                        </span>
                      : <span className="italic text-gray-400">never</span>}
                  </td>
                  <td className="px-3 py-3 text-xs">
                    {item.next_due_at ? (
                      <span className={item.is_overdue ? 'text-red-500 font-medium' : 'text-gray-500'}>
                        {item.is_overdue
                          ? `overdue ${formatDistanceToNow(parseISO(item.next_due_at), { addSuffix: true })}`
                          : `${formatDistanceToNow(parseISO(item.next_due_at), { addSuffix: true })}`}
                      </span>
                    ) : (
                      <span className="text-red-500 font-medium">ready now</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
