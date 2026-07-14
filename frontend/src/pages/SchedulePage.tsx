import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { useQuery, useMutation } from '@tanstack/react-query'
import { api } from '../api/axios'
import HiringFunnel from '../components/HiringFunnel'

interface ScheduleInfo {
  client_id: string
  company_name: string
  poc_name: string
  slots_booked: boolean
}

const getSchedule = (clientId: string) =>
  api.get<{ data: ScheduleInfo }>(`/schedule/${clientId}`).then(r => r.data.data)

const bookSlots = (clientId: string, body: { client_name: string; slots: string[] }) =>
  api.post(`/schedule/${clientId}/book`, body).then(r => r.data)

// ── Date/time helpers ──────────────────────────────────────────────────────────

const MONTHS = ['January','February','March','April','May','June',
                'July','August','September','October','November','December']
const DAYS_SHORT = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat']

const TIME_SLOTS = [
  '9:00 AM','10:00 AM','11:00 AM','12:00 PM',
  '1:00 PM','2:00 PM','3:00 PM','4:00 PM','5:00 PM','6:00 PM','7:00 PM','8:00 PM',
]

const MIN_NOTICE_DAYS = 1
const MIN_SLOTS = 4

function getCalendarDays(year: number, month: number): (number | null)[] {
  const firstDay = new Date(year, month, 1).getDay()
  const daysInMonth = new Date(year, month + 1, 0).getDate()
  const cells: (number | null)[] = Array(firstDay).fill(null)
  for (let d = 1; d <= daysInMonth; d++) cells.push(d)
  return cells
}

