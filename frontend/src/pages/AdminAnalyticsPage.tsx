import { useState, Fragment } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { api } from '../api/axios'

// ── Types ─────────────────────────────────────────────────────────────────────

interface ClientRow {
  batch_date: string; sub_type: string; batch_size: number; note?: string | null
  delivered: number; responded: number; positive: number
  agreement: number; matchmaking: number; interview: number; placement: number
}

interface CandRow {
  batch_date: string; sub_type: string; batch_size: number
  delivered: number
  not_looking: number; not_interested: number
  positive: number; form: number; ai_call: number
  passed: number; junior: number; senior: number
}

interface MatchmakingClient {
  id: string; company_name: string; job_title: string; segment: string
  open_positions: number; onboarded_at: string | null; tat_hours: number | null
  pool_size: number; interested_count: number; interviews_booked: number; total_mapped: number
}

interface ScrapeFunnelRow {
  batch_date: string
  scraped: number; numbers_found: number; not_found: number; recruiters_excluded: number
  find_rate: number
}

interface DrillState {
  entity: 'clients' | 'candidates' | 'scrape-funnel'; col: string
  batch_date: string | null; sub_type: string | null; title: string
  city?: string | null
}

interface ChecklistBatch { batch_date: string; tasks: Record<string, string> }
interface BatchChecklist { client: ChecklistBatch[]; candidate: ChecklistBatch[] }

// ── Constants ─────────────────────────────────────────────────────────────────

const CLIENT_SUB_TYPES = [
  { key: 'job_post',     label: 'Job Post',     color: '#f97316', heat: 'Hot',  heatCls: 'text-orange-500 bg-orange-50' },
  { key: 'ca_referral',  label: 'CA Referral',  color: '#3b82f6', heat: 'Cold', heatCls: 'text-blue-500 bg-blue-50' },
  { key: 'candidate_cv', label: 'Candidate CV', color: '#22c55e', heat: 'Hot',  heatCls: 'text-orange-500 bg-orange-50' },
  { key: 'inbound',      label: 'Inbound',      color: '#a855f7', heat: 'Warm', heatCls: 'text-yellow-600 bg-yellow-50' },
  { key: 'others',       label: 'Others',       color: '#94a3b8', heat: '—',    heatCls: 'text-gray-400 bg-gray-50' },
]

const CAND_SUB_TYPES = [
  { key: 'sourced',          label: 'Sourced',        color: '#2563eb' },
  { key: 'from_database',    label: 'From Database',  color: '#64748b' },
  { key: 'without_ai_score', label: 'w/o AI Score',   color: '#7c3aed' },
  { key: 'with_ai_score',    label: 'w/ AI Score',    color: '#16a34a' },
]

const CLIENT_TASKS = [
  { id: 'enrichment_complete', name: 'Lead enrichment completed',         meta: 'contact found before outreach' },
  { id: 'first_delivered',     name: '1st creative delivered',            meta: 'WhatsApp message #1 sent' },
  { id: 'full_sequence',       name: 'Full 12-creative sequence sent',    meta: 'all paced creatives fired' },
  { id: 'rereach_3h',          name: 'Re-reach @ T+3h (form stuck)',      meta: 'nudge for incomplete form' },
  { id: 'rereach_6h',          name: 'Re-reach @ T+6h (form stuck)',      meta: '2nd form nudge' },
  { id: 'rereach_24h',         name: 'Re-reach @ T+24h (form stuck)',     meta: 'final form nudge' },
  { id: 'agreement_issued',    name: 'Agreement link issued',             meta: 'on form submit' },
  { id: 'agreement_rereach',   name: 'Re-reach T+3/6/24h (agreement)',   meta: 'agreement nudges' },
  { id: 'jd_generated',        name: 'Dynamic JD generated',             meta: 'branded JD to matchmaking' },
  { id: 'mini_frontend',       name: 'Mini-frontend link shared',        meta: 'pre-interview candidate pack' },
  { id: 'interview_slot',      name: 'Interview slot link shared',       meta: 'to interested clients' },
  { id: 'rereach_slot',        name: 'Re-reach for slot booking',        meta: 'if slot not picked' },
  { id: 'reminders',           name: 'Interview reminders (T−1d/T−2h)', meta: 'client + candidate' },
  { id: 'feedback',            name: 'Feedback links sent',              meta: 'both parties post-interview' },
]

const CAND_TASKS = [
  { id: 'jd_delivered',   name: 'JD message delivered',             meta: 'WhatsApp JD to candidate' },
  { id: 'reping_3h',      name: 'Re-ping @ T+3h',                   meta: 'no-response / incomplete' },
  { id: 'reping_6h',      name: 'Re-ping @ T+6h',                   meta: '2nd nudge' },
  { id: 'reping_24h',     name: 'Re-ping @ T+24h',                  meta: 'final nudge' },
  { id: 'form_resume',    name: 'Form + resume collected',           meta: 'before AI call' },
  { id: 'ai_call',        name: 'AI call placed (5–6 min)',         meta: 'on form+resume received' },
  { id: 'retry_3h',       name: 'AI call retry @ T+3h',             meta: 'no pick-up' },
  { id: 'retry_6h',       name: 'AI call retry @ T+6h',             meta: '2nd retry' },
  { id: 'retry_24h',      name: 'AI call retry @ T+24h',            meta: 'final retry' },
  { id: 'call_complete',  name: 'AI call completed (not dropped)',   meta: 'full 5-min assessment' },
  { id: 'classification', name: 'Classification completed',         meta: 'Jr / Sr / Rejected assigned' },
  { id: 'coaching',       name: 'Coaching report sent',             meta: 'to passed candidates' },
  { id: 'counter_q',      name: 'Counter-questions handled',        meta: 'manual replies (for now)' },
  { id: 'back_trigger',   name: 'Back-trigger to matchmaking',      meta: 'pass candidates returned' },
]

const POOL_TARGET = 12

// ── Helpers ───────────────────────────────────────────────────────────────────

function pct(n: number, base: number): string {
  if (!base) return '—'
  return Math.round((n / base) * 100) + '%'
}

function pctCls(n: number, base: number): string {
  if (!base) return 'text-gray-400'
  const p = (n / base) * 100
  if (p >= 30) return 'text-green-600'
  if (p >= 10) return 'text-amber-600'
  return 'text-red-500'
}

function fmtDate(d: string): string {
  try { return new Date(d + 'T00:00:00').toLocaleDateString('en-IN', { day: 'numeric', month: 'short' }) }
  catch { return d }
}

function segmentLabel(seg: string): string {
  const m: Record<string, string> = { employer_lead: 'Cand. CV', active_job_post: 'Job Post', ca_network: 'CA Ref', inbound: 'Inbound' }
  return m[seg] || seg || '—'
}

function stageCls(stage: string): string {
  const m: Record<string, string> = {
    lead: 'bg-gray-100 text-gray-600', interested: 'bg-orange-100 text-orange-700',
    agreement_sent: 'bg-purple-100 text-purple-700', onboarded: 'bg-green-100 text-green-700',
    disqualified: 'bg-red-100 text-red-600', reachout_sent: 'bg-amber-100 text-amber-700',
  }
  return m[stage] || 'bg-gray-100 text-gray-500'
}

function evalBadge(s: string | null): { label: string; cls: string } {
  if (!s) return { label: 'Pending', cls: 'bg-gray-100 text-gray-500' }
  const l = s.toLowerCase()
  if (l === 'pass' || l === 'passed') return { label: 'Pass', cls: 'bg-green-100 text-green-700' }
  if (l === 'fail' || l === 'failed') return { label: 'Fail', cls: 'bg-red-100 text-red-600' }
  return { label: s, cls: 'bg-gray-100 text-gray-500' }
}

