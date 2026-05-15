import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, ChevronDown } from 'lucide-react'
import toast from 'react-hot-toast'
import { format } from 'date-fns'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { StatusBadge, Modal, Spinner, EmptyState, PageHeader } from '../components/ui'

const TYPES = ['corrective', 'preventive', 'inspection', 'emergency']
const PRIORITIES = ['low', 'medium', 'high', 'critical']
const STATUSES = ['open', 'in_progress', 'on_hold', 'completed', 'cancelled']

const EMPTY_FORM = {
  asset_id: '', type: 'corrective', priority: 'medium',
  title: '', description: '', due_date: '', estimated_hours: '',
}

export default function WorkOrdersPage() {
  useWebSocket('work_orders')

  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const [statusFilter, setStatusFilter] = useState('')
  const qc = useQueryClient()

  const { data: workOrders = [], isLoading } = useQuery({
    queryKey: ['work_orders'],
    queryFn: async () => {
      const params = statusFilter ? `?status=${statusFilter}` : ''
      return (await api.get(`/work-orders/${params}`)).data
    },
  })

  const { data: assets = [] } = useQuery({
    queryKey: ['assets'],
    queryFn: async () => (await api.get('/assets/')).data,
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post('/work-orders/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['work_orders'] })
      toast.success('Work order created')
      setShowModal(false)
      setForm(EMPTY_FORM)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const updateStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) =>
      api.patch(`/work-orders/${id}`, { status }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['work_orders'] }),
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const handleSubmit = () => {
    const payload: any = { ...form }
    if (payload.due_date) payload.due_date = new Date(payload.due_date).toISOString()
    if (payload.estimated_hours) payload.estimated_hours = parseFloat(payload.estimated_hours)
    createMutation.mutate(payload)
  }

  return (
    <div>
      <PageHeader
        title="Work Orders"
        subtitle="Track and manage all maintenance tasks"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> New Work Order
          </button>
        }
      />

      {/* Filter */}
      <div className="flex gap-2 mb-4 flex-wrap">
        {['', ...STATUSES].map((s) => (
          <button
            key={s}
            onClick={() => { setStatusFilter(s); qc.invalidateQueries({ queryKey: ['work_orders'] }) }}
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

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : workOrders.length === 0 ? <EmptyState message="No work orders found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['WO #', 'Title', 'Asset', 'Type', 'Priority', 'Status', 'Due Date', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {workOrders.map((wo: any) => (
                <tr key={wo.id} className="hover:bg-gray-50">
                  <td className="table-cell font-mono text-xs text-blue-700">{wo.wo_number}</td>
                  <td className="table-cell font-medium max-w-xs truncate">{wo.title}</td>
                  <td className="table-cell text-gray-500 text-xs">{wo.asset?.name ?? '—'}</td>
                  <td className="table-cell"><StatusBadge value={wo.type} /></td>
                  <td className="table-cell"><StatusBadge value={wo.priority} /></td>
                  <td className="table-cell"><StatusBadge value={wo.status} /></td>
                  <td className="table-cell text-xs text-gray-500">
                    {wo.due_date ? format(new Date(wo.due_date), 'dd MMM yyyy') : '—'}
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

      {/* Create Modal */}
      {showModal && (
        <Modal title="New Work Order" onClose={() => setShowModal(false)}>
          <div className="space-y-3">
            <div>
              <label className="label">Asset *</label>
              <select className="input" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
                <option value="">Select asset…</option>
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
                {createMutation.isPending ? 'Creating…' : 'Create'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}