import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { getTemplates } from '../api/whatsapp'
import type { WATemplate } from '../types'

interface Props {
  senderType?: 'client' | 'candidate'
  prefill?: Record<string, string>  // "1" → value for {{1}}, "2" → value for {{2}}, etc.
  onSend: (templateName: string, lang: string, variables: string[], header?: { url: string; type: string }) => void
  onClose: () => void
  loading?: boolean
  title?: string
}

function extractVarCount(text: string): number {
  return (text.match(/\{\{\d+\}\}/g) ?? []).length
}

function applyVars(text: string, vars: string[]): string {
  return text.replace(/\{\{(\d+)\}\}/g, (_, n) => vars[Number(n) - 1] ?? `{{${n}}}`)
}

export default function TemplatePickerModal({
  senderType = 'client',
  prefill = {},
  onSend,
  onClose,
  loading = false,
  title = 'Select WhatsApp Template',
}: Props) {
  const { data: templates = [], isLoading } = useQuery({
    queryKey: ['wa-templates', senderType],
    queryFn: () => getTemplates(senderType),
    staleTime: 5 * 60 * 1000,
  })

  const [selected, setSelected] = useState<WATemplate | null>(null)
  const [vars, setVars] = useState<string[]>([])
  const [headerUrl, setHeaderUrl] = useState('')
  const [search, setSearch] = useState('')

  const approved = templates.filter(
    t => t.status?.toUpperCase() === 'APPROVED' &&
    t.name.toLowerCase().includes(search.toLowerCase())
  )

  const bodyText = selected?.components.find(c => c.type === 'BODY')?.text ?? ''
  const headerText = selected?.components.find(c => c.type === 'HEADER')?.text ?? ''
  const footerText = selected?.components.find(c => c.type === 'FOOTER')?.text ?? ''
  const varCount = extractVarCount(bodyText)

  const headerComponent = selected?.components.find(c => c.type === 'HEADER')
  const headerFormat = headerComponent?.format?.toUpperCase() ?? ''
  const isMediaHeader = ['IMAGE', 'VIDEO', 'DOCUMENT'].includes(headerFormat)

  const handleSelect = (t: WATemplate) => {
    setSelected(t)
    setHeaderUrl('')
    const count = extractVarCount(
      t.components.find(c => c.type === 'BODY')?.text ?? ''
    )
    setVars(Array.from({ length: count }, (_, i) => prefill[String(i + 1)] ?? ''))
  }

  const previewBody = applyVars(bodyText, vars)

  const handleSend = () => {
    if (!selected) return
    const header = isMediaHeader && headerUrl.trim()
      ? { url: headerUrl.trim(), type: headerFormat.toLowerCase() }
      : undefined
    onSend(selected.name, selected.language ?? 'en', vars, header)
  }

  const allFilled =
    (varCount === 0 || vars.every(v => v.trim() !== '')) &&
    (!isMediaHeader || headerUrl.trim() !== '')

  return (
    <div className="fixed inset-0 bg-black/40 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl w-full max-w-2xl max-h-[88vh] flex flex-col shadow-xl">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 shrink-0">
          <h2 className="font-semibold text-gray-900 text-sm">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-xl leading-none">×</button>
        </div>

        <div className="flex flex-1 min-h-0">
          {/* Template list */}
          <div className="w-52 border-r border-gray-100 flex flex-col shrink-0">
            <div className="p-2 border-b border-gray-100">
              <input
                className="w-full text-xs border border-gray-200 rounded-lg px-2 py-1.5 focus:outline-none focus:ring-1 focus:ring-blue-300"
                placeholder="Search templates…"
                value={search}
                onChange={e => setSearch(e.target.value)}
              />
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-0.5">
              {isLoading ? (
                <p className="text-xs text-gray-400 p-2">Loading…</p>
              ) : approved.length === 0 ? (
                <p className="text-xs text-gray-400 p-2">No approved templates.</p>
              ) : approved.map(t => (
                <button
                  key={t.name}
                  onClick={() => handleSelect(t)}
                  className={`w-full text-left px-3 py-2 rounded-lg transition-colors ${
                    selected?.name === t.name
                      ? 'bg-blue-50 text-blue-700'
                      : 'hover:bg-gray-50 text-gray-700'
                  }`}
                >
                  <p className="text-xs font-medium truncate">{t.name}</p>
                  <p className="text-[10px] text-gray-400 mt-0.5">{t.category}</p>
                </button>
              ))}
            </div>
          </div>

          {/* Template detail */}
          <div className="flex-1 overflow-y-auto p-5">
            {!selected ? (
              <div className="h-full flex items-center justify-center">
                <p className="text-sm text-gray-400">Select a template to preview it</p>
              </div>
            ) : (
              <div className="space-y-5">
                {/* WhatsApp preview bubble */}
                <div>
                  <p className="text-xs font-medium text-gray-500 mb-2">Preview</p>
                  <div className="bg-[#e9f5e1] rounded-2xl rounded-tl-sm px-4 py-3 max-w-xs space-y-1 shadow-sm">
                    {headerText && (
                      <p className="text-xs font-semibold text-gray-800">{headerText}</p>
                    )}
                    <p className="text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">
                      {previewBody || bodyText}
                    </p>
                    {footerText && (
                      <p className="text-[10px] text-gray-500 mt-1">{footerText}</p>
                    )}
                  </div>
                </div>

                {/* Media header URL input */}
                {isMediaHeader && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2">
                      Header {headerFormat.charAt(0) + headerFormat.slice(1).toLowerCase()} URL
                      <span className="text-red-400 ml-0.5">*</span>
                    </p>
                    <input
                      className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                      value={headerUrl}
                      placeholder={`https://… (public ${headerFormat.toLowerCase()} URL)`}
                      onChange={e => setHeaderUrl(e.target.value)}
                    />
                  </div>
                )}

                {/* Variable inputs */}
                {varCount > 0 && (
                  <div>
                    <p className="text-xs font-medium text-gray-500 mb-2">Fill Variables</p>
                    <div className="space-y-2">
                      {Array.from({ length: varCount }, (_, i) => (
                        <div key={i} className="flex items-center gap-2">
                          <span className="text-[10px] text-gray-400 w-8 shrink-0 font-mono">{`{{${i + 1}}}`}</span>
                          <input
                            className="flex-1 border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-300"
                            value={vars[i] ?? ''}
                            placeholder={`Variable ${i + 1}`}
                            onChange={e => {
                              const next = [...vars]
                              next[i] = e.target.value
                              setVars(next)
                            }}
                          />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-gray-100 flex justify-end gap-3 shrink-0">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-gray-600 hover:text-gray-800"
          >
            Cancel
          </button>
          <button
            disabled={!selected || !allFilled || loading}
            onClick={handleSend}
            className="px-5 py-2 bg-blue-600 text-white text-sm rounded-lg hover:bg-blue-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {loading ? 'Sending…' : 'Send'}
          </button>
        </div>
      </div>
    </div>
  )
}
