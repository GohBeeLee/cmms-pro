import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'

export const api = axios.create({ baseURL: API_URL })

// Inject JWT on every request
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('cmms_token')
  if (token) config.headers.Authorization = `Bearer ${token}`
  return config
})

// Redirect to login on 401
api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err.response?.status === 401) {
      localStorage.removeItem('cmms_token')
      window.location.href = '/login'
    }
    return Promise.reject(err)
  }
)

export default api