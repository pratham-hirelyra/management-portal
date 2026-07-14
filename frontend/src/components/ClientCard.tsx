import { useNavigate } from 'react-router-dom'
import type { Client } from '../types'
import { clientStageBadge, stageLabel } from '../types'

interface Props {
  client: Client
}

export default function ClientCard({ client }: Props) {
  const navigate = useNavigate()

  return (
    <div
      className="bg-white rounded-xl border border-gray-200 p-4 hover:shadow-md cursor-pointer transition-shadow"
      onClick={() => navigate(`/clients/${client.id}`)}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-semibold text-gray-900 truncate">{client.company_name}</p>
          <p className="text-sm text-gray-500 truncate">{client.job_title}</p>
        </div>
        <span className={`shrink-0 text-xs px-2 py-0.5 rounded-full font-medium ${clientStageBadge(client.stage)}`}>
          {stageLabel(client.stage)}
        </span>
      </div>
      <div className="mt-3 flex flex-wrap gap-x-4 gap-y-1 text-xs text-gray-500">
        {client.industry && <span>{client.industry}</span>}
        {client.location && <span>{client.location}</span>}
        {client.poc_name && <span>POC: {client.poc_name}</span>}
      </div>
    </div>
  )
}
