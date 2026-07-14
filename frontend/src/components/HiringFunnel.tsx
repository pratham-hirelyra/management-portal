interface FunnelStage {
  key: string
  label: string
  status: 'done' | 'active' | 'pending'
}

interface Props {
  stages: FunnelStage[]
  className?: string
}

const ICONS: Record<string, string> = {
  form_submitted:  '📋',
  agreement:       '📝',
  profile_sharing: '👤',
  interviews:      '🎯',
  hired:           '🎉',
  applied:         '📋',
  evaluation:      '🔍',
  matching:        '🤝',
  interview:       '🎯',
  placed:          '🎉',
  requirement_submitted: '📋',
  matchmaking_started:   '🚀',
  reaching_out:          '📣',
  waiting_responses:     '⏳',
  candidate_review:      '🔍',
  interview_scheduling:  '📅',
  hiring:                '🤝',
}

export default function HiringFunnel({ stages, className = '' }: Props) {
  return (
    <div className={`w-full ${className}`}>
      <div className="flex items-center justify-between relative">
        {/* Connecting line behind circles */}
        <div className="absolute top-5 left-0 right-0 h-0.5 bg-gray-200 z-0" />

        {stages.map((stage, i) => {
          const isDone    = stage.status === 'done'
          const isActive  = stage.status === 'active'
          const isPending = stage.status === 'pending'

          return (
            <div key={stage.key} className="flex flex-col items-center z-10 flex-1">
              {/* Circle */}
              <div className="relative flex items-center justify-center">
                {isActive && (
                  <span className="absolute inset-0 rounded-full bg-blue-400 opacity-30 animate-ping" />
                )}
                <div
                  className={`
                    w-10 h-10 rounded-full flex items-center justify-center text-base
                    border-2 transition-all duration-300
                    ${isDone    ? 'bg-blue-600 border-blue-600 text-white'        : ''}
                    ${isActive  ? 'bg-white border-blue-600 text-blue-600'        : ''}
                    ${isPending ? 'bg-white border-gray-300 text-gray-400'        : ''}
                  `}
                >
                  {isDone ? (
                    <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
                    </svg>
                  ) : (
                    <span className="text-sm">{ICONS[stage.key] || String(i + 1)}</span>
                  )}
                </div>
              </div>

              {/* Label */}
              <span
                className={`
                  mt-2 text-xs font-medium text-center leading-tight
                  ${isDone   ? 'text-blue-700' : ''}
                  ${isActive ? 'text-blue-600' : ''}
                  ${isPending ? 'text-gray-400' : ''}
                `}
                style={{ maxWidth: 72 }}
              >
                {stage.label}
              </span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
