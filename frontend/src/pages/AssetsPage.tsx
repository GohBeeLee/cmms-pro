import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, Search } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { StatusBadge, Modal, Spinner, EmptyState, PageHeader } from '../components/ui'

const CATEGORIES = ['Pump', 'Motor', 'Compressor', 'Conveyor', 'HVAC', 'Generator', 'Other']
const STATUSES = ['operational', 'under_maintenance', 'out_of_service', 'decommissioned']

const EMPTY_FORM = {
  asset_code: '', name: '', category: '', location: '',
  manufacturer: '', model: '', serial_number: '', status: 'operational', notes: '',
}

export default function AssetsPage() {
  useWebSocket('assets')

  const [search, setSearch] = useState('')
  const [showModal, setShowModal] = useState(false)
  const [editing, setEditing] = useState<any>(null)
  const [form, setForm] = useState(EMPTY_FORM)

  const qc = useQueryClient()

  const { data: assets = [], isLoading } = useQuery({
    queryKey: ['assets'],
    queryFn: async () => (await api.get('/assets/')).data,
  })

  const saveMutation = useMutation({
    mutationFn: (data: any) =>
      editing
        ? api.patch(`/assets/${editing.id}`, data)
        : api.post('/assets/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assets'] })
      toast.success(editing ? 'Asset updated' : 'Asset created')
      closeModal()
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed to save'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/assets/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['assets'] })
      toast.success('Asset deleted')
    },
  })

  const openCreate = () => { setEditing(null); setForm(EMPTY_FORM); setShowModal(true) }
  const openEdit = (a: any) => {
    setEditing(a)
    setForm({ ...EMPTY_FORM, ...a })
    setShowModal(true)
  }
  const closeModal = () => { setShowModal(false); setEditing(null) }

  const filtered = assets.filter((a: any) =>
    a.name.toLowerCase().includes(search.toLowerCase()) ||
    a.asset_code.toLowerCase().includes(search.toLowerCase())
  )

  return (
    <div>
      <PageHeader
        title="Assets"
        subtitle="Manage all machines and equipment"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={openCreate}>
            <Plus size={16} /> Add Asset
          </button>
        }
      />

      {/* Search */}
      <div className="relative mb-4 max-w-sm">
        <Search size={15} className="absolute left-3 top-2.5 text-gray-400" />
        <input
          className="input pl-9"
          placeholder="Search assets…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : filtered.length === 0 ? <EmptyState message="No assets found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['Code', 'Name', 'Category', 'Location', 'Status', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((a: any) => (
                <tr key={a.id} className="hover:bg-gray-50">
                  <td className="table-cell font-mono text-xs text-blue-700">{a.asset_code}</td>
                  <td className="table-cell font-medium">{a.name}</td>
                  <td className="table-cell text-gray-500">{a.category}</td>
                  <td className="table-cell text-gray-500">{a.location}</td>
                  <td className="table-cell"><StatusBadge value={a.status} /></td>
                  <td className="table-cell">
                    <div className="flex gap-2">
                      <button className="text-xs text-blue-600 hover:underline" onClick={() => openEdit(a)}>Edit</button>
                      <button
                        className="text-xs text-red-500 hover:underline"
                        onClick={() => { if (confirm('Delete this asset?')) deleteMutation.mutate(a.id) }}
                      >Delete</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Modal */}
      {showModal && (
        <Modal title={editing ? 'Edit Asset' : 'Add Asset'} onClose={closeModal}>
          <div className="space-y-3">
            {[
              { key: 'asset_code', label: 'Asset Code *', disabled: !!editing },
              { key: 'name', label: 'Name *' },
              { key: 'location', label: 'Location *' },
              { key: 'manufacturer', label: 'Manufacturer' },
              { key: 'model', label: 'Model' },
              { key: 'serial_number', label: 'Serial Number' },
            ].map(({ key, label, disabled }) => (
              <div key={key}>
                <label className="label">{label}</label>
                <input
                  className="input"
                  disabled={disabled}
                  value={(form as any)[key]}
                  onChange={(e) => setForm({ ...form, [key]: e.target.value })}
                />
              </div>
            ))}
            <div>
              <label className="label">Category *</label>
              <select className="input" value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value })}>
                <option value="">Select…</option>
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Status</label>
              <select className="input" value={form.status} onChange={(e) => setForm({ ...form, status: e.target.value })}>
                {STATUSES.map(s => <option key={s} value={s}>{s.replace(/_/g, ' ')}</option>)}
              </select>
            </div>
            <div>
              <label className="label">Notes</label>
              <textarea className="input" rows={3} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={() => saveMutation.mutate(form)} disabled={saveMutation.isPending}>
                {saveMutation.isPending ? 'Saving…' : 'Save'}
              </button>
              <button className="btn-secondary flex-1" onClick={closeModal}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}