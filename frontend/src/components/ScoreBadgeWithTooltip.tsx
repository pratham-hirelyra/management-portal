import { useState, useRef } from 'react'
import { createPortal } from 'react-dom'
import { scoreBadge } from '../types'

export interface ScoreCriterion { score: number; max: number }
export type ScoreBreakdown = Record<string, ScoreCriterion>

const CRITERION_LABELS: Record<string, string> = {
  technical:  'Technical',
  proximity:  'Proximity',
  salary_pos: 'Salary fit',
  software:   'Software',
  gst_tds:    'GST / TDS',
}

const TOOLTIP_W = 176 // w-44 = 11rem = 176px

interface TooltipProps {
  breakdown: ScoreBreakdown
  anchorRect: DOMRect
}

function BreakdownTooltip({ breakdown, anchorRect }: TooltipProps) {
  const entries = Object.entries(breakdown) as [string, ScoreCriterion][]

  // Compute left so tooltip stays within viewport with 8px padding
  const badgeCenterX = anchorRect.left + anchorRect.width / 2
  const rawLeft = badgeCenterX - TOOLTIP_W / 2
  const left = Math.max(8, Math.min(rawLeft, window.innerWidth - TOOLTIP_W - 8))
  // Arrow points at badge center regardless of tooltip clamping
  const arrowLeft = Math.max(8, Math.min(badgeCenterX - left - 4, TOOLTIP_W - 16))
  // Render above the badge, anchored by bottom edge
  const bottom = window.innerHeight - anchorRect.top + 8

  return createPortal(
    <div
      className="fixed z-[9999] w-44 bg-gray-900 text-white text-[11px] rounded-lg shadow-xl p-2.5 pointer-events-none"
      style={{ left, bottom }}
    >
      <p className="font-semibold mb-1.5 text-gray-200">Score breakdown</p>
      {entries.map(([key, { score, max }]) => {
        const pct = max > 0 ? score / max : 0
        const barColor = pct >= 0.75 ? 'bg-green-400' : pct >= 0.4 ? 'bg-amber-400' : 'bg-red-400'
        return (
          <div key={key} className="flex items-center gap-1.5 mb-1">
            <span className="w-16 text-gray-400 shrink-0">{CRITERION_LABELS[key] ?? key}</span>
            <div className="flex-1 bg-gray-700 rounded-full h-1.5">
              <div className={`${barColor} h-1.5 rounded-full`} style={{ width: `${Math.max(0, pct) * 100}%` }} />
            </div>
            <span className="w-8 text-right text-gray-300">{score}/{max}</span>
          </div>
        )
      })}
      {/* Arrow pointing down to the badge */}
      <div
        className="absolute top-full border-4 border-transparent border-t-gray-900"
        style={{ left: arrowLeft }}
      />
    </div>,
    document.body,
  )
}

interface Props {
  score: number
  breakdown?: ScoreBreakdown | null
}

export default function ScoreBadgeWithTooltip({ score, breakdown }: Props) {
  const [anchorRect, setAnchorRect] = useState<DOMRect | null>(null)
  const ref = useRef<HTMLSpanElement>(null)

  return (
    <>
      <span
        ref={ref}
        className={`text-xs px-2 py-0.5 rounded-full font-medium ${breakdown ? 'cursor-help' : ''} ${scoreBadge(score)}`}
        onMouseEnter={() => {
          if (breakdown && ref.current) setAnchorRect(ref.current.getBoundingClientRect())
        }}
        onMouseLeave={() => setAnchorRect(null)}
      >
        {score}%
      </span>
      {anchorRect && breakdown && <BreakdownTooltip breakdown={breakdown} anchorRect={anchorRect} />}
    </>
  )
}
