import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import api from '../lib/api'

interface User {
  id: string
  name: string
  email: string
  role: string
}

interface AuthState {
  token: string | null
  user: User | null
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  fetchMe: () => Promise<void>
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,

      login: async (email, password) => {
        const params = new URLSearchParams()
        params.append('username', email)
        params.append('password', password)
        const { data } = await api.post('/auth/login', params, {
          headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        })
        localStorage.setItem('cmms_token', data.access_token)
        set({ token: data.access_token })
      },

      logout: () => {
        localStorage.removeItem('cmms_token')
        set({ token: null, user: null })
      },

      fetchMe: async () => {
        const { data } = await api.get('/auth/me')
        set({ user: data })
      },
    }),
    { name: 'cmms-auth', partialize: (s) => ({ token: s.token, user: s.user }) }
  )
)