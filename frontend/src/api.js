// Production builds call the Render backend directly: long-running analysis
// requests would otherwise hit Vercel's proxy timeout.
const DEFAULT_API_URL = import.meta.env.PROD ? 'https://uidt-api.onrender.com' : ''
const API_BASE_URL = (import.meta.env.VITE_API_URL || DEFAULT_API_URL).replace(/\/$/, '')

export function apiUrl(path) {
  if (/^https?:\/\//i.test(path)) return path
  return `${API_BASE_URL}${path}`
}
