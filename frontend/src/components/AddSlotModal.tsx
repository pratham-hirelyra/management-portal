import { useState } from 'react'

interface Slot {
  label: string
  iso?: string
  received_at?: string
  slot_id?: string
  client_name?: string
  booked_at?: string
}

interface Props {
  currentSlots: Slot[] | null
  onSave: (slots: Slot[]) => void
  onClose: () => void
  isPending: boolean
}

const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December']
const DAYS_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']
const TIME_SLOTS = [
  '9:00 AM','10:00 AM','11:00 AM','12:00 PM',
  '1:00 PM','2:00 PM','3:00 PM','4:00 PM','5:00 PM','6:00 PM','7:00 PM','8:00 PM',
]

function getCalendarDays(year: number, month: number): (number | null)[] {
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = Array(firstDay).fill(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  return cells
}

function parseTime(t: string) {
  const [timePart, period] = t.split(' ')
  const [h, m] = timePart.split(':').map(Number)
  let hour = h
  if (period === 'PM' && h !== 12) hour += 12
  if (period === 'AM' && h === 12) hour = 0
  return { hour, minute: m }
}

function makeLabel(date: Date, timeStr: string): string {
  const { hour, minute } = parseTime(timeStr)
  const start = new Date(date)
  start.setHours(hour, minute, 0, 0)
  const end = new Date(start.getTime() + 60 * 60 * 1000)
  const dateLabel = date.toLocaleDateString('en-IN', {
    weekday: 'long', day: 'numeric', month: 'long', year: 'numeric',
  })
  const fmt = (d: Date) =>
    d.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: true })
  return `${dateLabel} · ${fmt(start)} – ${fmt(end)}`
}

