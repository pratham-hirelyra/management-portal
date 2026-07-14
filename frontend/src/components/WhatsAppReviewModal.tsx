import { useMutation, useQueryClient } from '@tanstack/react-query'
import { format } from 'date-fns'
import { approveMessage } from '../api/whatsapp'
import type { WhatsAppMessage } from '../types'

interface Props {
  message: WhatsAppMessage
  onClose: () => void
}

export default function WhatsAppReviewModal({ message, onClose }: Props) {
  const qc = useQueryClient()

  const approve = useMutation({
    mutationFn: () => approveMessage(message.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['whatsapp-pending'] })
      onClose()
    },
  })

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg p-6">
        <div className="flex items-start justify-between mb-4">
          <div>
            <h2 className="text-base font-semibold">Review WhatsApp Message</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              To: {message.phone} · {format(new Date(message.created_at), 'dd MMM, HH:mm')}
            </p>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <div className="bg-gray-50 rounded-lg p-4 text-sm whitespace-pre-wrap border border-gray-200">
          {message.message_text}
        </div>

        <div className="mt-3 flex flex-wrap gap-2 text-xs text-gray-400">
          <span className="bg-gray-100 px-2 py-0.5 rounded">{message.message_type.replace(/_/g, ' ')}</span>
          <span className="bg-gray-100 px-2 py-0.5 rounded">{message.direction}</span>
        </div>

        <div className="flex justify-end gap-3 mt-6">
          <button onClick={onClose} className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50">
            Close
          </button>
          <button
            onClick={() => approve.mutate()}
            disabled={approve.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
          >
            {approve.isPending ? 'Sending…' : 'Approve & Send'}
          </button>
        </div>
        {approve.isError && (
          <p className="mt-2 text-xs text-red-500">Failed to send. Please try again.</p>
        )}
      </div>
    </div>
  )
}
