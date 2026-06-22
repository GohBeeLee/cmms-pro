import { useEffect, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Activity, Bell, CheckCircle, Volume2, Users, X } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../lib/api'
import { EmptyState, Modal, PageHeader, Spinner, StatusBadge } from '../components/ui'
import { useWebSocket } from '../hooks/useWebSocket'

const PRIORITIES = ['low', 'medium', 'high', 'critical']

function downtime(createdAt: string) {
  const start = new Date(createdAt).getTime()
  const mins = Math.max(0, Math.floor((Date.now() - start) / 60000))
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? `${h}h ${m}m` : `${m}m`
}

export default function MaintenanceAlertPage() {
  const [selected, setSelected] = useState<any | null>(null)
  const [assignedIds, setAssignedIds] = useState<string[]>([])
  const [priority, setPriority] = useState('medium')
  const [dueDate, setDueDate] = useState('')
  const [notes, setNotes] = useState('')
  const [soundEnabled, setSoundEnabled] = useState(false)
  const [, setTick] = useState(0)
  const audioRef = useRef<AudioContext | null>(null)
  const knownOpenIds = useRef<Set<string>>(new Set())
  const initialized = useRef(false)
  const qc = useQueryClient()

  const { data: open = [], isLoading: loadingOpen } = useQuery({
    queryKey: ['alert_work_orders', 'open'],
    queryFn: async () => (await api.get('/work-orders/?status=open&limit=200')).data,
  })
  const { data: inProgress = [], isLoading: loadingProgress } = useQuery({
    queryKey: ['alert_work_orders', 'in_progress'],
    queryFn: async () => (await api.get('/work-orders/?status=in_progress&limit=200')).data,
  })
  const { data: onHold = [], isLoading: loadingHold } = useQuery({
    queryKey: ['alert_work_orders', 'on_hold'],
    queryFn: async () => (await api.get('/work-orders/?status=on_hold&limit=200')).data,
  })

  // Completed today
  const { data: completedToday = [] } = useQuery({
    queryKey: ['alert_work_orders', 'completed_today'],
    queryFn: async () => {
      const today = new Date()
      today.setHours(0, 0, 0, 0)
      const res = await api.get('/work-orders/?status=completed&limit=500')
      return res.data.filter((wo: any) =>
        wo.completed_at && new Date(wo.completed_at) >= today
      )
    },
    refetchInterval: 60_000,
  })

  // Real-time assignable users — refetches whenever attendance (is_present) changes via WS
  const { data: users = [] } = useQuery({
    queryKey: ['assignable_users'],
    queryFn: async () => (await api.get('/users/technicians')).data,
    // Poll every 30s as a fallback
    refetchInterval: 30_000,
  })

  const activeOrders = [...open, ...inProgress, ...onHold].sort((a: any, b: any) => {
    const rank: any = { critical: 0, high: 1, medium: 2, low: 3 }
    return (rank[a.priority] ?? 2) - (rank[b.priority] ?? 2)
  })

  async function beep(force = false) {
    if (!force && !soundEnabled) return
    const Ctx = window.AudioContext || (window as any).webkitAudioContext
    if (!Ctx) return
    const ctx = audioRef.current || new Ctx()
    audioRef.current = ctx
    if (ctx.state === 'suspended') await ctx.resume()
    for (let i = 0; i < 3; i += 1) {
      const osc = ctx.createOscillator()
      const gain = ctx.createGain()
      osc.frequency.value = 880
      gain.gain.value = 0.12
      osc.connect(gain)
      gain.connect(ctx.destination)
      const t = ctx.currentTime + i * 0.28
      osc.start(t)
      osc.stop(t + 0.16)
    }
  }

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ['alert_work_orders'] })
    qc.invalidateQueries({ queryKey: ['work_orders'] })
  }

  // Listen to work_orders room for WO events
  useWebSocket('work_orders', (event) => {
    refresh()
    if (event.type === 'work_order.created') beep()
  })

  // Listen to users room — when admin marks attendance, re-fetch assignable users
  useWebSocket('users', (event) => {
    if (event.type === 'user.updated' || event.type === 'user.created' || event.type === 'user.deleted') {
      qc.invalidateQueries({ queryKey: ['assignable_users'] })
    }
  })

  useWebSocket('requests', () => {
    refresh()
    beep()
  })

  useEffect(() => {
    const timer = setInterval(() => setTick((n) => n + 1), 30_000)
    return () => clearInterval(timer)
  }, [])

  useEffect(() => {
    const ids = new Set(open.map((wo: any) => wo.id))
    if (initialized.current && [...ids].some(id => !knownOpenIds.current.has(id))) beep()
    knownOpenIds.current = ids
    initialized.current = true
  }, [open])

  const assignMutation = useMutation({
    mutationFn: () => api.post(`/work-orders/${selected.id}/assign`, {
      user_ids: assignedIds,
      priority,
      due_date: dueDate ? new Date(dueDate).toISOString() : null,
      notes: notes || null,
    }),
    onSuccess: () => {
      toast.success('Work order assigned')
      setSelected(null)
      refresh()
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const openAssign = (wo: any) => {
    setSelected(wo)
    // Pre-populate with existing assigned users
    const existingIds = (wo.assigned_users || []).map((u: any) => u.id)
    setAssignedIds(existingIds)
    setPriority(wo.priority || 'medium')
    setDueDate('')
    setNotes('')
  }

  const toggleUser = (userId: string) => {
    setAssignedIds(prev =>
      prev.includes(userId) ? prev.filter(id => id !== userId) : [...prev, userId]
    )
  }

  const getAssignedNames = (wo: any) => {
    const assigned = wo.assigned_users || []
    if (assigned.length === 0) return ''
    if (assigned.length === 1) return assigned[0].name
    return `${assigned[0].name} +${assigned.length - 1} more`
  }

  const isLoading = loadingOpen || loadingProgress || loadingHold

  return (
    <div>
      <PageHeader
        title="Maintenance Alert Board"
        subtitle="Live incoming work orders, assignment, and running downtime"
        action={
          <button className={soundEnabled ? 'btn-primary flex items-center gap-2' : 'btn-secondary flex items-center gap-2'} onClick={() => { setSoundEnabled(true); beep(true) }}>
            <Volume2 size={16} /> {soundEnabled ? 'Sound On' : 'Enable Sound'}
          </button>
        }
      />

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-6">
        <div className="card bg-red-50"><p className="text-3xl font-bold text-red-700">{open.length}</p><p className="text-sm text-gray-500">Incoming</p></div>
        <div className="card bg-blue-50"><p className="text-3xl font-bold text-blue-700">{inProgress.length + onHold.length}</p><p className="text-sm text-gray-500">Active</p></div>
        <div className="card bg-red-900"><p className="text-3xl font-bold text-white">{activeOrders.filter((w: any) => w.priority === 'critical').length}</p><p className="text-sm text-red-200">Critical</p></div>
        <div className="card bg-emerald-50 border border-emerald-200">
          <div className="flex items-center gap-2 mb-1">
            <CheckCircle size={14} className="text-emerald-600" />
            <p className="text-xs text-emerald-600 font-medium">Today</p>
          </div>
          <p className="text-3xl font-bold text-emerald-700">{completedToday.length}</p>
          <p className="text-sm text-gray-500">Completed</p>
        </div>
        <div className="card bg-green-50">
          <div className="flex items-center gap-2">
            <Activity size={14} className="text-green-600" />
            <p className="text-xs text-green-600 font-medium">Live</p>
          </div>
          <p className="text-3xl font-bold text-green-700">{users.length}</p>
          <p className="text-sm text-gray-500">Present Today</p>
        </div>
      </div>

      {isLoading ? <Spinner /> : activeOrders.length === 0 ? <EmptyState message="No active work orders" /> : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {activeOrders.map((wo: any) => {
            const isIncoming = wo.status === 'open'
            const isCritical = wo.priority === 'critical'
            const assignedUsers = wo.assigned_users || []
            const assignedLabel = getAssignedNames(wo)
            return (
              <div key={wo.id} className={`card ${isCritical ? 'bg-red-950 text-white border-red-500' : isIncoming ? 'bg-orange-50 border-orange-200' : ''}`}>
                <div className="flex justify-between gap-4 mb-3">
                  <div>
                    <p className={`font-mono text-xs font-bold ${isCritical ? 'text-red-200' : 'text-blue-700'}`}>{wo.wo_number}</p>
                    <h2 className="text-xl font-bold">{wo.asset?.name ?? wo.title}</h2>
                    <p className={`text-xs ${isCritical ? 'text-red-200' : 'text-gray-500'}`}>{wo.asset?.location ?? 'No location'}</p>
                  </div>
                  <div className="flex gap-2 items-start"><StatusBadge value={wo.priority} /><StatusBadge value={wo.status} /></div>
                </div>
                <div className={`rounded-lg p-3 mb-3 ${isCritical ? 'bg-white/10' : 'bg-gray-50'}`}>
                  <p className={`text-xs font-bold ${isCritical ? 'text-red-200' : 'text-gray-500'}`}>RUNNING DOWNTIME</p>
                  <p className={`text-3xl font-bold ${isCritical ? 'text-white' : 'text-orange-700'}`}>{downtime(wo.created_at)}</p>
                </div>
                {/* Assigned technicians list */}
                {assignedUsers.length > 0 && (
                  <div className={`flex flex-wrap gap-1 mb-2`}>
                    {assignedUsers.map((u: any) => (
                      <span key={u.id} className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full font-medium ${isCritical ? 'bg-white/20 text-white' : 'bg-indigo-100 text-indigo-700'}`}>
                        <Users size={10} /> {u.name}
                      </span>
                    ))}
                  </div>
                )}
                <div className="flex justify-between items-center">
                  <p className={`text-sm font-semibold ${assignedLabel ? 'text-indigo-600' : 'text-red-600'} ${isCritical ? '!text-white' : ''}`}>
                    {assignedLabel ? `Assigned: ${assignedLabel}` : 'Unassigned'}
                  </p>
                  <button className="btn-primary flex items-center gap-2" onClick={() => openAssign(wo)}>
                    <Bell size={14} /> {assignedLabel ? 'Reassign' : 'Assign'}
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      )}

      {selected && (
        <Modal title={`Assign: ${selected.wo_number}`} onClose={() => setSelected(null)}>
          <div className="space-y-4">
            {/* Multi-select technicians */}
            <div>
              <label className="label flex items-center gap-1">
                <Users size={14} /> Assign Technicians
                <span className="text-xs text-gray-400 ml-1">(only present staff shown)</span>
              </label>
              {users.length === 0 ? (
                <div className="text-sm text-gray-400 italic py-2">No staff marked as present today</div>
              ) : (
                <div className="border border-gray-200 rounded-lg divide-y max-h-52 overflow-y-auto">
                  {users.map((u: any) => {
                    const isSelected = assignedIds.includes(u.id)
                    return (
                      <label
                        key={u.id}
                        className={`flex items-center gap-3 px-3 py-2 cursor-pointer transition-colors hover:bg-gray-50 ${isSelected ? 'bg-indigo-50' : ''}`}
                      >
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleUser(u.id)}
                          className="accent-indigo-600"
                        />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm font-medium text-gray-900">{u.name}</p>
                          <p className="text-xs text-gray-400">{u.role}</p>
                        </div>
                        {isSelected && (
                          <span className="text-xs bg-indigo-600 text-white px-2 py-0.5 rounded-full">Selected</span>
                        )}
                      </label>
                    )
                  })}
                </div>
              )}
              {assignedIds.length > 0 && (
                <div className="flex flex-wrap gap-1 mt-2">
                  {assignedIds.map(id => {
                    const u = users.find((u: any) => u.id === id)
                    return u ? (
                      <span key={id} className="inline-flex items-center gap-1 text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded-full">
                        {u.name}
                        <button onClick={() => toggleUser(id)} className="hover:text-red-600"><X size={10} /></button>
                      </span>
                    ) : null
                  })}
                </div>
              )}
            </div>

            <div><label className="label">Priority</label><select className="input" value={priority} onChange={(e) => setPriority(e.target.value)}>{PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}</select></div>
            <div><label className="label">Due Date</label><input type="datetime-local" className="input" value={dueDate} onChange={(e) => setDueDate(e.target.value)} /></div>
            <div><label className="label">Notes</label><textarea className="input" rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} /></div>
            <div className="flex gap-3 pt-2">
              <button
                className="btn-primary flex-1"
                disabled={assignedIds.length === 0 || assignMutation.isPending}
                onClick={() => assignMutation.mutate()}
              >
                {assignMutation.isPending ? 'Assigning...' : `Assign ${assignedIds.length > 0 ? `(${assignedIds.length})` : ''}`}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setSelected(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}