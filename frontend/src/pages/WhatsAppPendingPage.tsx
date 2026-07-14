import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getPendingMessages } from '../api/whatsapp'
import type { WhatsAppMessage } from '../types'
import { format } from 'date-fns'
import WhatsAppReviewModal from '../components/WhatsAppReviewModal'

export default function WhatsAppPendingPage() {
  const [selected, setSelected] = useState<WhatsAppMessage | null>(null)

  const { data: messages = [], isLoading, refetch } = useQuery({
    queryKey: ['whatsapp-pending'],
    queryFn: getPendingMessages,
    refetchInterval: 15000,
  })

  return (
    <div className="p-6">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-xl font-bold text-gray-900">WhatsApp Pending</h1>
        <button onClick={() => refetch()} className="text-xs text-blue-500 hover:underline">
          Refresh
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[...Array(5)].map((_, i) => (
            <div key={i} className="h-16 rounded-lg bg-gray-100 animate-pulse" />
          ))}
        </div>
      ) : messages.length === 0 ? (
        <div className="text-center py-20">
          <p className="text-gray-400 text-sm">No messages pending review.</p>
        </div>
      ) : (
        <div className="space-y-2">
          {messages.map(msg => (
            <div
              key={msg.id}
              className="bg-white rounded-xl border border-gray-200 p-4 flex items-start justify-between gap-4 hover:shadow-sm"
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-xs bg-gray-100 px-2 py-0.5 rounded text-gray-600">
                    {msg.message_type.replace(/_/g, ' ')}
                  </span>
                  <span className="text-xs text-gray-400">{msg.phone}</span>
                  <span className="text-xs text-gray-400">
                    {format(new Date(msg.created_at), 'dd MMM, HH:mm')}
                  </span>
                </div>
                <p className="text-sm text-gray-700 line-clamp-2">{msg.message_text}</p>
              </div>
              <button
                onClick={() => setSelected(msg)}
                className="shrink-0 px-3 py-1.5 text-xs rounded-lg bg-green-50 text-green-700 hover:bg-green-100"
              >
                Review
              </button>
            </div>
          ))}
        </div>
      )}

      {selected && (
        <WhatsAppReviewModal
          message={selected}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  )
}
