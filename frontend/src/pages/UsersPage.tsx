import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Edit2, Plus, Trash2, UserCheck, UserX } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../lib/api'
import { EmptyState, Modal, PageHeader, Spinner, StatusBadge } from '../components/ui'
import { useWebSocket } from '../hooks/useWebSocket'

const ROLES = ['technician', 'manager', 'production', 'viewer', 'admin']
const EMPTY_FORM = {
  name: '',
  email: '',
  password: '',
  role: 'technician',
}

export default function UsersPage() {
  const [showModal, setShowModal] = useState(false)
  const [editingUser, setEditingUser] = useState<any | null>(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  useWebSocket('users')

  const { data: users = [], isLoading } = useQuery({
    queryKey: ['users'],
    queryFn: async () => (await api.get('/users/')).data,
  })

  const createMutation = useMutation({
    mutationFn: (data: typeof EMPTY_FORM) => api.post('/users/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['kpi'] })
      toast.success('User added')
      closeModal()
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: { name: string; role: string; is_active?: boolean } }) =>
      api.patch(`/users/${id}`, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['assignable_users'] })
      toast.success('User updated')
      closeModal()
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  // Attendance toggle — marks is_present true/false
  const attendanceMutation = useMutation({
    mutationFn: ({ id, is_present }: { id: string; is_present: boolean }) =>
      api.patch(`/users/${id}`, { is_present }),
    onSuccess: (_, { is_present }) => {
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['assignable_users'] })
      toast.success(is_present ? '✅ Marked as present' : '❌ Marked as absent')
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/users/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['users'] })
      qc.invalidateQueries({ queryKey: ['kpi'] })
      toast.success('User deleted')
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const closeModal = () => {
    setShowModal(false)
    setEditingUser(null)
    setForm(EMPTY_FORM)
  }

  const openEdit = (user: any) => {
    setEditingUser(user)
    setForm({ name: user.name, email: user.email, password: '', role: user.role })
    setShowModal(true)
  }

  const submit = () => {
    if (editingUser) {
      updateMutation.mutate({ id: editingUser.id, data: { name: form.name, role: form.role } })
      return
    }
    createMutation.mutate(form)
  }

  const presentCount = users.filter((u: any) => u.is_present).length

  return (
    <div>
      <PageHeader
        title="Users"
        subtitle="Add technicians, managers, production, viewers, and admins"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> Add User
          </button>
        }
      />

      {/* Attendance summary banner */}
      <div className="mb-4 flex items-center gap-3 bg-green-50 border border-green-200 rounded-lg px-4 py-3">
        <UserCheck size={18} className="text-green-600" />
        <p className="text-sm text-green-800">
          <span className="font-semibold">{presentCount} of {users.length}</span> staff marked present today — only present staff can be assigned on the Maintenance Alert Board
        </p>
      </div>

      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : users.length === 0 ? <EmptyState message="No users found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['Name', 'Email', 'Role', 'Status', 'Present Today', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.map((user: any) => (
                <tr key={user.id} className="hover:bg-gray-50">
                  <td className="table-cell font-medium">{user.name}</td>
                  <td className="table-cell text-gray-500">{user.email}</td>
                  <td className="table-cell"><StatusBadge value={user.role} /></td>
                  <td className="table-cell"><StatusBadge value={user.is_active ? 'active' : 'inactive'} /></td>
                  <td className="table-cell">
                    <button
                      title={user.is_present ? 'Click to mark absent' : 'Click to mark present'}
                      onClick={() => attendanceMutation.mutate({ id: user.id, is_present: !user.is_present })}
                      disabled={attendanceMutation.isPending}
                      className={`inline-flex items-center gap-1.5 text-xs font-medium px-3 py-1 rounded-full border transition-colors ${
                        user.is_present
                          ? 'bg-green-100 text-green-700 border-green-300 hover:bg-green-200'
                          : 'bg-gray-100 text-gray-500 border-gray-300 hover:bg-gray-200'
                      }`}
                    >
                      {user.is_present ? <UserCheck size={12} /> : <UserX size={12} />}
                      {user.is_present ? 'Present' : 'Absent'}
                    </button>
                  </td>
                  <td className="table-cell">
                    <div className="flex gap-2">
                      <button className="text-xs text-blue-600 hover:underline inline-flex items-center gap-1" onClick={() => openEdit(user)}>
                        <Edit2 size={12} /> Edit
                      </button>
                      <button
                        className="text-xs text-red-500 hover:underline inline-flex items-center gap-1"
                        onClick={() => { if (confirm(`Delete ${user.name}?`)) deleteMutation.mutate(user.id) }}
                      >
                        <Trash2 size={12} /> Delete
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <Modal title={editingUser ? 'Edit User' : 'Add User'} onClose={closeModal}>
          <div className="space-y-3">
            <div>
              <label className="label">Name *</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="label">Email *</label>
              <input type="email" className="input" value={form.email} disabled={!!editingUser} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            {!editingUser && (
              <div>
                <label className="label">Password *</label>
                <input type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
              </div>
            )}
            <div>
              <label className="label">Role</label>
              <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {ROLES.map(role => <option key={role} value={role}>{role}</option>)}
              </select>
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={submit} disabled={createMutation.isPending || updateMutation.isPending}>
                {createMutation.isPending || updateMutation.isPending ? 'Saving...' : 'Save User'}
              </button>
              <button className="btn-secondary flex-1" onClick={closeModal}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}