function slotKey(date: Date, timeStr: string) {
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}|${timeStr}`
}

function isSameDay(a: Date, b: Date) {
  return a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate()
}

export default function AddSlotModal({ currentSlots, onSave, onClose, isPending }: Props) {
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  const [viewDate, setViewDate] = useState(new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const [pickedKeys, setPickedKeys] = useState<Set<string>>(new Set())
  const [pickedLabels, setPickedLabels] = useState<Map<string, string>>(new Map())

  const calDays = getCalendarDays(viewDate.getFullYear(), viewDate.getMonth())
  const canGoPrev =
    viewDate.getFullYear() > today.getFullYear() ||
    viewDate.getMonth() > today.getMonth()

  const toggleSlot = (date: Date, timeStr: string) => {
    const key = slotKey(date, timeStr)
    setPickedKeys(prev => {
      const next = new Set(prev)
      next.has(key) ? next.delete(key) : next.add(key)
      return next
    })
    setPickedLabels(prev => {
      const next = new Map(prev)
      next.has(key) ? next.delete(key) : next.set(key, makeLabel(date, timeStr))
      return next
    })
  }

  const handleSave = () => {
    const newSlots: Slot[] = Array.from(pickedKeys).map(key => {
      const [datePart, timeStr] = key.split('|')
      const [year, month, day] = datePart.split('-').map(Number)
      const { hour, minute } = parseTime(timeStr)
      const dt = new Date(year, month, day, hour, minute, 0, 0)
      return { label: pickedLabels.get(key)!, iso: dt.toISOString() }
    })
    onSave([...(currentSlots ?? []), ...newSlots])
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-lg max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-100">
          <h2 className="text-sm font-semibold text-gray-800">Add Interview Slots</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <div className="p-5">
          {/* Month navigation */}
          <div className="flex items-center justify-between mb-4">
            <span className="text-sm font-semibold text-gray-800">
              {MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}
            </span>
            <div className="flex gap-1">
              <button
                onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1))}
                disabled={!canGoPrev}
                className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100 disabled:opacity-30"
              >
                <svg className="w-3.5 h-3.5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <button
                onClick={() => setViewDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1))}
                className="w-7 h-7 flex items-center justify-center rounded-full hover:bg-gray-100"
              >
                <svg className="w-3.5 h-3.5 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
          </div>

          {/* Day headers */}
          <div className="grid grid-cols-7 mb-1">
            {DAYS_SHORT.map(d => (
              <div key={d} className="text-center text-[10px] font-medium text-gray-400 py-1">{d}</div>
            ))}
          </div>

          {/* Calendar grid */}
          <div className="grid grid-cols-7 gap-y-0.5 mb-5">
            {calDays.map((day, i) => {
              if (!day) return <div key={i} />
              const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day)
              const isPast = cellDate < today
              const isToday = isSameDay(cellDate, today)
              const isSelected = selectedDate ? isSameDay(cellDate, selectedDate) : false
              return (
                <button
                  key={i}
                  onClick={() => !isPast && setSelectedDate(cellDate)}
                  disabled={isPast}
                  className={`
                    mx-auto w-8 h-8 flex items-center justify-center rounded-full text-xs transition-colors
                    ${isPast ? 'text-gray-300 cursor-not-allowed' : 'cursor-pointer hover:bg-blue-50 hover:text-blue-600'}
                    ${isSelected ? 'bg-blue-600 text-white hover:bg-blue-600 hover:text-white font-semibold' : ''}
                    ${isToday && !isSelected ? 'font-semibold text-blue-600' : ''}
                    ${!isPast && !isSelected && !isToday ? 'text-gray-800' : ''}
                  `}
                >
                  {day}
                </button>
              )
            })}
          </div>

          {/* Time slots */}
          {selectedDate ? (
            <div className="mb-5">
              <p className="text-xs font-medium text-gray-600 mb-2">
                {selectedDate.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
              </p>
              <div className="grid grid-cols-3 gap-1.5">
                {TIME_SLOTS.filter(timeStr => {
                  if (!isSameDay(selectedDate, today)) return true
                  const { hour, minute } = parseTime(timeStr)
                  const slotTime = new Date()
                  slotTime.setHours(hour, minute, 0, 0)
                  return slotTime > new Date()
                }).map(timeStr => {
                  const key = slotKey(selectedDate, timeStr)
                  const picked = pickedKeys.has(key)
                  return (
                    <button
                      key={timeStr}
                      onClick={() => toggleSlot(selectedDate, timeStr)}
                      className={`py-2 px-2 rounded-lg border text-xs font-medium transition-all ${
                        picked
                          ? 'bg-blue-600 border-blue-600 text-white'
                          : 'border-gray-200 text-gray-700 hover:border-blue-400 hover:text-blue-600'
                      }`}
                    >
                      {timeStr}
                    </button>
                  )
                })}
              </div>
            </div>
          ) : (
            <p className="text-xs text-gray-400 mb-5">Select a date to see available times.</p>
          )}

          {/* Selected chips */}
          {pickedKeys.size > 0 && (
            <div className="mb-5">
              <p className="text-xs font-medium text-gray-500 mb-2">Selected slots ({pickedKeys.size})</p>
              <div className="flex flex-wrap gap-1.5">
                {Array.from(pickedLabels.entries()).map(([key, label]) => (
                  <span
                    key={key}
                    className="inline-flex items-center gap-1 text-[11px] bg-blue-50 text-blue-700 border border-blue-100 rounded-full px-2.5 py-1"
                  >
                    {label}
                    <button
                      onClick={() => {
                        setPickedKeys(prev => { const n = new Set(prev); n.delete(key); return n })
                        setPickedLabels(prev => { const n = new Map(prev); n.delete(key); return n })
                      }}
                      className="text-blue-400 hover:text-blue-700 ml-0.5 leading-none"
                    >×</button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Footer */}
          <div className="flex gap-2 justify-end pt-2 border-t border-gray-100">
            <button
              onClick={onClose}
              className="px-3 py-1.5 text-sm rounded-lg border border-gray-300 text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={pickedKeys.size === 0 || isPending}
              className="px-4 py-1.5 text-sm rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {isPending ? 'Saving…' : `Add ${pickedKeys.size || ''} Slot${pickedKeys.size !== 1 ? 's' : ''}`}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