function taskBadge(status: string): { label: string; cls: string; dot: string } {
  switch (status) {
    case 'done':    return { label: 'Done',    cls: 'bg-green-100 text-green-700',  dot: 'bg-green-500' }
    case 'pending': return { label: 'Pending', cls: 'bg-amber-100 text-amber-600',  dot: 'bg-amber-400' }
    case 'failed':  return { label: 'Failed',  cls: 'bg-red-100 text-red-600',      dot: 'bg-red-500' }
    default:        return { label: '—',       cls: 'bg-gray-100 text-gray-400',    dot: 'bg-gray-300' }
  }
}

type ClientTotals = Omit<ClientRow, 'batch_date' | 'sub_type'>
type CandTotals = Omit<CandRow, 'batch_date' | 'sub_type'>

function sumClients(rows: ClientRow[]): ClientTotals {
  return rows.reduce((a, r) => ({
    batch_size: a.batch_size + r.batch_size, delivered: a.delivered + r.delivered,
    responded: a.responded + r.responded, positive: a.positive + r.positive,
    agreement: a.agreement + r.agreement, matchmaking: a.matchmaking + r.matchmaking,
    interview: a.interview + r.interview, placement: a.placement + r.placement,
  }), { batch_size: 0, delivered: 0, responded: 0, positive: 0, agreement: 0, matchmaking: 0, interview: 0, placement: 0 })
}

function sumCands(rows: CandRow[]): CandTotals {
  return rows.reduce((a, r) => ({
    batch_size: a.batch_size + r.batch_size, delivered: a.delivered + r.delivered,
    not_looking: a.not_looking + r.not_looking, not_interested: a.not_interested + r.not_interested,
    positive: a.positive + r.positive, form: a.form + r.form,
    ai_call: a.ai_call + r.ai_call, passed: a.passed + r.passed,
    junior: a.junior + r.junior, senior: a.senior + r.senior,
  }), { batch_size: 0, delivered: 0, not_looking: 0, not_interested: 0, positive: 0, form: 0, ai_call: 0, passed: 0, junior: 0, senior: 0 })
}

function groupBy<T extends { batch_date: string }>(rows: T[]): Map<string, T[]> {
  const m = new Map<string, T[]>()
  for (const r of rows) { if (!m.has(r.batch_date)) m.set(r.batch_date, []); m.get(r.batch_date)!.push(r) }
  return m
}

function aggregateClients(rows: ClientRow[]): Record<string, ClientRow> {
  const acc: Record<string, ClientRow> = {}
  for (const st of CLIENT_SUB_TYPES)
    acc[st.key] = { batch_date: 'TOTAL', sub_type: st.key, batch_size: 0, delivered: 0, responded: 0, positive: 0, agreement: 0, matchmaking: 0, interview: 0, placement: 0 }
  for (const r of rows) {
    const a = acc[r.sub_type]; if (!a) continue
    a.batch_size += r.batch_size; a.delivered += r.delivered; a.responded += r.responded
    a.positive += r.positive; a.agreement += r.agreement; a.matchmaking += r.matchmaking
    a.interview += r.interview; a.placement += r.placement
  }
  return acc
}

function aggregateCands(rows: CandRow[]): Record<string, CandRow> {
  const acc: Record<string, CandRow> = {}
  for (const st of CAND_SUB_TYPES)
    acc[st.key] = { batch_date: 'TOTAL', sub_type: st.key, batch_size: 0, delivered: 0, not_looking: 0, not_interested: 0, positive: 0, form: 0, ai_call: 0, passed: 0, junior: 0, senior: 0 }
  for (const r of rows) {
    const a = acc[r.sub_type]; if (!a) continue
    a.batch_size += r.batch_size; a.delivered += r.delivered
    a.not_looking += r.not_looking
    a.not_interested += r.not_interested; a.positive += r.positive; a.form += r.form
    a.ai_call += r.ai_call; a.passed += r.passed; a.junior += r.junior; a.senior += r.senior
  }
  return acc
}

// ── UI Primitives ─────────────────────────────────────────────────────────────

function Spinner() {
  return <div className="flex justify-center py-16"><div className="w-5 h-5 border-2 border-blue-500 border-t-transparent rounded-full animate-spin" /></div>
}

type BM = 'all' | 'split'

