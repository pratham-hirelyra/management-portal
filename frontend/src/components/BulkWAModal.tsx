import { useMutation, useQueryClient } from '@tanstack/react-query'
import { sendCandidateIntentBulk } from '../api/whatsapp'

interface Props {
  mappingIds: string[]
  onClose: () => void
}

export default function BulkWAModal({ mappingIds, onClose }: Props) {
  const qc = useQueryClient()

  const send = useMutation({
    mutationFn: () => sendCandidateIntentBulk(mappingIds),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['mappings'] })
      onClose()
      alert(`Sent to ${data.data.sent} candidate(s).`)
    },
  })

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-sm p-6">
        <h2 className="text-base font-semibold mb-1">Send JD to Candidates</h2>
        <p className="text-sm text-gray-500 mb-4">
          The <span className="font-medium text-gray-700">candidate_share_client_jd</span> template
          will be sent to <span className="font-semibold">{mappingIds.length}</span> selected
          candidate(s). All variables are auto-filled from the client's job details.
        </p>
        <p className="text-xs text-gray-400 mb-5">
          Mapping stage will move to <span className="font-medium">Intent Ask</span> for each candidate.
        </p>

        {send.isError && (
          <p className="text-xs text-red-500 mb-3">Failed to send. Please try again.</p>
        )}

        <div className="flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            disabled={send.isPending}
            onClick={() => send.mutate()}
            className="px-4 py-2 text-sm rounded-lg bg-green-600 text-white hover:bg-green-700 disabled:opacity-50"
          >
            {send.isPending ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}
