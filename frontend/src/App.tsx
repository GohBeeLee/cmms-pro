import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { useEffect } from 'react'
import { useAuthStore } from './store/authStore'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import DashboardPage from './pages/DashboardPage'
import AssetsPage from './pages/AssetsPage'
import WorkOrdersPage from './pages/WorkOrdersPage'
import InventoryPage from './pages/InventoryPage'
import PMSchedulesPage from './pages/PMSchedulesPage'
import UsersPage from './pages/UsersPage'
import MaintenanceAlertPage from './pages/MaintenanceAlertPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { token } = useAuthStore()
  if (!token) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  const { token, fetchMe } = useAuthStore()

  useEffect(() => {
    if (token) fetchMe().catch(() => {})
  }, [token])

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <Layout>
                <Routes>
                  <Route path="/"            element={<DashboardPage />} />
                  <Route path="/alert"       element={<MaintenanceAlertPage />} />
                  <Route path="/assets"      element={<AssetsPage />} />
                  <Route path="/work-orders" element={<WorkOrdersPage />} />
                  <Route path="/inventory"   element={<InventoryPage />} />
                  <Route path="/pm"          element={<PMSchedulesPage />} />
                  <Route path="/users"       element={<UsersPage />} />
                  <Route path="*"            element={<Navigate to="/" replace />} />
                </Routes>
              </Layout>
            </RequireAuth>
          }
        />
      </Routes>
    </BrowserRouter>
  )
}
