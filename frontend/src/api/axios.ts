import axios from 'axios'

declare global {
  interface Window {
    __RUNTIME_CONFIG__?: { API_BASE_URL?: string; GMAPS_KEY?: string }
  }
}

const baseURL =
  window.__RUNTIME_CONFIG__?.API_BASE_URL ||
  import.meta.env.VITE_API_BASE_URL ||
  'http://localhost:8000'

export const api = axios.create({ baseURL })

