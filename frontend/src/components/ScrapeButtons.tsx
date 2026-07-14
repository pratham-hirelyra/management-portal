import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { formatDistanceToNow } from 'date-fns'
import { api } from '../api/axios'
import type { ScrapingJob } from '../types'

const SCRAPER_TYPES = ['job_portal', 'apollo', 'google_maps', 'custom'] as const

interface Props {
  clientId: string
}

export default function ScrapeButtons({ clientId }: Props) {
  const qc = useQueryClient()

  const { data: jobs = [] } = useQuery<ScrapingJob[]>({
    queryKey: ['scraping', clientId],
    queryFn: () => api.get(`/clients/${clientId}/scrape`).then(r => r.data.data),
    refetchInterval: (query) => {
      const d = query.state.data as ScrapingJob[] | undefined
      return d?.some(j => j.status === 'running') ? 3000 : false
    },
  })

  const trigger = useMutation({
    mutationFn: (type: string) => api.post(`/clients/${clientId}/scrape/${type}`).then(r => r.data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scraping', clientId] })
      qc.invalidateQueries({ queryKey: ['clients'] })
    },
  })

  return (
    <div className="flex flex-wrap gap-2">
      {SCRAPER_TYPES.map(type => {
        const job = jobs.find(j => j.scraper_type === type)
        const status = job?.status ?? 'idle'
        const label = type.replace(/_/g, ' ')

        let btnClass = 'px-3 py-1.5 text-xs rounded-lg font-medium transition-colors '
        let icon: React.ReactNode = null
        let disabled = false

        if (status === 'running') {
          btnClass += 'bg-blue-50 text-blue-600 cursor-not-allowed'
          icon = <span className="mr-1 inline-block h-3 w-3 animate-spin rounded-full border-2 border-blue-500 border-t-transparent" />
          disabled = true
        } else if (status === 'completed') {
          btnClass += 'bg-green-50 text-green-700 cursor-not-allowed'
          icon = <span className="mr-1">✓</span>
          disabled = true
        } else if (status === 'failed') {
          btnClass += 'bg-red-50 text-red-600 hover:bg-red-100'
        } else {
          btnClass += 'bg-gray-100 text-gray-700 hover:bg-gray-200'
        }

        return (
          <div key={type} className="flex flex-col items-center gap-0.5">
            <button
              className={btnClass}
              disabled={disabled || trigger.isPending}
              onClick={() => trigger.mutate(type)}
            >
              {icon}{label}
            </button>
            {status === 'completed' && job?.completed_at && (
              <span className="text-[10px] text-gray-400">
                {formatDistanceToNow(new Date(job.completed_at), { addSuffix: true })}
              </span>
            )}
            {status === 'failed' && job?.error_msg && (
              <span className="text-[10px] text-red-400 max-w-[80px] truncate" title={job.error_msg}>
                {job.error_msg}
              </span>
            )}
          </div>
        )
      })}
    </div>
  )
}