function parseTime(timeStr: string): { hour: number; minute: number } {
  const [timePart, period] = timeStr.split(' ')
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

// ── Main component ─────────────────────────────────────────────────────────────

export default function SchedulePage() {
  const { clientId } = useParams<{ clientId: string }>()
  const today = new Date()
  today.setHours(0, 0, 0, 0)

  // Earliest selectable date — at least MIN_NOTICE_DAYS from today (rules out today and tomorrow)
  const earliestSelectableDate = new Date(today)
  earliestSelectableDate.setDate(earliestSelectableDate.getDate() + MIN_NOTICE_DAYS)

  const [viewDate, setViewDate] = useState(new Date(today.getFullYear(), today.getMonth(), 1))
  const [selectedDate, setSelectedDate] = useState<Date | null>(null)
  const [pickedKeys, setPickedKeys] = useState<Set<string>>(new Set())
  const [pickedLabels, setPickedLabels] = useState<Map<string, string>>(new Map())
  const [booked, setBooked] = useState(false)
  const [showNoticePopup, setShowNoticePopup] = useState(false)

  const { data: schedule, isLoading, error } = useQuery({
    queryKey: ['schedule', clientId],
    queryFn: () => getSchedule(clientId!),
    enabled: !!clientId,
  })

  // Surface the booking notice 5s after the booking form is shown —
  // easy to miss as plain text, so we also nudge with a popup.
  useEffect(() => {
    if (isLoading || error || !schedule || schedule.slots_booked || booked) return
    const timer = setTimeout(() => setShowNoticePopup(true), 5000)
    return () => clearTimeout(timer)
  }, [isLoading, error, schedule, booked])

  const bookMutation = useMutation({
    mutationFn: () =>
      bookSlots(clientId!, {
        client_name: schedule?.poc_name ?? '',
        slots: Array.from(pickedLabels.values()),
      }),
    onSuccess: () => setBooked(true),
  })

  const toggleSlot = (date: Date, timeStr: string) => {
    const key = slotKey(date, timeStr)
    setPickedKeys(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
    setPickedLabels(prev => {
      const next = new Map(prev)
      if (next.has(key)) next.delete(key)
      else next.set(key, makeLabel(date, timeStr))
      return next
    })
  }

  const prevMonth = () =>
    setViewDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1))
  const nextMonth = () =>
    setViewDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1))

  // ── Loading / error / success ──────────────────────────────────────────────

  if (isLoading) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <div className="animate-spin w-8 h-8 border-2 border-blue-600 border-t-transparent rounded-full" />
      </div>
    )
  }

  if (error || !schedule) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center">
        <p className="text-gray-400">This scheduling link is invalid or has expired.</p>
      </div>
    )
  }

  if (schedule.slots_booked) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-6">
        <div className="max-w-sm w-full text-center">
          <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg className="w-7 h-7 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">Slots already submitted!</h2>
          <p className="text-sm text-gray-500 leading-relaxed">
            You have already shared your availability. Our team is reviewing the candidates and will reach out to you shortly.
          </p>
          <p className="mt-8 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    )
  }

  const SCHEDULE_FUNNEL = [
    { key: 'form_submitted',  label: 'Form Submitted',  status: 'done'    as const },
    { key: 'agreement',       label: 'Agreement',        status: 'done'    as const },
    { key: 'profile_sharing', label: 'Profile Sharing',  status: 'active'  as const },
    { key: 'interviews',      label: 'Interviews',       status: 'pending' as const },
    { key: 'hired',           label: 'Hired',            status: 'pending' as const },
  ]

  if (booked) {
    return (
      <div className="min-h-screen bg-white flex items-center justify-center p-6">
        <div className="max-w-sm w-full text-center">
          <div className="w-14 h-14 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-5">
            <svg className="w-7 h-7 text-green-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
          </div>
          <h2 className="text-xl font-semibold text-gray-900 mb-2">You're confirmed!</h2>
          <p className="text-sm text-gray-500 mb-8">
            A calendar invitation has been sent. We'll reach out shortly with the candidate details.
          </p>

          <div className="bg-gray-50 rounded-xl p-5">
            <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-4">Your Hiring Progress</p>
            <HiringFunnel stages={SCHEDULE_FUNNEL} />
          </div>

          <p className="mt-8 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    )
  }

  // ── Calendar data ──────────────────────────────────────────────────────────

  const calDays = getCalendarDays(viewDate.getFullYear(), viewDate.getMonth())
  const canGoPrev =
    viewDate.getFullYear() > today.getFullYear() ||
    viewDate.getMonth() > today.getMonth()

  const needsMoreSlots = pickedKeys.size > 0 && pickedKeys.size < MIN_SLOTS

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-white">
      {/* Notice popup — surfaces the booking notice 5s after the page loads */}
      {showNoticePopup && (
        <div
          className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-6"
          onClick={() => setShowNoticePopup(false)}
        >
          <div
            className="bg-white rounded-2xl max-w-sm w-full p-6 shadow-xl"
            onClick={e => e.stopPropagation()}
          >
            <div className="w-11 h-11 bg-blue-100 rounded-full flex items-center justify-center mb-4 text-lg">
              📅
            </div>
            <h3 className="text-base font-semibold text-gray-900 mb-2">Before you pick your slots</h3>
            <p className="text-sm text-gray-500 leading-relaxed mb-5">
              Slots for today are unavailable, to ensure candidates receive sufficient advance notice before their interviews.
            </p>
            <button
              onClick={() => setShowNoticePopup(false)}
              className="w-full bg-blue-600 hover:bg-blue-700 text-white font-semibold py-2.5 rounded-xl text-sm transition-colors"
            >
              Got it
            </button>
          </div>
        </div>
      )}

      <div className="max-w-4xl mx-auto min-h-screen flex flex-col md:flex-row">

        {/* ── Left info panel ── */}
        <div className="md:w-72 shrink-0 border-b md:border-b-0 md:border-r border-gray-200 px-8 py-10">
          <img
            src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg"
            alt="Just Accountants"
            className="w-10 h-10 rounded-xl object-cover mb-5"
          />
          <p className="text-xs text-gray-400 uppercase tracking-widest mb-1">Just Accountants</p>
          <h1 className="text-lg font-bold text-gray-900 leading-snug mb-4">
            {schedule.company_name}
          </h1>


          <div className="mt-8 pt-8 border-t border-gray-100">
            <p className="text-xs text-gray-400 leading-relaxed">
              Select one or more slots that work best for you. We'll confirm the final time.
            </p>
          </div>
        </div>

        {/* ── Right panel ── */}
        <div className="flex-1 px-6 md:px-10 py-10">

          {/* Booking notice — why nearby dates are greyed out */}
          <div className="flex gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3 mb-3">
            <span className="text-blue-500 text-base shrink-0">📅</span>
            <p className="text-xs text-blue-700 leading-relaxed">
              Today's slots are unavailable, to give candidates enough advance notice before their interviews.
            </p>
          </div>

          {/* Tip banner — candidate-availability suggestion */}
          <div className="flex gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3 mb-6">
            <span className="text-amber-500 text-base shrink-0">💡</span>
            <p className="text-xs text-amber-700 leading-relaxed">
              <span className="font-semibold">Tip:</span> Most candidates are free <span className="font-semibold">before 11 AM or after 6 PM</span> — including a few slots in those windows can help.
            </p>
          </div>

          {/* Month navigation */}
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-base font-semibold text-gray-900">
              {MONTHS[viewDate.getMonth()]} {viewDate.getFullYear()}
            </h2>
            <div className="flex gap-1">
              <button
                onClick={prevMonth}
                disabled={!canGoPrev}
                className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed"
              >
                <svg className="w-4 h-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
              </button>
              <button
                onClick={nextMonth}
                className="w-8 h-8 flex items-center justify-center rounded-full hover:bg-gray-100"
              >
                <svg className="w-4 h-4 text-gray-600" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                </svg>
              </button>
            </div>
          </div>

          {/* Calendar grid */}
          <div className="grid grid-cols-7 mb-2">
            {DAYS_SHORT.map(d => (
              <div key={d} className="text-center text-xs font-medium text-gray-400 py-1">{d}</div>
            ))}
          </div>
          <div className="grid grid-cols-7 gap-y-1 mb-8">
            {calDays.map((day, i) => {
              if (!day) return <div key={i} />
              const cellDate = new Date(viewDate.getFullYear(), viewDate.getMonth(), day)
              // Disabled if the date falls before the earliest selectable date —
              // covers today, giving clients MIN_NOTICE_DAYS to prepare.
              const isPast = cellDate < earliestSelectableDate
              const isToday = isSameDay(cellDate, today)
              const isSelected = selectedDate ? isSameDay(cellDate, selectedDate) : false

              return (
                <button
                  key={i}
                  onClick={() => !isPast && setSelectedDate(cellDate)}
                  disabled={isPast}
                  className={`
                    mx-auto w-9 h-9 flex items-center justify-center rounded-full text-sm transition-colors
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
          {selectedDate && (
            <div>
              <p className="text-sm font-medium text-gray-700 mb-3">
                {selectedDate.toLocaleDateString('en-IN', { weekday: 'long', day: 'numeric', month: 'long' })}
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-8">
                {TIME_SLOTS.map(timeStr => {
                  const key = slotKey(selectedDate, timeStr)
                  const picked = pickedKeys.has(key)
                  return (
                    <button
                      key={timeStr}
                      onClick={() => toggleSlot(selectedDate, timeStr)}
                      className={`
                        py-2.5 px-3 rounded-lg border text-sm font-medium transition-all
                        ${picked
                          ? 'bg-blue-600 border-blue-600 text-white'
                          : 'border-gray-200 text-gray-700 hover:border-blue-400 hover:text-blue-600'}
                      `}
                    >
                      {timeStr}
                    </button>
                  )
                })}
              </div>
            </div>
          )}

          {!selectedDate && (
            <p className="text-sm text-gray-400 mb-8">Select a date to see available times.</p>
          )}

          {/* Selected slots chips */}
          {pickedKeys.size > 0 && (
            <div className="mb-6">
              <p className="text-xs font-medium text-gray-500 mb-2">Selected slots</p>
              {needsMoreSlots && (
                <p className="text-xs text-amber-600 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2 mb-3">
                  {`Please pick at least ${MIN_SLOTS} slots so more candidates can attend — you've picked ${pickedKeys.size} so far.`}
                </p>
              )}
              <div className="flex flex-wrap gap-2">
                {Array.from(pickedLabels.entries()).map(([key, label]) => (
                  <span
                    key={key}
                    className="inline-flex items-center gap-1.5 text-xs bg-blue-50 text-blue-700 border border-blue-100 rounded-full px-3 py-1"
                  >
                    {label}
                    <button
                      onClick={() => {
                        setPickedKeys(prev => { const n = new Set(prev); n.delete(key); return n })
                        setPickedLabels(prev => { const n = new Map(prev); n.delete(key); return n })
                      }}
                      className="text-blue-400 hover:text-blue-700 ml-0.5"
                    >×</button>
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Submit */}
          <div className="border-t border-gray-100 pt-6 max-w-sm">
            <button
              onClick={() => bookMutation.mutate()}
              disabled={pickedKeys.size === 0 || needsMoreSlots || bookMutation.isPending}
              className="w-full bg-blue-600 hover:bg-blue-700 disabled:bg-gray-100 disabled:text-gray-400 text-white font-semibold py-3 rounded-xl transition-colors text-sm"
            >
              {bookMutation.isPending
                ? 'Confirming…'
                : pickedKeys.size === 0
                ? 'Select at least one slot'
                : needsMoreSlots
                ? `Pick ${MIN_SLOTS - pickedKeys.size} more slot${MIN_SLOTS - pickedKeys.size > 1 ? 's' : ''} (${MIN_SLOTS} min)`
                : `Confirm ${pickedKeys.size} slot${pickedKeys.size > 1 ? 's' : ''}`}
            </button>
            {bookMutation.isError && (
              <p className="text-red-500 text-xs text-center">
                {(bookMutation.error as any)?.response?.data?.detail || 'Something went wrong. Please try again.'}
              </p>
            )}
          </div>

          <p className="mt-8 text-xs text-gray-300">Powered by Just Accountants</p>
        </div>
      </div>
    </div>
  )
}
