import { format } from 'date-fns'
import type { PipelineEvent } from '../types'
import { stageLabel } from '../types'

interface Props {
  events: PipelineEvent[]
}

function AutoMatchEntry({ ev }: { ev: PipelineEvent }) {
  const includeMuslim = ev.details?.filters?.include_muslim
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium text-gray-800">Auto-Match Run</span>
        <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${includeMuslim ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-600'}`}>
          Muslim candidates: {includeMuslim ? 'included' : 'excluded'}
        </span>
      </div>
      {ev.details?.matched !== undefined && (
        <p className="mt-0.5 text-xs text-gray-500">Pool after match: {ev.details.matched} candidates</p>
      )}
    </>
  )
}

export default function StageTimeline({ events }: Props) {
  if (!events.length) return <p className="text-sm text-gray-400">No stage changes yet.</p>

  return (
    <ol className="relative border-l border-gray-200 ml-3">
      {events.map((ev) => (
        <li key={ev.id} className="mb-6 ml-6">
          <span className={`absolute -left-2.5 flex h-5 w-5 items-center justify-center rounded-full ring-4 ring-white ${ev.event_type === 'auto_match' ? 'bg-purple-100' : 'bg-blue-100'}`}>
            <span className={`h-2 w-2 rounded-full ${ev.event_type === 'auto_match' ? 'bg-purple-500' : 'bg-blue-500'}`} />
          </span>

          {ev.event_type === 'auto_match' ? (
            <AutoMatchEntry ev={ev} />
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-2">
                {ev.from_stage && (
                  <>
                    <span className="text-xs text-gray-500">{stageLabel(ev.from_stage)}</span>
                    <span className="text-gray-300">→</span>
                  </>
                )}
                <span className="text-sm font-medium text-gray-800">{stageLabel(ev.to_stage!)}</span>
              </div>
              {ev.note && <p className="mt-0.5 text-xs text-gray-500">{ev.note}</p>}
            </>
          )}

          <time className="mt-0.5 block text-xs text-gray-400">
            {format(new Date(ev.created_at), 'dd MMM yyyy, HH:mm')} · {ev.created_by}
          </time>
        </li>
      ))}
    </ol>
  )
}
