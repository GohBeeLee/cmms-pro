import { useQuery } from '@tanstack/react-query'
import {
  Wrench, ClipboardList, AlertTriangle,
  CheckCircle, Calendar, Package, Users, Activity
} from 'lucide-react'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, PieChart, Pie, Cell, Legend
} from 'recharts'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { KpiCard, Spinner, PageHeader } from '../components/ui'

const COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#ef4444', '#8b5cf6']

export default function DashboardPage() {
  // Connect to all rooms for live updates
  useWebSocket('assets')
  useWebSocket('work_orders')
  useWebSocket('inventory')

  const { data: kpi, isLoading } = useQuery({
    queryKey: ['kpi'],
    queryFn: async () => (await api.get('/dashboard/kpi')).data,
    refetchInterval: 60_000,
  })

  const { data: workOrders } = useQuery({
    queryKey: ['work_orders'],
    queryFn: async () => (await api.get('/work-orders/?limit=200')).data,
  })

  if (isLoading) return <Spinner />

  // Build chart data from work orders
  const statusCounts: Record<string, number> = {}
  const priorityCounts: Record<string, number> = {}
  ;(workOrders ?? []).forEach((wo: any) => {
    statusCounts[wo.status] = (statusCounts[wo.status] ?? 0) + 1
    priorityCounts[wo.priority] = (priorityCounts[wo.priority] ?? 0) + 1
  })

  const statusData = Object.entries(statusCounts).map(([name, value]) => ({ name, value }))
  const priorityData = Object.entries(priorityCounts).map(([name, value]) => ({ name, value }))

  return (
    <div>
      <PageHeader
        title="Dashboard"
        subtitle="Live system overview — updates in real time"
        action={
          <div className="flex items-center gap-2 text-xs text-green-600 bg-green-50 px-3 py-1.5 rounded-full border border-green-200">
            <Activity size={12} />
            Live sync active
          </div>
        }
      />

      {/* KPI Grid */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
        <KpiCard
          label="Total Assets"
          value={kpi?.total_assets ?? 0}
          icon={<Wrench size={22} className="text-blue-600" />}
          color="bg-blue-50"
        />
        <KpiCard
          label="Under Maintenance"
          value={kpi?.assets_under_maintenance ?? 0}
          icon={<AlertTriangle size={22} className="text-yellow-600" />}
          color="bg-yellow-50"
          alert={(kpi?.assets_under_maintenance ?? 0) > 0}
        />
        <KpiCard
          label="Open Work Orders"
          value={kpi?.open_work_orders ?? 0}
          icon={<ClipboardList size={22} className="text-indigo-600" />}
          color="bg-indigo-50"
        />
        <KpiCard
          label="Overdue WOs"
          value={kpi?.overdue_work_orders ?? 0}
          icon={<AlertTriangle size={22} className="text-red-600" />}
          color="bg-red-50"
          alert={(kpi?.overdue_work_orders ?? 0) > 0}
        />
        <KpiCard
          label="Completed Today"
          value={kpi?.work_orders_completed_today ?? 0}
          icon={<CheckCircle size={22} className="text-green-600" />}
          color="bg-green-50"
        />
        <KpiCard
          label="PM Due (7 days)"
          value={kpi?.pm_schedules_due_soon ?? 0}
          icon={<Calendar size={22} className="text-purple-600" />}
          color="bg-purple-50"
          alert={(kpi?.pm_schedules_due_soon ?? 0) > 0}
        />
        <KpiCard
          label="Low Stock Parts"
          value={kpi?.low_stock_parts ?? 0}
          icon={<Package size={22} className="text-orange-600" />}
          color="bg-orange-50"
          alert={(kpi?.low_stock_parts ?? 0) > 0}
        />
        <KpiCard
          label="Active Users"
          value={kpi?.total_technicians ?? 0}
          icon={<Users size={22} className="text-teal-600" />}
          color="bg-teal-50"
        />
      </div>

      {/* Charts */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="card">
          <h3 className="font-semibold text-gray-700 mb-4">Work Orders by Status</h3>
          <ResponsiveContainer width="100%" height={220}>
            <PieChart>
              <Pie data={statusData} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label>
                {statusData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Pie>
              <Tooltip />
              <Legend />
            </PieChart>
          </ResponsiveContainer>
        </div>

        <div className="card">
          <h3 className="font-semibold text-gray-700 mb-4">Work Orders by Priority</h3>
          <ResponsiveContainer width="100%" height={220}>
            <BarChart data={priorityData}>
              <XAxis dataKey="name" tick={{ fontSize: 12 }} />
              <YAxis allowDecimals={false} tick={{ fontSize: 12 }} />
              <Tooltip />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {priorityData.map((_, i) => (
                  <Cell key={i} fill={COLORS[i % COLORS.length]} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      </div>
    </div>
  )
}