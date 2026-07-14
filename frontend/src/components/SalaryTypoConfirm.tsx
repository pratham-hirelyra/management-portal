// Most roles on this portal pay roughly ₹10,000–₹1,00,000/month — a value outside that
// band is far more likely a misplaced zero ("150000" meant as "15000", or "1500" meant
// as "15000") than a genuine salary, so we nudge for a one-time confirmation.
export const SALARY_TYPO_LOW = 10000
export const SALARY_TYPO_HIGH = 100000

export interface SalaryTypoFlag {
  direction: 'low' | 'high'
  suggested: number
}

export function salaryTypoFlag(value: number | string | null | undefined): SalaryTypoFlag | null {
  const n = typeof value === 'string' ? Number(value) : value
  if (n == null || !Number.isFinite(n) || n <= 0) return null

  if (n > SALARY_TYPO_HIGH) {
    const suggested = Math.round(n / 10)
    if (suggested >= 1000) return { direction: 'high', suggested }
  }
  if (n < SALARY_TYPO_LOW) {
    return { direction: 'low', suggested: Math.round(n * 10) }
  }
  return null
}

interface Props {
  value: number | string | null | undefined
  onUseSuggested: (suggested: number) => void
  onConfirmAsIs: () => void
}

// Inline "confirm once" nudge — appears only when a salary value looks like a typo
// (an extra or missing zero). Submission should stay gated until the person picks one.
export default function SalaryTypoConfirm({ value, onUseSuggested, onConfirmAsIs }: Props) {
  const n = typeof value === 'string' ? Number(value) : value
  const flag = salaryTypoFlag(value)
  if (n == null || flag == null) return null

  const fmt = (x: number) => `₹${x.toLocaleString('en-IN')}`
  const sizeWord = flag.direction === 'high' ? 'unusually high' : 'unusually low'

  return (
    <div className="mt-1.5 text-xs bg-amber-50 border border-amber-200 rounded-md px-2.5 py-2 text-amber-800">
      <p>
        That's <strong>{fmt(n)}/month</strong> — {sizeWord} for this role.
        Did you mean <strong>{fmt(flag.suggested)}</strong>?
      </p>
      <div className="flex gap-2 mt-1.5">
        <button
          type="button"
          onClick={() => onUseSuggested(flag.suggested)}
          className="px-2 py-0.5 rounded bg-amber-600 text-white hover:bg-amber-700 font-medium"
        >
          Use {fmt(flag.suggested)}
        </button>
        <button
          type="button"
          onClick={onConfirmAsIs}
          className="px-2 py-0.5 rounded border border-amber-300 text-amber-700 hover:bg-amber-100"
        >
          No, {fmt(n)} is correct
        </button>
      </div>
    </div>
  )
}
