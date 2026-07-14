import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { getAIVoiceReachoutClients } from '../api/clients'
import { aiCallStatusBadge, aiCallClassificationBadge, clientStageBadge, stageLabel } from '../types'

export default function AIVoiceReachoutPage() {
  const [statusFilter, setStatusFilter] = useState('all')
  const [classificationFilter, setClassificationFilter] = useState('all')

  const { data, isLoading, refetch } = useQuery({
    queryKey: ['ai-voice-reachout'],
    queryFn: getAIVoiceReachoutClients,
    refetchInterval: 60_000,
  })
  const items = data?.data ?? []

  const statuses = useMemo(() => Array.from(new Set(items.map(i => i.status))), [items])
  const classifications = useMemo(
    () => Array.from(new Set(items.map(i => i.classification).filter((c): c is string => !!c))),
    [items]
  )

  const filtered = useMemo(() => {
    return items.filter(i => {
      if (statusFilter !== 'all' && i.status !== statusFilter) return false
      if (classificationFilter !== 'all' && i.classification !== classificationFilter) return false
      return true
    })
  }, [items, statusFilter, classificationFilter])

  return (
    <div className="p-6 max-w-6xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-xl font-bold text-gray-900">AI Voice Reachout</h1>
        <button onClick={() => refetch()} className="text-xs text-blue-500 hover:underline">Refresh</button>
      </div>

      {/* Filter bar */}
      <div className="bg-white rounded-xl border border-gray-200 px-4 py-3 mb-4 space-y-2.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400 w-20 flex-shrink-0">Status</span>
          <button
            onClick={() => setStatusFilter('all')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              statusFilter === 'all' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All
            <span className={`ml-1.5 ${statusFilter === 'all' ? 'opacity-60' : 'text-gray-400'}`}>{items.length}</span>
          </button>
          {statuses.map(s => {
            const count = items.filter(i => i.status === s).length
            return (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                  statusFilter === s ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {stageLabel(s)}
                <span className={`ml-1.5 ${statusFilter === s ? 'opacity-70' : 'text-gray-400'}`}>{count}</span>
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-gray-400 w-20 flex-shrink-0">Outcome</span>
          <button
            onClick={() => setClassificationFilter('all')}
            className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
              classificationFilter === 'all' ? 'bg-gray-900 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
            }`}
          >
            All
            <span className={`ml-1.5 ${classificationFilter === 'all' ? 'opacity-60' : 'text-gray-400'}`}>{items.length}</span>
          </button>
          {classifications.map(c => {
            const count = items.filter(i => i.classification === c).length
            return (
              <button
                key={c}
                onClick={() => setClassificationFilter(c)}
                className={`px-3 py-1 rounded-lg text-xs font-medium transition-colors ${
                  classificationFilter === c ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                }`}
              >
                {stageLabel(c)}
                <span className={`ml-1.5 ${classificationFilter === c ? 'opacity-70' : 'text-gray-400'}`}>{count}</span>
              </button>
            )
          })}
        </div>
      </div>

      {/* Table */}
      {isLoading ? (
        <div className="space-y-2">
          {[...Array(6)].map((_, i) => <div key={i} className="h-14 rounded-xl bg-gray-100 animate-pulse" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-gray-400 text-sm">No AI voice reachout calls match these filters.</p>
        </div>
      ) : (
        <div className="bg-white rounded-xl border border-gray-200 overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-100 bg-gray-50">
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Company</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Stage</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Call Status</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Outcome</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Reason</th>
                <th className="text-left px-3 py-3 text-xs font-semibold text-gray-500 uppercase tracking-wide">Called</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-50">
              {filtered.map(item => (
                <tr key={item.call_id} className="hover:bg-gray-50/60 transition-colors">
                  <td className="px-3 py-3">
                    <Link to={`/clients/${item.client_id}`} className="font-medium text-gray-900 hover:text-blue-600 transition-colors">
                      {item.company_name || '—'}
                    </Link>
                    {item.phone && <p className="text-xs text-gray-400 mt-0.5 font-mono">{item.phone}</p>}
                    {item.is_dnd && (
                      <span className="inline-block mt-0.5 text-[10px] px-1.5 py-0.5 rounded-full bg-red-50 text-red-500">DND</span>
                    )}
                  </td>
                  <td className="px-3 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${clientStageBadge(item.stage)}`}>
                      {stageLabel(item.stage)}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    <span className={`text-xs px-2 py-0.5 rounded-full ${aiCallStatusBadge(item.status)}`}>
                      {stageLabel(item.status)}
                    </span>
                  </td>
                  <td className="px-3 py-3">
                    {item.classification ? (
                      <span className={`text-xs px-2 py-0.5 rounded-full ${aiCallClassificationBadge(item.classification)}`}>
                        {stageLabel(item.classification)}
                      </span>
                    ) : (
                      <span className="text-xs text-gray-400">—</span>
                    )}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-500 max-w-xs">
                    {item.classification_reason ?? '—'}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-500 whitespace-nowrap">
                    {new Date(item.triggered_at).toLocaleString()}
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