function BatchToggle({ value, onChange }: { value: BM; onChange: (v: BM) => void }) {
  return (
    <div className="flex bg-gray-100 rounded-lg p-0.5 gap-0.5">
      {([['all', 'All batches'], ['split', 'Split by batch']] as [BM, string][]).map(([k, l]) => (
        <button key={k} onClick={() => onChange(k)}
          className={`px-3 py-1.5 rounded text-xs font-medium transition-colors ${value === k ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
          {l}
        </button>
      ))}
    </div>
  )
}

function NC({ n, base, label, onClick }: { n: number; base?: number; label?: string; onClick?: () => void }) {
  const body = (
    <div className="text-center">
      <div className="text-sm font-semibold text-gray-800">{n}</div>
      {base !== undefined && <div className={`text-[10px] mt-0.5 ${pctCls(n, base)}`}>{pct(n, base)}{label ? ` of ${label}` : ''}</div>}
    </div>
  )
  return (
    <td className="px-3 py-2.5 text-center">
      {onClick && n > 0
        ? <button onClick={onClick} className="hover:bg-blue-50 rounded px-1.5 py-0.5 w-full transition-colors">{body}</button>
        : <div className="px-1.5 py-0.5">{body}</div>}
    </td>
  )
}

function RateTd({ n, d }: { n: number; d: number }) {
  const p = d ? Math.round(n / d * 100) : 0
  const cls = p >= 25 ? 'bg-green-100 text-green-700' : p >= 10 ? 'bg-amber-100 text-amber-600' : 'bg-red-100 text-red-600'
  return <td className="px-3 py-2.5 text-center"><span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>{p}%</span></td>
}

function RatePill({ n, d, lo = 50 }: { n: number; d: number; lo?: number }) {
  const p = d ? Math.round(n / d * 100) : 0
  const cls = p >= lo ? 'bg-green-100 text-green-700' : p >= lo / 2 ? 'bg-amber-100 text-amber-600' : 'bg-red-100 text-red-600'
  return <span className={`text-[11px] font-semibold px-1.5 py-0.5 rounded ${cls}`}>{p}%</span>
}

// ── DrillModal ────────────────────────────────────────────────────────────────

function DrillModal({ drill, onClose }: { drill: DrillState; onClose: () => void }) {
  const drillUrl = drill.entity === 'scrape-funnel' ? '/analytics/new/clients/scrape-funnel/records' : `/analytics/new/${drill.entity}/records`
  const { data, isLoading } = useQuery({
    queryKey: ['analytics-drill', drill.entity, drill.col, drill.batch_date, drill.sub_type, drill.city],
    queryFn: () => api.get(drillUrl, {
      params: {
        col: drill.col,
        ...(drill.batch_date && { batch_date: drill.batch_date }),
        ...(drill.sub_type && { sub_type: drill.sub_type }),
        ...(drill.city && { city: drill.city }),
      }
    }).then(r => r.data.data),
  })

  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/30" onClick={onClose} />
      <div className="w-[400px] bg-white flex flex-col h-full shadow-2xl">
        <div className="px-5 py-4 border-b border-gray-200 flex items-start justify-between">
          <div>
            <p className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">
              {drill.batch_date ? fmtDate(drill.batch_date) : 'All Time'}
            </p>
            <h2 className="text-sm font-bold text-gray-900 mt-0.5">{drill.title}</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>
        <div className="flex-1 overflow-y-auto">
          {isLoading ? <Spinner /> : !data?.length ? (
            <div className="flex items-center justify-center h-32 text-sm text-gray-400">No records</div>
          ) : drill.entity === 'clients' || drill.entity === 'scrape-funnel' ? (
            <ul className="divide-y divide-gray-100">
              {data.map((r: any) => (
                <li key={r.id} className="px-5 py-3 hover:bg-gray-50">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <Link to={`/clients/${r.id}`} onClick={onClose} className="text-sm font-semibold text-blue-600 hover:underline truncate block">{r.company_name || '—'}</Link>
                      <p className="text-xs text-gray-400 mt-0.5 truncate">{r.job_title || '—'}</p>
                    </div>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium shrink-0 ${stageCls(r.stage)}`}>{(r.stage || '').replace(/_/g, ' ')}</span>
                  </div>
                  {r.poc_phone && <p className="text-[11px] text-gray-400 mt-1">{r.poc_phone}</p>}
                </li>
              ))}
            </ul>
          ) : (
            <ul className="divide-y divide-gray-100">
              {data.map((r: any) => {
                const ev = evalBadge(r.evaluation_status)
                return (
                  <li key={r.id} className="px-5 py-3 hover:bg-gray-50">
                    <div className="flex items-start justify-between gap-2">
                      <div className="min-w-0">
                        <p className="text-sm font-semibold text-gray-800 truncate">{r.name || '—'}</p>
                        <p className="text-xs text-gray-400 mt-0.5">{r.phone || '—'}</p>
                      </div>
                      <div className="flex flex-col items-end gap-1 shrink-0">
                        <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${ev.cls}`}>{ev.label}</span>
                        {r.category && <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 text-gray-500 font-medium">{r.category === 'Senior Accountant' ? 'Senior' : 'Junior'}</span>}
                      </div>
                    </div>
                  </li>
                )
              })}
            </ul>
          )}
        </div>
        {data && <div className="px-5 py-3 border-t border-gray-100 text-xs text-gray-400">{data.length} record{data.length !== 1 ? 's' : ''}</div>}
      </div>
    </div>
  )
}

// ── ScrapeFunnelTable ─────────────────────────────────────────────────────────

function ScrapeFunnelTable() {
  const [drill, setDrill] = useState<DrillState | null>(null)
  const { data: rawRows, isLoading } = useQuery({
    queryKey: ['analytics-scrape-funnel'],
    queryFn: () => api.get('/analytics/new/clients/scrape-funnel').then(r => r.data.data as ScrapeFunnelRow[]),
  })

  if (isLoading) return <Spinner />
  const rows = rawRows ?? []
  if (!rows.length) return <div className="flex items-center justify-center h-32 bg-gray-50 rounded-xl border border-dashed border-gray-200"><p className="text-sm text-gray-400">No scraping data yet</p></div>

  const open = (col: string, batch_date: string | null, title: string) =>
    setDrill({ entity: 'scrape-funnel', col, batch_date, sub_type: null, title })

  const totals = rows.reduce((a, r) => ({
    scraped: a.scraped + r.scraped, numbers_found: a.numbers_found + r.numbers_found,
    not_found: a.not_found + r.not_found, recruiters_excluded: a.recruiters_excluded + r.recruiters_excluded,
  }), { scraped: 0, numbers_found: 0, not_found: 0, recruiters_excluded: 0 })

  return (
    <>
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Batch (scraped)</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Scraped</th>
                <th className="px-3 py-3 text-center text-green-600 font-semibold uppercase tracking-wider">Numbers<br />Found</th>
                <th className="px-3 py-3 text-center text-red-500 font-semibold uppercase tracking-wider">Not<br />Found</th>
                <th className="px-3 py-3 text-center text-gray-400 font-semibold uppercase tracking-wider">Recruiters<br />Excluded</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Find<br />Rate</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.batch_date} className="hover:bg-gray-50/60 border-b border-gray-100">
                  <td className="px-4 py-2.5 text-xs font-medium text-gray-700">{fmtDate(r.batch_date)}</td>
                  <NC n={r.scraped} onClick={() => open('scraped', r.batch_date, `${fmtDate(r.batch_date)} — Scraped`)} />
                  <NC n={r.numbers_found} base={r.scraped} label="scraped" onClick={() => open('numbers_found', r.batch_date, `${fmtDate(r.batch_date)} — Numbers Found`)} />
                  <NC n={r.not_found} base={r.scraped} label="scraped" onClick={() => open('not_found', r.batch_date, `${fmtDate(r.batch_date)} — Not Found`)} />
                  <NC n={r.recruiters_excluded} base={r.scraped} label="scraped" onClick={() => open('recruiters_excluded', r.batch_date, `${fmtDate(r.batch_date)} — Recruiters Excluded`)} />
                  <RateTd n={r.numbers_found} d={r.scraped} />
                </tr>
              ))}
              <tr className="bg-gray-100 border-t-2 border-gray-200">
                <td className="px-4 py-2.5 text-xs font-bold text-gray-700">Grand total</td>
                <NC n={totals.scraped} onClick={() => open('scraped', null, 'Grand Total — Scraped')} />
                <NC n={totals.numbers_found} base={totals.scraped} label="scraped" onClick={() => open('numbers_found', null, 'Grand Total — Numbers Found')} />
                <NC n={totals.not_found} base={totals.scraped} label="scraped" onClick={() => open('not_found', null, 'Grand Total — Not Found')} />
                <NC n={totals.recruiters_excluded} base={totals.scraped} label="scraped" onClick={() => open('recruiters_excluded', null, 'Grand Total — Recruiters Excluded')} />
                <RateTd n={totals.numbers_found} d={totals.scraped} />
              </tr>
            </tbody>
          </table>
        </div>
      </div>
      {drill && <DrillModal drill={drill} onClose={() => setDrill(null)} />}
    </>
  )
}

// ── ClientFunnel ──────────────────────────────────────────────────────────────

function ClientFunnel({ rows }: { rows: ClientRow[] }) {
  const t = sumClients(rows)
  const sent = t.batch_size || 1
  const del = t.delivered || 1
  const stages = [
    { label: 'Sent',              n: t.batch_size,  color: '#64748b', base: sent, baseLabel: 'total' },
    { label: 'Delivered',         n: t.delivered,   color: '#3b82f6', base: sent, baseLabel: 'sent' },
    { label: 'Responded',         n: t.responded,   color: '#06b6d4', base: del,  baseLabel: 'del.' },
    { label: 'Positive Response', n: t.positive,    color: '#10b981', base: del,  baseLabel: 'del.' },
    { label: 'Agreement Signed',  n: t.agreement,   color: '#eab308', base: del,  baseLabel: 'del.' },
    { label: 'In Matchmaking',    n: t.matchmaking, color: '#f59e0b', base: del,  baseLabel: 'del.' },
    { label: 'Interview',         n: t.interview,   color: '#f97316', base: del,  baseLabel: 'del.' },
    { label: 'Placement',         n: t.placement,   color: '#ef4444', base: del,  baseLabel: 'del.' },
  ]
  return (
    <div className="bg-white border border-gray-200 rounded-xl p-5 space-y-3">
      {stages.map((s, i) => {
        const w = Math.round(s.n / sent * 100)
        const prev = i === 0 ? s.n : stages[i - 1].n
        const drop = prev - s.n
        const dropPct = prev ? Math.round(drop / prev * 100) : 0
        const pctOfBase = Math.round(s.n / s.base * 100)
        return (
          <div key={s.label} className="flex items-center gap-4">
            <div className="w-36 text-xs text-gray-600 flex items-center gap-1.5 shrink-0">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: s.color }} />
              {s.label}
            </div>
            <div className="flex-1 bg-gray-100 rounded h-7 relative overflow-hidden">
              <div className="h-full rounded flex items-center px-2 text-xs font-semibold text-white"
                style={{ width: `${Math.max(w, 3)}%`, background: s.color, minWidth: 32 }}>
                {s.n}
              </div>
            </div>
            <div className="w-52 text-xs text-gray-500 shrink-0">
              <span className="font-semibold text-gray-700">{pctOfBase}%</span> of {s.baseLabel}
              {i > 0 && drop > 0 && <span className="ml-2 text-red-500">−{drop} ({dropPct}%) drop</span>}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── BatchChecklist ────────────────────────────────────────────────────────────

function BatchChecklist({ tasks, batches }: { tasks: { id: string; name: string; meta: string }[]; batches: ChecklistBatch[] }) {
  if (!batches.length) return <p className="text-sm text-gray-400 py-4 text-center">No batch data available</p>
  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-50 border-b border-gray-200">
            <tr>
              <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider min-w-[240px]">Backend Task</th>
              {batches.map((b, i) => (
                <th key={b.batch_date} className="px-4 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider min-w-[100px]">
                  Batch {i + 1}<br /><span className="text-gray-400 font-normal normal-case">{fmtDate(b.batch_date)}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {tasks.map(task => (
              <tr key={task.id} className="hover:bg-gray-50">
                <td className="px-4 py-3">
                  <div className="font-medium text-gray-700">{task.name}</div>
                  <div className="text-[10px] text-gray-400 mt-0.5">{task.meta}</div>
                </td>
                {batches.map(b => {
                  const { label, cls, dot } = taskBadge(b.tasks[task.id] || 'unknown')
                  return (
                    <td key={b.batch_date} className="px-4 py-3 text-center">
                      <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium ${cls}`}>
                        <span className={`w-1.5 h-1.5 rounded-full ${dot}`} />{label}
                      </span>
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── SectionHead ───────────────────────────────────────────────────────────────

function SectionHead({ icon, title, tag, bg }: { icon: string; title: string; tag: string; bg: string }) {
  return (
    <div className="flex items-center gap-2 mb-3">
      <span className={`w-7 h-7 flex items-center justify-center rounded-lg text-sm ${bg}`}>{icon}</span>
      <h2 className="text-sm font-bold text-gray-900">{title}</h2>
      <span className="text-xs text-gray-400 bg-gray-100 px-2 py-0.5 rounded">{tag}</span>
    </div>
  )
}

// ── ClientWorkflow ────────────────────────────────────────────────────────────

function ClientWorkflow() {
  const [batchMode, setBatchMode] = useState<BM>('all')
  const [drill, setDrill] = useState<DrillState | null>(null)
  const [cityFilter, setCityFilter] = useState<string>('')

  const { data: cityRows } = useQuery({
    queryKey: ['analytics-city-clients'],
    queryFn: () => api.get('/analytics/city/clients').then(r => r.data.data as CityClientRow[]),
    staleTime: 60_000,
  })
  const cityOptions = (cityRows ?? []).filter(r => r.city !== 'unknown').sort((a, b) => b.lead_count - a.lead_count)
  const unclassifiedCount = (cityRows ?? []).find(r => r.city === 'unknown')?.lead_count ?? 0

  const { data: rawRows, isLoading } = useQuery({
    queryKey: ['analytics-client-breakdown', cityFilter],
    queryFn: () => api.get('/analytics/new/clients/breakdown', { params: { city: cityFilter || undefined } }).then(r => r.data.data as ClientRow[]),
  })
  const { data: checklist } = useQuery({
    queryKey: ['analytics-batch-checklist'],
    queryFn: () => api.get('/analytics/new/batch-checklist').then(r => r.data.data as BatchChecklist),
    staleTime: 60_000,
  })

  const citySelector = (
    <div className="flex items-center gap-2">
      <span className="text-xs text-gray-500 font-medium">City</span>
      <select
        value={cityFilter}
        onChange={e => setCityFilter(e.target.value)}
        className="text-xs border border-gray-200 rounded-lg px-2 py-1.5 bg-white text-gray-700 font-medium"
      >
        <option value="">All cities</option>
        {cityOptions.map(c => (
          <option key={c.city} value={c.city}>{c.city} ({c.lead_count})</option>
        ))}
      </select>
      {unclassifiedCount > 0 && (
        <span className="text-[10px] text-gray-400">{unclassifiedCount} clients not yet geocoded</span>
      )}
    </div>
  )

  if (isLoading) return <>{citySelector}<Spinner /></>
  const rows = rawRows ?? []
  if (!rows.length) return <div className="space-y-3">{citySelector}<div className="flex items-center justify-center h-40 bg-gray-50 rounded-xl border border-dashed border-gray-200"><p className="text-sm text-gray-400">No client outreach data{cityFilter ? ` for ${cityFilter}` : ''} yet</p></div></div>

  const open = (col: string, batch_date: string | null, sub_type: string | null, title: string) =>
    setDrill({ entity: 'clients', col, batch_date, sub_type, title, city: cityFilter || null })

  const batchMap = groupBy(rows)
  const batchDates = Array.from(batchMap.keys()) // newest first from API
  const sorted = [...batchDates].sort()
  const dateRange = sorted.length > 1 ? `${fmtDate(sorted[0])} – ${fmtDate(sorted[sorted.length - 1])}` : sorted.length === 1 ? fmtDate(sorted[0]) : ''

  const combined = aggregateClients(rows)

  const renderGroups: { label: string; rows: ClientRow[]; bd: string | null }[] = batchMode === 'all'
    ? [{ label: 'All batches combined', rows: Object.values(combined), bd: null }]
    : batchDates.map((bd, i) => ({ label: `Batch ${batchDates.length - i} · ${fmtDate(bd)}`, rows: batchMap.get(bd)!, bd }))

  const renderSubRow = (r: ClientRow, bd: string | null) => {
    const meta = CLIENT_SUB_TYPES.find(s => s.key === r.sub_type)
    if (!meta) return null
    const del = r.delivered
    return (
      <tr key={r.sub_type} className="hover:bg-gray-50/60 border-b border-gray-100">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: meta.color }} />
            <span className="text-xs font-medium text-gray-700">{meta.label}</span>
            <span className={`text-[10px] px-1 py-0.5 rounded font-medium ${meta.heatCls}`}>{meta.heat}</span>
          </div>
        </td>
        <NC n={r.batch_size} onClick={() => open('total', bd, r.sub_type, `${meta.label} — All`)} />
        <NC n={r.delivered} base={r.batch_size} label="size" onClick={() => open('delivered', bd, r.sub_type, `${meta.label} — Delivered`)} />
        <NC n={r.responded} base={del} label="del." onClick={() => open('responded', bd, r.sub_type, `${meta.label} — Responded`)} />
        <NC n={r.positive} base={del} label="del." onClick={() => open('positive', bd, r.sub_type, `${meta.label} — Positive`)} />
        <NC n={r.agreement} base={del} label="del." onClick={() => open('agreement_signed', bd, r.sub_type, `${meta.label} — Agreement`)} />
        <NC n={r.matchmaking} base={del} label="del." onClick={() => open('in_matchmaking', bd, r.sub_type, `${meta.label} — Matchmaking`)} />
        <NC n={r.interview} base={del} label="del." onClick={() => open('interview', bd, r.sub_type, `${meta.label} — Interview`)} />
        <NC n={r.placement} base={del} label="del." onClick={() => open('placement', bd, r.sub_type, `${meta.label} — Placement`)} />
        <RateTd n={r.agreement} d={del} />
        <RateTd n={r.placement} d={r.agreement} />
      </tr>
    )
  }

  const renderGrandRow = (t: ClientTotals, bd: string | null) => {
    const del = t.delivered
    return (
      <tr className="bg-gray-100 border-t-2 border-gray-200">
        <td className="px-4 py-2.5 text-xs font-bold text-gray-700">Grand total</td>
        <NC n={t.batch_size}   onClick={() => open('total',        bd, null, 'Grand Total — All')} />
        <NC n={t.delivered}    base={t.batch_size} label="size" onClick={() => open('delivered',     bd, null, 'Grand Total — Delivered')} />
        <NC n={t.responded}    base={del} label="del." onClick={() => open('responded',    bd, null, 'Grand Total — Responded')} />
        <NC n={t.positive}     base={del} label="del." onClick={() => open('positive',     bd, null, 'Grand Total — Positive')} />
        <NC n={t.agreement}    base={del} label="del." onClick={() => open('agreement_signed', bd, null, 'Grand Total — Agreement')} />
        <NC n={t.matchmaking}  base={del} label="del." onClick={() => open('in_matchmaking',  bd, null, 'Grand Total — Matchmaking')} />
        <NC n={t.interview}    base={del} label="del." onClick={() => open('interview',    bd, null, 'Grand Total — Interview')} />
        <NC n={t.placement}    base={del} label="del." onClick={() => open('placement',    bd, null, 'Grand Total — Placement')} />
        <RateTd n={t.agreement} d={del} />
        <RateTd n={t.placement} d={t.agreement} />
      </tr>
    )
  }

  const clientChecklist = [...(checklist?.client ?? [])].sort((a, b) => a.batch_date.localeCompare(b.batch_date))

  return (
    <div className="space-y-6">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-white border border-gray-200 rounded-xl">
        <span className="text-xs text-gray-500 font-medium">Batches</span>
        <BatchToggle value={batchMode} onChange={setBatchMode} />
        <div className="ml-2 pl-3 border-l border-gray-200">{citySelector}</div>
        <span className="ml-2 text-xs text-gray-400 flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
          Click any number to open the client list at that stage
        </span>
      </div>

      {/* Table */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[960px]">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Batch / Sub-type</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Batch<br />Size</th>
                <th className="px-3 py-3 text-center text-blue-600 font-semibold uppercase tracking-wider">Delivered<br /><span className="text-gray-400 font-normal normal-case text-[10px]">(1st-time only)</span></th>
                <th className="px-3 py-3 text-center text-cyan-600 font-semibold uppercase tracking-wider">Responded</th>
                <th className="px-3 py-3 text-center text-green-600 font-semibold uppercase tracking-wider">Positive</th>
                <th className="px-3 py-3 text-center text-yellow-600 font-semibold uppercase tracking-wider">Agreement</th>
                <th className="px-3 py-3 text-center text-amber-600 font-semibold uppercase tracking-wider">Matchmaking</th>
                <th className="px-3 py-3 text-center text-orange-600 font-semibold uppercase tracking-wider">Interview</th>
                <th className="px-3 py-3 text-center text-red-500 font-semibold uppercase tracking-wider">Placement</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Onboarding<br />Rate %</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Placement<br />Success %</th>
              </tr>
            </thead>
            <tbody>
              {renderGroups.map((g, gi) => {
                const totals = sumClients(g.rows)
                const note = g.rows.find(r => r.note)?.note
                return (
                  <Fragment key={gi}>
                    <tr className="bg-gray-50 border-b border-gray-100">
                      <td colSpan={11} className="px-4 py-2">
                        <span className="text-xs font-semibold text-gray-700">{g.label}</span>
                        {batchMode === 'all' && dateRange && <span className="ml-2 text-[10px] text-gray-400 bg-gray-200 px-2 py-0.5 rounded">{dateRange}</span>}
                        {note && <span className="ml-2 text-[10px] text-amber-700 bg-amber-100 px-2 py-0.5 rounded font-medium">⚠ {note}</span>}
                      </td>
                    </tr>
                    {CLIENT_SUB_TYPES.map(st => {
                      const r = g.rows.find(row => row.sub_type === st.key)
                        ?? { batch_date: '', sub_type: st.key, batch_size: 0, delivered: 0, responded: 0, positive: 0, agreement: 0, matchmaking: 0, interview: 0, placement: 0 }
                      return renderSubRow(r, g.bd)
                    })}
                    {renderGrandRow(totals, g.bd)}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      {/* Legend */}
      <div className="flex flex-wrap gap-3 px-1">
        {CLIENT_SUB_TYPES.map(s => (
          <span key={s.key} className="flex items-center gap-1.5 text-xs text-gray-600">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />{s.label}
            <span className="text-gray-400">({s.heat})</span>
          </span>
        ))}
        <span className="text-xs text-gray-400 ml-1">Heat = likelihood the client is actively hiring</span>
      </div>

      {/* Scrape → Enrich funnel */}
      <div>
        <SectionHead icon="🔍" title="Lead Scraping — Numbers Found" tag="batched by day scraped · before any reachout" bg="bg-teal-100 text-teal-700" />
        <ScrapeFunnelTable />
      </div>

      {/* Funnel */}
      <div>
        <SectionHead icon="▾" title="Client Funnel — till date" tag="all batches combined · through to placement" bg="bg-purple-100 text-purple-700" />
        <ClientFunnel rows={rows} />
      </div>

      {/* Checklist */}
      <div>
        <SectionHead icon="✓" title="Batch Task Checklist" tag="backend tasks that break the funnel if they fail" bg="bg-green-100 text-green-700" />
        <BatchChecklist tasks={CLIENT_TASKS} batches={clientChecklist} />
      </div>

      <p className="text-xs text-gray-400 border-t border-gray-100 pt-3">
        <b>How to read:</b> table % is on <b>Delivered</b>. Funnel: <b>Delivered %</b> = Delivered ÷ Sent · all other stages % = ÷ Delivered. <b>Onboarding Rate</b> = Agreement ÷ Delivered · <b>Placement Success</b> = Placement ÷ Agreement.
      </p>

      {drill && <DrillModal drill={drill} onClose={() => setDrill(null)} />}
    </div>
  )
}

// ── CandidateWorkflow ─────────────────────────────────────────────────────────

function CandidateWorkflow() {
  const [batchMode, setBatchMode] = useState<BM>('all')
  const [drill, setDrill] = useState<DrillState | null>(null)

  const { data: rawRows, isLoading } = useQuery({
    queryKey: ['analytics-cand-breakdown'],
    queryFn: () => api.get('/analytics/new/candidates/breakdown').then(r => r.data.data as CandRow[]),
  })
  const { data: checklist } = useQuery({
    queryKey: ['analytics-batch-checklist'],
    queryFn: () => api.get('/analytics/new/batch-checklist').then(r => r.data.data as BatchChecklist),
    staleTime: 60_000,
  })

  if (isLoading) return <Spinner />
  const rows = rawRows ?? []
  if (!rows.length) return <div className="flex items-center justify-center h-40 bg-gray-50 rounded-xl border border-dashed border-gray-200"><p className="text-sm text-gray-400">No candidate outreach data yet</p></div>

  const open = (col: string, bd: string | null, sub_type: string | null, title: string) =>
    setDrill({ entity: 'candidates', col, batch_date: bd, sub_type, title })

  const batchMap = groupBy(rows)
  const batchDates = Array.from(batchMap.keys())
  const sorted = [...batchDates].sort()
  const dateRange = sorted.length > 1 ? `${fmtDate(sorted[0])} – ${fmtDate(sorted[sorted.length - 1])}` : sorted.length === 1 ? fmtDate(sorted[0]) : ''

  const combined = aggregateCands(rows)

  const renderGroups: { label: string; rows: CandRow[]; bd: string | null }[] = batchMode === 'all'
    ? [{ label: 'All batches combined', rows: Object.values(combined), bd: null }]
    : batchDates.map((bd, i) => ({ label: `Batch ${batchDates.length - i} · ${fmtDate(bd)}`, rows: batchMap.get(bd)!, bd }))

  const renderSubRow = (r: CandRow, bd: string | null) => {
    const meta = CAND_SUB_TYPES.find(s => s.key === r.sub_type)
    if (!meta) return null
    const del = r.delivered
    const isWoAI = r.sub_type === 'without_ai_score'
    // form_submitted is always true for these buckets by definition (their entry
    // status was already form_filled/passed/failed) — showing it is redundant
    const formIsRedundant = r.sub_type === 'without_ai_score' || r.sub_type === 'with_ai_score'
    const eng = del ? Math.round((r.not_looking + r.not_interested + r.positive) / del * 100) : 0
    const compl = isWoAI ? (r.positive ? Math.round(r.ai_call / r.positive * 100) : 0) : (r.form ? Math.round(r.ai_call / r.form * 100) : 0)
    const passPct = r.ai_call ? Math.round(r.passed / r.ai_call * 100) : 0

    return (
      <tr key={r.sub_type} className="hover:bg-gray-50/60 border-b border-gray-100">
        <td className="px-4 py-2.5">
          <div className="flex items-center gap-2">
            <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ background: meta.color }} />
            <span className="text-xs font-medium text-gray-700">{meta.label}</span>
          </div>
        </td>
        <NC n={r.batch_size} onClick={() => open('total', bd, r.sub_type, `${meta.label} — All`)} />
        <NC n={r.delivered} base={r.batch_size} label="size" onClick={() => open('delivered', bd, r.sub_type, `${meta.label} — Delivered`)} />
        <NC n={r.not_looking} base={del} label="del." onClick={() => open('not_looking', bd, r.sub_type, `${meta.label} — Not Looking`)} />
        <NC n={r.not_interested} base={del} label="del." onClick={() => open('not_interested_role', bd, r.sub_type, `${meta.label} — Not Interested`)} />
        <NC n={r.positive} base={del} label="del." onClick={() => open('positive', bd, r.sub_type, `${meta.label} — Positive`)} />
        <td className="px-3 py-2.5 text-center"><RatePill n={r.not_looking + r.not_interested + r.positive} d={del} lo={70} /></td>
        {formIsRedundant
          ? <td className="px-3 py-2.5 text-center text-sm text-gray-300">—</td>
          : <NC n={r.form} base={del} label="del." onClick={() => open('form', bd, r.sub_type, `${meta.label} — Form`)} />}
        <NC n={r.ai_call} base={del} label="del." onClick={() => open('ai_call', bd, r.sub_type, `${meta.label} — AI Call`)} />
        <td className="px-3 py-2.5 text-center"><RatePill n={compl} d={100} lo={70} /></td>
        <NC n={r.passed} base={del} label="del." onClick={() => open('passed', bd, r.sub_type, `${meta.label} — Passed`)} />
        <NC n={r.junior} base={r.passed} label="pass" onClick={() => open('junior', bd, r.sub_type, `${meta.label} — Junior`)} />
        <NC n={r.senior} base={r.passed} label="pass" onClick={() => open('senior', bd, r.sub_type, `${meta.label} — Senior`)} />
        <td className="px-3 py-2.5 text-center"><RatePill n={passPct} d={100} lo={50} /></td>
      </tr>
    )
  }

  const candChecklist = [...(checklist?.candidate ?? [])].sort((a, b) => a.batch_date.localeCompare(b.batch_date))

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-3 px-4 py-3 bg-white border border-gray-200 rounded-xl">
        <span className="text-xs text-gray-500 font-medium">Batches</span>
        <BatchToggle value={batchMode} onChange={setBatchMode} />
        <span className="text-xs text-gray-400 flex items-center gap-1.5 ml-auto">
          <span className="w-1.5 h-1.5 rounded-full bg-blue-400 inline-block" />
          Click any number to open the candidate list
        </span>
      </div>

      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-xs min-w-[1100px]">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Batch / Sub-type</th>
                <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Batch<br />Size</th>
                <th className="px-3 py-3 text-center text-blue-600 font-semibold uppercase tracking-wider">Delivered</th>
                <th className="px-3 py-3 text-center font-semibold uppercase tracking-wider" style={{ color: '#a855f7' }}>Not Looking<br /><span className="font-normal normal-case text-gray-400 text-[10px]">(A)</span></th>
                <th className="px-3 py-3 text-center text-slate-500 font-semibold uppercase tracking-wider">Not Interested<br /><span className="font-normal normal-case text-gray-400 text-[10px]">(B)</span></th>
                <th className="px-3 py-3 text-center text-green-600 font-semibold uppercase tracking-wider">Positive<br /><span className="font-normal normal-case text-gray-400 text-[10px]">(C)</span></th>
                <th className="px-3 py-3 text-center text-cyan-600 font-semibold uppercase tracking-wider">Engagement<br /><span className="font-normal normal-case text-gray-400 text-[10px]">(A+B+C)/Del</span></th>
                <th className="px-3 py-3 text-center text-green-500 font-semibold uppercase tracking-wider">Form</th>
                <th className="px-3 py-3 text-center text-orange-500 font-semibold uppercase tracking-wider">AI Call</th>
                <th className="px-3 py-3 text-center text-cyan-400 font-semibold uppercase tracking-wider">Call Compl.</th>
                <th className="px-3 py-3 text-center font-semibold uppercase tracking-wider" style={{ color: '#4ade80' }}>Passed</th>
                <th className="px-3 py-3 text-center font-semibold uppercase tracking-wider" style={{ color: '#a3e635' }}>Jr. Acct<br /><span className="font-normal normal-case text-gray-400 text-[10px]">of passed</span></th>
                <th className="px-3 py-3 text-center font-semibold uppercase tracking-wider" style={{ color: '#facc15' }}>Sr. Acct<br /><span className="font-normal normal-case text-gray-400 text-[10px]">of passed</span></th>
                <th className="px-3 py-3 text-center font-semibold uppercase tracking-wider" style={{ color: '#34d399' }}>Pass %</th>
              </tr>
            </thead>
            <tbody>
              {renderGroups.map((g, gi) => {
                const t = sumCands(g.rows)
                const del = t.delivered
                const eng = del ? Math.round((t.not_looking + t.not_interested + t.positive) / del * 100) : 0
                const compl = t.form ? Math.round(t.ai_call / t.form * 100) : 0
                const passPct = t.ai_call ? Math.round(t.passed / t.ai_call * 100) : 0
                return (
                  <Fragment key={gi}>
                    <tr className="bg-gray-50 border-b border-gray-100">
                      <td colSpan={14} className="px-4 py-2">
                        <span className="text-xs font-semibold text-gray-700">{g.label}</span>
                        {batchMode === 'all' && dateRange && <span className="ml-2 text-[10px] text-gray-400 bg-gray-200 px-2 py-0.5 rounded">{dateRange}</span>}
                      </td>
                    </tr>
                    {CAND_SUB_TYPES.map(st => {
                      const r = g.rows.find(row => row.sub_type === st.key)
                        ?? { batch_date: '', sub_type: st.key, batch_size: 0, delivered: 0, not_looking: 0, not_interested: 0, positive: 0, form: 0, ai_call: 0, passed: 0, junior: 0, senior: 0 }
                      return renderSubRow(r, g.bd)
                    })}
                    <tr className="bg-gray-100 border-t-2 border-gray-200">
                      <td className="px-4 py-2.5 text-xs font-bold text-gray-700">Grand total</td>
                      <td className="px-3 py-2.5 text-center text-sm font-bold text-gray-800">{t.batch_size}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.delivered}<div className={`text-[10px] ${pctCls(t.delivered, t.batch_size)}`}>{pct(t.delivered, t.batch_size)} of size</div></td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.not_looking}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.not_interested}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.positive}</td>
                      <td className="px-3 py-2.5 text-center"><RatePill n={eng} d={100} lo={70} /></td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.form}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.ai_call}</td>
                      <td className="px-3 py-2.5 text-center"><RatePill n={compl} d={100} lo={70} /></td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.passed}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.junior}</td>
                      <td className="px-3 py-2.5 text-center text-sm font-semibold text-gray-800">{t.senior}</td>
                      <td className="px-3 py-2.5 text-center"><RatePill n={passPct} d={100} lo={50} /></td>
                    </tr>
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 px-1">
        {CAND_SUB_TYPES.map(s => (
          <span key={s.key} className="flex items-center gap-1.5 text-xs text-gray-600">
            <span className="w-2.5 h-2.5 rounded-full" style={{ background: s.color }} />{s.label}
          </span>
        ))}
      </div>

      <div>
        <SectionHead icon="✓" title="Batch Task Checklist" tag="candidate-side backend hygiene" bg="bg-pink-100 text-pink-700" />
        <BatchChecklist tasks={CAND_TASKS} batches={candChecklist} />
      </div>

      <p className="text-xs text-gray-400 border-t border-gray-100 pt-3">
        <b>A</b> = Not looking · <b>B</b> = Not interested in role · <b>C</b> = Positive (→ AI call). <b>Engagement</b> = (A+B+C) ÷ Delivered. <b>Call Completion</b> = AI calls ÷ Form. <b>Pass %</b> = Passed ÷ Calls.
      </p>

      {drill && <DrillModal drill={drill} onClose={() => setDrill(null)} />}
    </div>
  )
}

// ── MatchmakingWorkflow ───────────────────────────────────────────────────────

function MatchmakingWorkflow() {
  const { data, isLoading } = useQuery({
    queryKey: ['analytics-new-matchmaking'],
    queryFn: () => api.get('/analytics/new/matchmaking').then(r => r.data.data as MatchmakingClient[]),
  })

  if (isLoading) return <Spinner />
  const clients = data ?? []
  if (!clients.length) return <div className="flex items-center justify-center h-40 bg-gray-50 rounded-xl border border-dashed border-gray-200"><p className="text-sm text-gray-400">No clients in matchmaking yet</p></div>

  const atRisk = clients.filter(c => (c.tat_hours ?? 0) >= 42 && c.pool_size < POOL_TARGET).length
  const avgTat = clients.length ? Math.round(clients.reduce((s, c) => s + (c.tat_hours ?? 0), 0) / clients.length) : 0
  const totalInterviews = clients.reduce((s, c) => s + (c.interviews_booked || 0), 0)

  function getStatus(c: MatchmakingClient) {
    const h = c.tat_hours ?? 0
    if (c.pool_size >= POOL_TARGET) return { label: 'Filled ✓', bg: 'rgba(34,197,94,.13)', cls: 'text-green-500' }
    if (h > 48 || (h >= 42 && c.pool_size < 6)) return { label: 'Urgent — intervene', bg: 'rgba(239,68,68,.15)', cls: 'text-red-500' }
    if (h >= 30) return { label: 'Watch', bg: 'rgba(234,179,8,.13)', cls: 'text-amber-500' }
    return { label: 'On track', bg: 'rgba(34,197,94,.12)', cls: 'text-green-400' }
  }

  function getTatPill(c: MatchmakingClient) {
    const h = c.tat_hours ?? 0
    const label = c.pool_size >= POOL_TARGET || h > 48 ? `${Math.round(h)}h` : `${Math.round(h)}h / 48h`
    if (h > 48)  return { label, bg: 'bg-red-100', cls: 'text-red-600' }
    if (h >= 30) return { label, bg: 'bg-amber-100', cls: 'text-amber-600' }
    return { label, bg: 'bg-green-100', cls: 'text-green-600' }
  }

  const segToSubType: Record<string, string> = {
    active_job_post: 'job_post', ca_network: 'ca_referral', employer_lead: 'candidate_cv', inbound: 'inbound',
  }

  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {[
          { label: 'Active clients',    value: clients.length,    sub: `${clients.filter(c => c.pool_size >= POOL_TARGET).length} pool filled · ${clients.filter(c => c.pool_size < POOL_TARGET).length} in progress`, vcls: '' },
          { label: 'At risk (red)',     value: atRisk,            sub: 'TAT approaching / past 48h', vcls: atRisk > 0 ? 'text-red-400' : '' },
          { label: 'Avg TAT (open)',    value: `${avgTat}h`,      sub: 'since agreement signed', vcls: '' },
          { label: 'Interviews booked', value: totalInterviews,   sub: 'across all active clients', vcls: 'text-green-400' },
        ].map(kpi => (
          <div key={kpi.label} className="bg-white border border-gray-200 rounded-xl p-4">
            <p className="text-xs text-gray-500 mb-1">{kpi.label}</p>
            <p className={`text-2xl font-bold ${kpi.vcls || 'text-gray-900'}`}>{kpi.value}</p>
            <p className="text-xs text-gray-400 mt-1">{kpi.sub}</p>
          </div>
        ))}
      </div>

      <div>
        <SectionHead icon="⬡" title="Live Client Status Board" tag="red flag = TAT approaching 48h · close this client fast" bg="bg-green-100 text-green-700" />
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 border-b border-gray-200">
                <tr>
                  <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Client</th>
                  <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Sub-type</th>
                  <th className="px-4 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Recruitment TAT<br /><span className="font-normal normal-case text-gray-400">since agreement</span></th>
                  <th className="px-4 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Pool Depth<br /><span className="font-normal normal-case text-gray-400">MMS&gt;70 / {POOL_TARGET}</span></th>
                  <th className="px-4 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Interviews<br />Booked</th>
                  <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {clients.map(c => {
                  const status = getStatus(c)
                  const tat = getTatPill(c)
                  const poolFill = Math.min(Math.round(c.pool_size / POOL_TARGET * 100), 100)
                  const poolColor = c.pool_size >= POOL_TARGET ? '#22c55e' : c.pool_size >= 8 ? '#84cc16' : '#eab308'
                  const stKey = segToSubType[c.segment] || 'others'
                  const stMeta = CLIENT_SUB_TYPES.find(s => s.key === stKey) ?? CLIENT_SUB_TYPES[4]
                  return (
                    <tr key={c.id} className="hover:bg-gray-50">
                      <td className="px-4 py-3">
                        <Link to={`/clients/${c.id}`} className="text-sm font-semibold text-gray-800 hover:text-blue-600 hover:underline block">{c.company_name}</Link>
                        {c.onboarded_at && <p className="text-[10px] text-gray-400 mt-0.5">{new Date(c.onboarded_at).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}</p>}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-1.5">
                          <span className="w-2 h-2 rounded-full shrink-0" style={{ background: stMeta.color }} />
                          <span className="text-xs text-gray-600">{stMeta.label}</span>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`inline-block text-[11px] font-semibold px-2 py-0.5 rounded ${tat.bg} ${tat.cls}`}>{tat.label}</span>
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-bold" style={{ color: poolColor }}>{c.pool_size}<span className="text-gray-400 font-normal text-xs">/{POOL_TARGET}</span></span>
                          <div className="flex-1 bg-gray-100 rounded-full h-1.5 min-w-[40px]">
                            <div className="h-1.5 rounded-full" style={{ width: `${poolFill}%`, background: poolColor }} />
                          </div>
                        </div>
                      </td>
                      <td className="px-4 py-3 text-center">
                        <span className={`text-sm font-bold ${c.interviews_booked >= 3 ? 'text-green-500' : c.interviews_booked >= 1 ? 'text-amber-500' : 'text-red-400'}`}>{c.interviews_booked}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-[11px] font-semibold px-2 py-0.5 rounded ${status.cls}`} style={{ background: status.bg }}>{status.label}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <p className="text-xs text-gray-400 border-t border-gray-100 pt-3">
        <b>Recruitment TAT</b> = time since agreement signed until {POOL_TARGET}-candidate pool (MMS&gt;70) is filled. <span className="text-green-500 font-medium">Green</span> &lt; 30h · <span className="text-amber-500 font-medium">Amber</span> 30–42h · <span className="text-red-400 font-medium">Red</span> approaching/over 48h. <b>Pool</b> = candidates with MMS&gt;70. <b>Interviews booked</b> = candidates who picked an interview slot.
      </p>
    </div>
  )
}

// ── CityWorkflow ──────────────────────────────────────────────────────────────

interface CityClientRow {
  city: string; lead_count: number; onboarded_count: number
  disqualified_count: number; clients_with_placement: number
}

interface CityCandidateRow {
  city: string; pool_size: number; pass_count: number; fail_count: number
  pass_rate: number; active_count: number; placed_count: number
}

function CityWorkflow() {
  const { data: clientRows, isLoading: loadingClients } = useQuery({
    queryKey: ['analytics-city-clients'],
    queryFn: () => api.get('/analytics/city/clients').then(r => r.data.data as CityClientRow[]),
  })
  const { data: candidateRows, isLoading: loadingCandidates } = useQuery({
    queryKey: ['analytics-city-candidates'],
    queryFn: () => api.get('/analytics/city/candidates').then(r => r.data.data as CityCandidateRow[]),
  })

  if (loadingClients || loadingCandidates) return <Spinner />

  const clients = clientRows ?? []
  const candidates = candidateRows ?? []

  if (!clients.length && !candidates.length) {
    return <div className="flex items-center justify-center h-40 bg-gray-50 rounded-xl border border-dashed border-gray-200"><p className="text-sm text-gray-400">No city data yet</p></div>
  }

  return (
    <div className="space-y-6">
      <div>
        <SectionHead icon="🏙️" title="Clients by City" tag="lead → onboarded funnel, sliced by client city" bg="bg-blue-100 text-blue-700" />
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[640px]">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">City</th>
                  <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Leads</th>
                  <th className="px-3 py-3 text-center text-green-600 font-semibold uppercase tracking-wider">Onboarded</th>
                  <th className="px-3 py-3 text-center text-red-500 font-semibold uppercase tracking-wider">Disqualified</th>
                  <th className="px-3 py-3 text-center text-purple-600 font-semibold uppercase tracking-wider">Clients w/ Placement</th>
                </tr>
              </thead>
              <tbody>
                {clients.map(r => (
                  <tr key={r.city} className="hover:bg-gray-50/60 border-b border-gray-100">
                    <td className="px-4 py-2.5 font-medium text-gray-700">{r.city}</td>
                    <td className="px-3 py-2.5 text-center">{r.lead_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.onboarded_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.disqualified_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.clients_with_placement}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <div>
        <SectionHead icon="👤" title="Candidates by City" tag="pool depth, evaluation pass rate & placements, sliced by candidate city" bg="bg-amber-100 text-amber-700" />
        <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-xs min-w-[720px]">
              <thead>
                <tr className="bg-gray-50 border-b border-gray-200">
                  <th className="px-4 py-3 text-left text-gray-500 font-semibold uppercase tracking-wider">City</th>
                  <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Pool Size</th>
                  <th className="px-3 py-3 text-center text-green-600 font-semibold uppercase tracking-wider">Pass</th>
                  <th className="px-3 py-3 text-center text-red-500 font-semibold uppercase tracking-wider">Fail</th>
                  <th className="px-3 py-3 text-center text-gray-500 font-semibold uppercase tracking-wider">Pass Rate %</th>
                  <th className="px-3 py-3 text-center text-blue-600 font-semibold uppercase tracking-wider">Active</th>
                  <th className="px-3 py-3 text-center text-purple-600 font-semibold uppercase tracking-wider">Placed</th>
                </tr>
              </thead>
              <tbody>
                {candidates.map(r => (
                  <tr key={r.city} className="hover:bg-gray-50/60 border-b border-gray-100">
                    <td className="px-4 py-2.5 font-medium text-gray-700">{r.city}</td>
                    <td className="px-3 py-2.5 text-center">{r.pool_size}</td>
                    <td className="px-3 py-2.5 text-center">{r.pass_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.fail_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.pass_rate}%</td>
                    <td className="px-3 py-2.5 text-center">{r.active_count}</td>
                    <td className="px-3 py-2.5 text-center">{r.placed_count}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      <p className="text-xs text-gray-400 border-t border-gray-100 pt-3">
        <b>City</b> is derived from geocoded addresses (client job location / candidate current location) — rows with no geocoded address yet show as <b>unknown</b>. Activating a new city for lead sourcing is a data change (an <code>active_cities</code> row), not a code change.
      </p>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────

const TABS = [
  { id: 'clients',     label: 'Client Workflow',    sub: 'Batch-wise acquisition funnel · broken down by client sub-type' },
  { id: 'candidates',  label: 'Candidate Workflow',  sub: 'Batch-wise sourcing funnel · vetting · classification' },
  { id: 'matchmaking', label: 'Matchmaking',          sub: 'Per-client pool depth, recruitment speed & interview output' },
  { id: 'city',        label: 'City Breakdown',      sub: 'Client and candidate funnels, sliced by city' },
] as const

type TabId = typeof TABS[number]['id']

export default function AdminAnalyticsPage() {
  const [tab, setTab] = useState<TabId>('clients')
  const active = TABS.find(t => t.id === tab)!

  return (
    <div className="p-6 max-w-full mx-auto">
      <div className="mb-6">
        <h1 className="text-xl font-bold text-gray-900">Analytics</h1>
        <p className="text-sm text-gray-500 mt-0.5">{active.sub}</p>
      </div>
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 mb-6 w-fit">
        {TABS.map(t => (
          <button key={t.id} onClick={() => setTab(t.id)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors whitespace-nowrap ${tab === t.id ? 'bg-white text-gray-900 shadow-sm' : 'text-gray-500 hover:text-gray-700'}`}>
            {t.label}
          </button>
        ))}
      </div>
      {tab === 'clients'     && <ClientWorkflow />}
      {tab === 'candidates'  && <CandidateWorkflow />}
      {tab === 'matchmaking' && <MatchmakingWorkflow />}
      {tab === 'city'        && <CityWorkflow />}
    </div>
  )
}
