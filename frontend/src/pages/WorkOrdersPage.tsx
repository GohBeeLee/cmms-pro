import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Activity, BarChart3, Plus } from 'lucide-react'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { StatusBadge, Modal, Spinner, EmptyState, PageHeader, KpiCard } from '../components/ui'

const TYPES = ['corrective', 'preventive', 'inspection', 'emergency']
const PRIORITIES = ['low', 'medium', 'high', 'critical']
const STATUSES = ['open', 'in_progress', 'on_hold', 'completed', 'cancelled']

const EMPTY_FORM = {
  asset_id: '',
  type: 'corrective',
  priority: 'medium',
  title: '',
  description: '',
  due_date: '',
  estimated_hours: '',
  affected_downtime: true,
}

export default function WorkOrdersPage() {
  useWebSocket('work_orders')

  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [statusFilter, setStatusFilter] = useState('')
  const [analysisFilters, setAnalysisFilters] = useState({
    date_from: '',
    date_to: '',
    location: '',
    asset_id: '',
  })
  const qc = useQueryClient()

  const { data: workOrders = [], isLoading } = useQuery({
    queryKey: ['work_orders', statusFilter],
    queryFn: async () => {
      const params = statusFilter ? `?status=${statusFilter}` : ''
      return (await api.get(`/work-orders/${params}`)).data
    },
  })

  const { data: assets = [] } = useQuery({
    queryKey: ['assets'],
    queryFn: async () => (await api.get('/assets/')).data,
  })

  const { data: analysisLocations = [] } = useQuery({
    queryKey: ['analysis_locations'],
    queryFn: async () => (await api.get('/analysis/locations')).data,
  })

  const { data: analysis, isLoading: analysisLoading } = useQuery({
    queryKey: ['analysis', analysisFilters],
    queryFn: async () => {
      const params = new URLSearchParams()
      Object.entries(analysisFilters).forEach(([key, value]) => {
        if (value) params.set(key, value)
      })
      const qs = params.toString()
      return (await api.get(`/analysis/work-orders${qs ? `?${qs}` : ''}`)).data
    },
    refetchInterval: 30_000,
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post('/work-orders/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work_orders'] })
      qc.invalidateQueries({ queryKey: ['analysis'] })
      toast.success('Work order created')
      setShowModal(false)
      setForm(EMPTY_FORM)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch(`/work-orders/${id}`, { status }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work_orders'] })
      qc.invalidateQueries({ queryKey: ['analysis'] })
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const handleSubmit = () => {
    const payload: any = { ...form }
    if (payload.due_date) payload.due_date = new Date(payload.due_date).toISOString()
    if (payload.estimated_hours) payload.estimated_hours = parseFloat(payload.estimated_hours)
    createMutation.mutate(payload)
  }

  const updateAnalysisFilter = (key: keyof typeof analysisFilters, value: string) => {
    setAnalysisFilters((current) => ({ ...current, [key]: value }))
  }

  return (
    <div>
      <PageHeader
        title="Work Orders"
        subtitle="Track, manage, and analyse maintenance downtime"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> New Work Order
          </button>
        }
      />

      <div className="flex gap-2 mb-4 flex-wrap">
        {['', ...STATUSES].map((s) => (
          <button
            key={s}
            onClick={() => setStatusFilter(s)}
            className={`px-3 py-1 rounded-full text-xs font-medium border transition-colors ${
              statusFilter === s
                ? 'bg-blue-600 text-white border-blue-600'
                : 'bg-white text-gray-600 border-gray-200 hover:border-blue-400'
            }`}
          >
            {s === '' ? 'All' : s.replace(/_/g, ' ')}
          </button>
        ))}
      </div>

      <div className="mb-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h2 className="text-lg font-semibold text-gray-900 flex items-center gap-2">
              <BarChart3 size={18} className="text-blue-600" />
              Downtime Analysis
            </h2>
            <p className="text-xs text-gray-500 mt-1">Affected and non-affected downtime from work order history</p>
          </div>
          <div className="flex items-center gap-2 text-xs text-green-600 bg-green-50 px-3 py-1.5 rounded-full border border-green-200">
            <Activity size={12} />
            Live
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-4 gap-3">
          <input
            type="date"
            className="input"
            value={analysisFilters.date_from}
            onChange={(e) => updateAnalysisFilter('date_from', e.target.value)}
          />
          <input
            type="date"
            className="input"
            value={analysisFilters.date_to}
            onChange={(e) => updateAnalysisFilter('date_to', e.target.value)}
          />
          <select
            className="input"
            value={analysisFilters.location}
            onChange={(e) => updateAnalysisFilter('location', e.target.value)}
          >
            <option value="">All locations</option>
            {analysisLocations.map((item: any) => (
              <option key={item.location} value={item.location}>{item.location}</option>
            ))}
          </select>
          <select
            className="input"
            value={analysisFilters.asset_id}
            onChange={(e) => updateAnalysisFilter('asset_id', e.target.value)}
          >
            <option value="">All machines</option>
            {assets.map((a: any) => <option key={a.id} value={a.id}>{a.name} ({a.asset_code})</option>)}
          </select>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-4 gap-4">
          <KpiCard
            label="Affected Hrs"
            value={analysis?.summary?.affected_downtime_hrs ?? 0}
            icon={<Activity size={22} className="text-red-600" />}
            color="bg-red-50"
            alert={(analysis?.summary?.affected_downtime_hrs ?? 0) > 0}
          />
          <KpiCard
            label="Non-Affected Hrs"
            value={analysis?.summary?.non_affected_downtime_hrs ?? 0}
            icon={<Activity size={22} className="text-emerald-600" />}
            color="bg-emerald-50"
          />
          <KpiCard
            label="Total Cases"
            value={analysis?.summary?.total_cases ?? 0}
            icon={<BarChart3 size={22} className="text-blue-600" />}
            color="bg-blue-50"
          />
          <KpiCard
            label="Avg Downtime"
            value={`${analysis?.summary?.avg_downtime_hrs ?? 0}h`}
            icon={<Activity size={22} className="text-indigo-600" />}
            color="bg-indigo-50"
          />
        </div>

        <div className="card">
          <h3 className="font-semibold text-gray-700 mb-4">Downtime by Request Date</h3>
          {analysisLoading ? <Spinner /> : (
            <ResponsiveContainer width="100%" height={260}>
              <BarChart data={analysis?.downtime_graph ?? []}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} />
                <XAxis dataKey="date" tick={{ fontSize: 12 }} />
                <YAxis tick={{ fontSize: 12 }} label={{ value: 'Hours', angle: -90, position: 'insideLeft', fontSize: 12 }} />
                <Tooltip formatter={(value: number) => [`${value}h`, '']} />
                <Legend />
                <Bar dataKey="affected_downtime" name="Affected" stackId="downtime" fill="#ef4444" radius={[4, 4, 0, 0]} />
                <Bar dataKey="non_affected_downtime" name="Non-affected" stackId="downtime" fill="#10b981" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          )}
        </div>
      </div>

      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : workOrders.length === 0 ? <EmptyState message="No work orders found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['WO #', 'Title', 'Asset', 'Type', 'Downtime', 'Priority', 'Status', 'Due Date', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {workOrders.map((wo: any) => (
                <tr key={wo.id} className="hover:bg-gray-50">
                  <td className="table-cell font-mono text-xs text-blue-700">{wo.wo_number}</td>
                  <td className="table-cell font-medium max-w-xs truncate">{wo.title}</td>
                  <td className="table-cell text-gray-500 text-xs">{wo.asset?.name ?? '-'}</td>
                  <td className="table-cell"><StatusBadge value={wo.type} /></td>
                  <td className="table-cell">
                    <StatusBadge
                      value={wo.affected_downtime ? 'affected' : 'non_affected'}
                      label={wo.affected_downtime ? 'Affected' : 'Non-affected'}
                    />
                  </td>
                  <td className="table-cell"><StatusBadge value={wo.priority} /></td>
                  <td className="table-cell"><StatusBadge value={wo.status} /></td>
                  <td className="table-cell text-xs text-gray-500">
                    {wo.due_date ? format(new Date(wo.due_date), 'dd MMM yyyy') : '-'}
                  </td>
                  <td className="table-cell">
                    <select
                      className="text-xs border border-gray-200 rounded px-2 py-1 bg-white"
                      value={wo.status}
                      onChange={(e) => updateStatus.mutate({ id: wo.id, status: e.target.value })}
                    >
                      {STATUSES.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
                    </select>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <Modal title="New Work Order" onClose={() => setShowModal(false)}>
          <div className="space-y-3">
            <div>
              <label className="label">Asset *</label>
              <select className="input" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
                <option value="">Select asset...</option>
                {assets.map((a: any) => <option key={a.id} value={a.id}>{a.name} ({a.asset_code})</option>)}
              </select>
            </div>
            <div>
              <label className="label">Title *</label>
              <input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Type</label>
                <select className="input" value={form.type} onChange={(e) => setForm({ ...form, type: e.target.value })}>
                  {TYPES.map(t => <option key={t} value={t}>{t}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Priority</label>
                <select className="input" value={form.priority} onChange={(e) => setForm({ ...form, priority: e.target.value })}>
                  {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
            </div>
            <div>
              <label className="label">Description</label>
              <textarea className="input" rows={3} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div>
              <label className="label">Downtime Type</label>
              <select
                className="input"
                value={form.affected_downtime ? 'affected' : 'non_affected'}
                onChange={(e) => setForm({ ...form, affected_downtime: e.target.value === 'affected' })}
              >
                <option value="affected">Affected downtime</option>
                <option value="non_affected">Non-affected downtime</option>
              </select>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Due Date</label>
                <input type="datetime-local" className="input" value={form.due_date} onChange={(e) => setForm({ ...form, due_date: e.target.value })} />
              </div>
              <div>
                <label className="label">Est. Hours</label>
                <input type="number" className="input" value={form.estimated_hours} onChange={(e) => setForm({ ...form, estimated_hours: e.target.value })} />
              </div>
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={handleSubmit} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating...' : 'Create'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
