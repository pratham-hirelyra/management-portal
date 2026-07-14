import { useState } from 'react'

const MIN_SLOTS = 4

interface Props {
  onConfirm: (slots: { label: string }[]) => void
  onCancel: () => void
  loading?: boolean
}

export default function SlotsInputModal({ onConfirm, onCancel, loading }: Props) {
  const [rows, setRows] = useState<string[]>(['', '', '', ''])

  const addRow = () => setRows(prev => [...prev, ''])
  const setRow = (i: number, val: string) =>
    setRows(prev => prev.map((r, idx) => (idx === i ? val : r)))
  const removeRow = (i: number) =>
    setRows(prev => prev.filter((_, idx) => idx !== i))

  const filledSlots = rows.filter(r => r.trim())
  const canConfirm = filledSlots.length >= MIN_SLOTS

  const handleConfirm = () => {
    if (!canConfirm) return
    onConfirm(filledSlots.map(r => ({ label: r.trim() })))
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-md p-6">
        <h2 className="text-base font-semibold mb-1">Enter Available Slots</h2>
        <p className="text-xs text-gray-400 mb-4">
          Minimum {MIN_SLOTS} slots required · max 2 candidates per slot
        </p>
        <div className="space-y-2">
          {rows.map((row, i) => (
            <div key={i} className="flex gap-2 items-center">
              <span className="text-xs text-gray-400 w-5 text-right shrink-0">{i + 1}.</span>
              <input
                className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                placeholder="e.g. Mon 12 May 3pm"
                value={row}
                onChange={e => setRow(i, e.target.value)}
              />
              {rows.length > MIN_SLOTS && (
                <button
                  onClick={() => removeRow(i)}
                  className="text-gray-300 hover:text-red-500 text-lg leading-none"
                >
                  ×
                </button>
              )}
            </div>
          ))}
        </div>

        <button onClick={addRow} className="mt-3 text-xs text-blue-600 hover:underline">
          + Add another slot
        </button>

        {!canConfirm && filledSlots.length > 0 && (
          <p className="mt-3 text-xs text-amber-600">
            {MIN_SLOTS - filledSlots.length} more slot{MIN_SLOTS - filledSlots.length !== 1 ? 's' : ''} needed
          </p>
        )}

        <div className="flex justify-end gap-3 mt-5">
          <button
            onClick={onCancel}
            className="px-4 py-2 text-sm rounded-lg border border-gray-300 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            onClick={handleConfirm}
            disabled={loading || !canConfirm}
            className="px-4 py-2 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Saving…' : `Confirm ${filledSlots.length >= MIN_SLOTS ? `(${filledSlots.length})` : ''}`}
          </button>
        </div>
      </div>
    </div>
  )
}
