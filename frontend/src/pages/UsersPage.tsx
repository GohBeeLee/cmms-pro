import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Users } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../lib/api'
import { EmptyState, Modal, PageHeader, Spinner, StatusBadge } from '../components/ui'

const EMPTY_FORM = {
  name: '',
  email: '',
  password: '',
  role: 'technician',
}

export default function UsersPage() {
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

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
      setShowModal(false)
      setForm(EMPTY_FORM)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  return (
    <div>
      <PageHeader
        title="Users"
        subtitle="Add technicians, managers, viewers, and admins"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> Add User
          </button>
        }
      />

      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : users.length === 0 ? <EmptyState message="No users found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['Name', 'Email', 'Role', 'Status'].map(h => <th key={h} className="table-header">{h}</th>)}
              </tr>
            </thead>
            <tbody>
              {users.map((user: any) => (
                <tr key={user.id} className="hover:bg-gray-50">
                  <td className="table-cell font-medium">{user.name}</td>
                  <td className="table-cell text-gray-500">{user.email}</td>
                  <td className="table-cell"><StatusBadge value={user.role} /></td>
                  <td className="table-cell"><StatusBadge value={user.is_active ? 'active' : 'inactive'} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {showModal && (
        <Modal title="Add User" onClose={() => setShowModal(false)}>
          <div className="space-y-3">
            <div>
              <label className="label">Name *</label>
              <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            </div>
            <div>
              <label className="label">Email *</label>
              <input type="email" className="input" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div>
              <label className="label">Password *</label>
              <input type="password" className="input" value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} />
            </div>
            <div>
              <label className="label">Role</label>
              <select className="input" value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
                {['technician', 'manager', 'viewer', 'admin'].map(role => (
                  <option key={role} value={role}>{role}</option>
                ))}
              </select>
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={() => createMutation.mutate(form)} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Saving...' : 'Save User'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
