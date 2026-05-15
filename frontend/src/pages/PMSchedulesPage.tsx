import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Zap, Calendar } from 'lucide-react'
import toast from 'react-hot-toast'
import { format, isPast, isWithinInterval, addDays } from 'date-fns'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { Modal, Spinner, EmptyState, PageHeader } from '../components/ui'

const FREQUENCIES = ['daily', 'weekly', 'monthly', 'quarterly', 'biannual', 'annual', 'custom']

const EMPTY_FORM = {
  asset_id: '', title: '', description: '',
  frequency: 'monthly', interval_days: 30,
  estimated_hours: '', next_due: '',
}

export default function PMSchedulesPage() {
  useWebSocket('pm_schedules')

  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState<any>(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ['pm_schedules'],
    queryFn: async () => (await api.get('/pm-schedules/')).data,
  })

  const { data: assets = [] } = useQuery({
    queryKey: ['assets'],
    queryFn: async () => (await api.get('/assets/')).data,
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post('/pm-schedules/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pm_schedules'] })
      toast.success('PM Schedule created')
      setShowModal(false)
      setForm(EMPTY_FORM)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const triggerMutation = useMutation({
    mutationFn: (id: string) => api.post(`/pm-schedules/${id}/trigger`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['pm_schedules', 'work_orders', 'kpi'] })
      toast.success('Work order generated from PM schedule')
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch(`/pm-schedules/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['pm_schedules'] }),
  })

  const handleSubmit = () => {
    const payload: any = { ...form }
    if (payload.next_due) payload.next_due = new Date(payload.next_due).toISOString()
    if (payload.estimated_hours) payload.estimated_hours = parseFloat(payload.estimated_hours)
    payload.interval_days = parseInt(payload.interval_days)
    createMutation.mutate(payload)
  }

  const getRowColor = (nextDue: string) => {
    const due = new Date(nextDue)
    if (isPast(due)) return 'bg-red-50 border-l-4 border-l-red-400'
    if (isWithinInterval(due, { start: new Date(), end: addDays(new Date(), 7) }))
      return 'bg-yellow-50 border-l-4 border-l-yellow-400'
    return ''
  }

  return (
    <div>
      <PageHeader
        title="PM Schedules"
        subtitle="Preventive maintenance planning"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> New Schedule
          </button>
        }
      />

      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : schedules.length === 0 ? <EmptyState message="No PM schedules found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['Title', 'Asset', 'Frequency', 'Next Due', 'Est. Hours', 'Status', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {schedules.map((pm: any) => (
                <tr key={pm.id} className={`hover:bg-gray-50 ${getRowColor(pm.next_due)}`}>
                  <td className="table-cell font-medium">{pm.title}</td>
                  <td className="table-cell text-gray-500 text-xs">{pm.asset?.name ?? '—'}</td>
                  <td className="table-cell capitalize text-gray-600">{pm.frequency}</td>
                  <td className="table-cell text-sm">
                    <span className={isPast(new Date(pm.next_due)) ? 'text-red-600 font-semibold' : 'text-gray-700'}>
                      {format(new Date(pm.next_due), 'dd MMM yyyy')}
                    </span>
                  </td>
                  <td className="table-cell text-gray-500">{pm.estimated_hours ? `${pm.estimated_hours}h` : '—'}</td>
                  <td className="table-cell">
                    <span className={`badge ${pm.is_active ? 'bg-green-100 text-green-800' : 'bg-gray-100 text-gray-600'}`}>
                      {pm.is_active ? 'Active' : 'Inactive'}
                    </span>
                  </td>
                  <td className="table-cell">
                    <div className="flex gap-2">
                      <button
                        className="flex items-center gap-1 text-xs text-indigo-600 hover:underline"
                        onClick={() => triggerMutation.mutate(pm.id)}
                        disabled={triggerMutation.isPending}
                        title="Generate work order now"
                      >
                        <Zap size={12} /> Trigger
                      </button>
                      <button
                        className="text-xs text-gray-500 hover:underline"
                        onClick={() => toggleMutation.mutate({ id: pm.id, is_active: !pm.is_active })}
                      >
                        {pm.is_active ? 'Deactivate' : 'Activate'}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Create Modal */}
      {showModal && (
        <Modal title="New PM Schedule" onClose={() => setShowModal(false)}>
          <div className="space-y-3">
            <div>
              <label className="label">Asset *</label>
              <select className="input" value={form.asset_id} onChange={(e) => setForm({ ...form, asset_id: e.target.value })}>
                <option value="">Select asset…</option>
                {assets.map((a: any) => <option key={a.id} value={a.id}>{a.name} ({a.asset_code})</option>)}
              </select>
            </div>
            <div>
              <label className="label">Schedule Title *</label>
              <input className="input" value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            </div>
            <div>
              <label className="label">Description</label>
              <textarea className="input" rows={2} value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">Frequency</label>
                <select className="input" value={form.frequency} onChange={(e) => setForm({ ...form, frequency: e.target.value })}>
                  {FREQUENCIES.map(f => <option key={f} value={f}>{f}</option>)}
                </select>
              </div>
              <div>
                <label className="label">Interval (days)</label>
                <input type="number" className="input" value={form.interval_days} onChange={(e) => setForm({ ...form, interval_days: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className="label">First Due Date *</label>
                <input type="datetime-local" className="input" value={form.next_due} onChange={(e) => setForm({ ...form, next_due: e.target.value })} />
              </div>
              <div>
                <label className="label">Est. Hours</label>
                <input type="number" className="input" value={form.estimated_hours} onChange={(e) => setForm({ ...form, estimated_hours: e.target.value })} />
              </div>
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={handleSubmit} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Creating…' : 'Create Schedule'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}