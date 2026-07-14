import { useEffect } from 'react'
import { BrowserRouter, Routes, Route, NavLink, Navigate, useNavigate, useParams } from 'react-router-dom'
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { getAIVoiceReachoutClients, getFunnelStuckReachoutClients } from './api/clients'
import ClientsPage from './pages/ClientsPage'
import ClientDetailPage from './pages/ClientDetailPage'
import CandidatesPage from './pages/CandidatesPage'
import SchedulePage from './pages/SchedulePage'
import CandidateSchedulePage from './pages/CandidateSchedulePage'
import LoginPage from './pages/LoginPage'
import ClientStatusPage from './pages/ClientStatusPage'
import AdminTemplatesPage from './pages/AdminTemplatesPage'
import AdminAnalyticsPage from './pages/AdminAnalyticsPage'
import AdminRMsPage from './pages/AdminRMsPage'
import ReachoutQueuePage from './pages/ReachoutQueuePage'
import AIVoiceReachoutPage from './pages/AIVoiceReachoutPage'
import FunnelStuckReachoutPage from './pages/FunnelStuckReachoutPage'
import HandoverPage from './pages/HandoverPage'
import FeedbackPage from './pages/FeedbackPage'
import ClientFeedbackPage from './pages/ClientFeedbackPage'
import CandidateFeedbackPage from './pages/CandidateFeedbackPage'
import RMPage from './pages/RMPage'
import ClientPortalPage from './pages/ClientPortalPage'

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1 } },
})

// /apply and /client-onboard moved to the candidate-portal / client-portal
// apps — this catches anyone who lands on the old URL (already-delivered
// WhatsApp messages, bookmarks, approved template example links) and
// forwards them, preserving any query string (?ref=..., ?intent=passive).
function ExternalRedirect({ baseUrl, path }: { baseUrl: string; path: string }) {
  useEffect(() => {
    window.location.href = `${baseUrl.replace(/\/$/, '')}${path}${window.location.search}`
  }, [])
  return null
}

// /agreement/:token and /success/:clientId moved to client-portal — same idea
// as ExternalRedirect above, but the path carries a dynamic segment that must
// be forwarded.
function DynamicExternalRedirect({ baseUrl, pathPrefix, paramName }: { baseUrl: string; pathPrefix: string; paramName: string }) {
  const params = useParams()
  const value = params[paramName]
  useEffect(() => {
    window.location.href = `${baseUrl.replace(/\/$/, '')}${pathPrefix}/${value}${window.location.search}`
  }, [value])
  return null
}

function PrivateRoute({ children }: { children: React.ReactNode }) {
  const isAuth = localStorage.getItem('ja_portal_auth') === '1'
  return isAuth ? <>{children}</> : <Navigate to="/login" replace />
}

export default function App() {
  return (
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <Routes>
          {/* Public pages — no sidebar, no auth */}
          <Route path="/login" element={<LoginPage />} />
          <Route path="/apply" element={<ExternalRedirect baseUrl={import.meta.env.VITE_CANDIDATE_PORTAL_URL} path="/apply" />} />
          <Route path="/client-onboard" element={<ExternalRedirect baseUrl={import.meta.env.VITE_CLIENT_PORTAL_URL} path="/client-onboard" />} />
          <Route path="/status/:clientId" element={<ClientStatusPage />} />
          <Route path="/success/:clientId" element={<DynamicExternalRedirect baseUrl={import.meta.env.VITE_CLIENT_PORTAL_URL} pathPrefix="/success" paramName="clientId" />} />
          <Route path="/schedule/:clientId" element={<SchedulePage />} />
          <Route path="/candidate-schedule/:mappingId" element={<CandidateSchedulePage />} />
          <Route path="/handover/:token" element={<HandoverPage />} />
          <Route path="/feedback/:token" element={<FeedbackPage />} />
          <Route path="/client-feedback/:token" element={<ClientFeedbackPage />} />
          <Route path="/candidate-feedback/:token" element={<CandidateFeedbackPage />} />
          <Route path="/rm/:rmId" element={<RMPage />} />
          <Route path="/agreement/:token" element={<DynamicExternalRedirect baseUrl={import.meta.env.VITE_CLIENT_PORTAL_URL} pathPrefix="/agreement" paramName="token" />} />
          <Route path="/client/:token" element={<ClientPortalPage />} />

          {/* Private pages — auth required */}
          <Route path="/*" element={
            <PrivateRoute>
              <AppLayout />
            </PrivateRoute>
          } />
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  )
}

