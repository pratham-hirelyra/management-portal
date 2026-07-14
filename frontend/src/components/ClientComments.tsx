import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import { getClientComments, addClientComment, deleteClientComment } from '../api/clients'

interface Props {
  clientId: string
}

export default function ClientComments({ clientId }: Props) {
  const qc = useQueryClient()
  const [commentText, setCommentText] = useState('')
  const [createdBy, setCreatedBy] = useState('')

  const { data: comments = [], isLoading } = useQuery({
    queryKey: ['client-comments', clientId],
    queryFn: () => getClientComments(clientId),
  })

  const addComment = useMutation({
    mutationFn: () =>
      addClientComment(clientId, {
        comment_text: commentText.trim() || undefined,
        created_by: createdBy.trim() || undefined,
      }),
    onSuccess: () => {
      setCommentText('')
      qc.invalidateQueries({ queryKey: ['client-comments', clientId] })
    },
  })

  const removeComment = useMutation({
    mutationFn: (commentId: string) => deleteClientComment(clientId, commentId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['client-comments', clientId] }),
  })

  return (
    <div className="space-y-6">
      {/* Add comment box */}
      <div className="border border-gray-200 rounded-xl p-5 space-y-4">
        <div>
          <p className="text-xs font-medium text-gray-500 mb-2">Comment</p>
          <textarea
            rows={3}
            placeholder="Add a note for this client..."
            className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
            value={commentText}
            onChange={e => setCommentText(e.target.value)}
          />
        </div>

        <div className="flex items-end justify-between gap-3">
          <div className="w-48">
            <p className="text-xs font-medium text-gray-500 mb-1">Your name (optional)</p>
            <input
              type="text"
              placeholder="RM"
              className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-blue-400"
              value={createdBy}
              onChange={e => setCreatedBy(e.target.value)}
            />
          </div>
          <button
            onClick={() => addComment.mutate()}
            disabled={!commentText.trim() || addComment.isPending}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50"
          >
            {addComment.isPending ? 'Saving…' : 'Add Comment'}
          </button>
        </div>
        {addComment.isError && (
          <p className="text-xs text-red-500">Failed to save comment</p>
        )}
      </div>

      {/* Comments list */}
      {isLoading ? (
        <p className="text-sm text-gray-400">Loading…</p>
      ) : comments.length === 0 ? (
        <p className="text-sm text-gray-400">No comments yet.</p>
      ) : (
        <ul className="space-y-3">
          {comments.map(c => (
            <li key={c.id} className="border border-gray-200 rounded-xl p-4">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm text-gray-700 whitespace-pre-wrap flex-1">{c.comment_text}</p>
                <button
                  onClick={() => removeComment.mutate(c.id)}
                  className="text-gray-300 hover:text-red-500 text-lg leading-none"
                  title="Delete comment"
                >
                  ×
                </button>
              </div>
              <p className="mt-2 text-xs text-gray-400">
                {c.created_by} · {formatDistanceToNow(new Date(c.created_at), { addSuffix: true })}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
