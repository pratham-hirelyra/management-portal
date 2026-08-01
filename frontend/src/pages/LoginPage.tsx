import { useState } from 'react'
import { useNavigate } from 'react-router-dom'

const PASSWORD = import.meta.env.VITE_PORTAL_PASSWORD || 'hireLyra@2024'

export default function LoginPage() {
  const [pw, setPw] = useState('')
  const [error, setError] = useState(false)
  const navigate = useNavigate()

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    if (pw === PASSWORD) {
      localStorage.setItem('ja_portal_auth', '1')
      navigate('/clients', { replace: true })
    } else {
      setError(true)
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-lg p-8 w-full max-w-sm">
        <div className="text-center mb-8">
          <img
            src="https://storage.googleapis.com/hirelyra-website/images/unnamed%20(1).jpg"
            alt="Hire Lyra"
            className="w-14 h-14 rounded-xl mx-auto mb-3 object-cover"
          />
          <h1 className="text-lg font-bold text-gray-900">Management Portal</h1>
          <p className="text-xs text-gray-400 mt-0.5">Hire Lyra</p>
        </div>
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">Password</label>
            <input
              type="password"
              value={pw}
              onChange={e => { setPw(e.target.value); setError(false) }}
              className={`w-full rounded-lg border px-3 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400 ${
                error ? 'border-red-400' : 'border-gray-300'
              }`}
              placeholder="Enter portal password"
              autoFocus
            />
            {error && <p className="mt-1 text-xs text-red-500">Incorrect password.</p>}
          </div>
          <button
            type="submit"
            className="w-full py-2.5 rounded-lg bg-blue-600 text-white text-sm font-medium hover:bg-blue-700 transition-colors"
          >
            Sign In
          </button>
        </form>
      </div>
    </div>
  )
}