function AppLayout() {
  return (
    <div className="flex h-screen overflow-hidden bg-gray-50">
      <Sidebar />
      <main className="flex-1 overflow-y-auto">
        <Routes>
          <Route path="/" element={<Navigate to="/clients" replace />} />
          <Route path="/clients" element={<ClientsPage />} />
          <Route path="/clients/:id" element={<ClientDetailPage />} />
          <Route path="/candidates" element={<CandidatesPage />} />
          <Route path="/admin/templates" element={<AdminTemplatesPage />} />
          <Route path="/admin/analytics" element={<AdminAnalyticsPage />} />
          <Route path="/admin/rms" element={<AdminRMsPage />} />
          <Route path="/admin/reachout-queue" element={<ReachoutQueuePage />} />
          <Route path="/admin/ai-voice-reachout" element={<AIVoiceReachoutPage />} />
          <Route path="/admin/funnel-stuck-reachout" element={<FunnelStuckReachoutPage />} />
        </Routes>
      </main>
    </div>
  )
}

function Sidebar() {
  const navigate = useNavigate()

  const { data: aiReachout } = useQuery({
    queryKey: ['ai-voice-reachout'],
    queryFn: getAIVoiceReachoutClients,
    staleTime: 60_000,
  })
  const aiReachoutCount = aiReachout?.count ?? 0

  const { data: funnelStuckReachout } = useQuery({
    queryKey: ['funnel-stuck-reachout'],
    queryFn: getFunnelStuckReachoutClients,
    staleTime: 60_000,
  })
  const funnelStuckCount = funnelStuckReachout?.count ?? 0

  const handleLogout = () => {
    localStorage.removeItem('ja_portal_auth')
    navigate('/login', { replace: true })
  }

  const navClass = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
      isActive
        ? 'bg-blue-50 text-blue-700'
        : 'text-gray-600 hover:bg-gray-100 hover:text-gray-900'
    }`

  return (
    <aside className="w-56 shrink-0 flex flex-col bg-white border-r border-gray-200 py-5 px-3">
      <div className="mb-8 px-2">
        <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest">Just Accountants</p>
      </div>

      <nav className="flex-1 space-y-1">
        <NavLink to="/clients" className={navClass}>
          <span>🏢</span> Clients
        </NavLink>
        <NavLink to="/candidates" className={navClass}>
          <span>👤</span> Candidates
        </NavLink>

        <div className="mt-4 px-2 pb-1">
          <p className="text-[10px] font-semibold text-gray-300 uppercase tracking-widest">Admin</p>
        </div>
        <NavLink to="/admin/templates" className={navClass}>
          <span>📋</span> Templates
        </NavLink>
        <NavLink to="/admin/analytics" className={navClass} end>
          <span>📊</span> Analytics
        </NavLink>
        <NavLink to="/admin/rms" className={navClass}>
          <span>👥</span> RMs
        </NavLink>
        <NavLink to="/admin/reachout-queue" className={navClass}>
          <span>📨</span> Reachout Queue
        </NavLink>
        <NavLink to="/admin/ai-voice-reachout" className={navClass}>
          <span>🤖</span> AI Voice Reachout
          {aiReachoutCount > 0 && (
            <span className="ml-auto text-xs bg-blue-100 text-blue-700 px-1.5 py-0.5 rounded-full">
              {aiReachoutCount}
            </span>
          )}
        </NavLink>
        <NavLink to="/admin/funnel-stuck-reachout" className={navClass}>
          <span>📞</span> Funnel Stuck Reachout
          {funnelStuckCount > 0 && (
            <span className="ml-auto text-xs bg-indigo-100 text-indigo-700 px-1.5 py-0.5 rounded-full">
              {funnelStuckCount}
            </span>
          )}
        </NavLink>
      </nav>

      <button
        onClick={handleLogout}
        className="mt-4 flex items-center gap-2 px-3 py-2 rounded-xl text-xs text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors"
      >
        <span>⎋</span> Logout
      </button>
    </aside>
  )
}
