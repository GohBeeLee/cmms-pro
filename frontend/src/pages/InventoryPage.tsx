import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, AlertTriangle, Search } from 'lucide-react'
import toast from 'react-hot-toast'
import api from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'
import { Modal, Spinner, EmptyState, PageHeader } from '../components/ui'

const EMPTY_FORM = {
  part_code: '', name: '', description: '', category: '',
  unit: 'pcs', quantity_on_hand: 0, reorder_level: 5,
  unit_cost: '', supplier: '', location: '', barcode: '',
}

export default function InventoryPage() {
  useWebSocket('inventory')

  const [search, setSearch] = useState('')
  const [lowStockOnly, setLowStockOnly] = useState(false)
  const [showModal, setShowModal] = useState(false)
  const [restockId, setRestockId] = useState<string | null>(null)
  const [restockQty, setRestockQty] = useState(0)
  const [form, setForm] = useState(EMPTY_FORM)
  const qc = useQueryClient()

  const { data: parts = [], isLoading } = useQuery({
    queryKey: ['inventory', lowStockOnly],
    queryFn: async () =>
      (await api.get(`/inventory/?low_stock=${lowStockOnly}`)).data,
  })

  const createMutation = useMutation({
    mutationFn: (data: any) => api.post('/inventory/', data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory'] })
      toast.success('Part added')
      setShowModal(false)
      setForm(EMPTY_FORM)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const restockMutation = useMutation({
    mutationFn: ({ id, qty }: { id: string; qty: number }) =>
      api.post(`/inventory/${id}/restock?quantity=${qty}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory'] })
      toast.success('Restocked successfully')
      setRestockId(null)
    },
    onError: (e: any) => toast.error(e.response?.data?.detail ?? 'Failed'),
  })

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.delete(`/inventory/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['inventory'] })
      toast.success('Part removed')
    },
  })

  const filtered = parts.filter((p: any) =>
    p.name.toLowerCase().includes(search.toLowerCase()) ||
    p.part_code.toLowerCase().includes(search.toLowerCase())
  )

  const handleSubmit = () => {
    const payload: any = { ...form }
    if (payload.unit_cost) payload.unit_cost = parseFloat(payload.unit_cost)
    payload.quantity_on_hand = parseInt(payload.quantity_on_hand)
    payload.reorder_level = parseInt(payload.reorder_level)
    createMutation.mutate(payload)
  }

  return (
    <div>
      <PageHeader
        title="Inventory"
        subtitle="Spare parts and stock management"
        action={
          <button className="btn-primary flex items-center gap-2" onClick={() => setShowModal(true)}>
            <Plus size={16} /> Add Part
          </button>
        }
      />

      {/* Toolbar */}
      <div className="flex items-center gap-3 mb-4">
        <div className="relative flex-1 max-w-sm">
          <Search size={15} className="absolute left-3 top-2.5 text-gray-400" />
          <input className="input pl-9" placeholder="Search parts…" value={search} onChange={(e) => setSearch(e.target.value)} />
        </div>
        <label className="flex items-center gap-2 text-sm text-gray-600 cursor-pointer">
          <input
            type="checkbox"
            checked={lowStockOnly}
            onChange={(e) => setLowStockOnly(e.target.checked)}
            className="rounded"
          />
          <AlertTriangle size={14} className="text-orange-500" />
          Low stock only
        </label>
      </div>

      {/* Table */}
      <div className="card p-0 overflow-hidden">
        {isLoading ? <Spinner /> : filtered.length === 0 ? <EmptyState message="No parts found" /> : (
          <table className="w-full">
            <thead>
              <tr>
                {['Part Code', 'Name', 'Category', 'Barcode', 'In Stock', 'Reorder Level', 'Unit Cost', 'Supplier', 'Actions'].map(h => (
                  <th key={h} className="table-header">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {filtered.map((p: any) => {
                const isLow = p.quantity_on_hand <= p.reorder_level
                return (
                  <tr key={p.id} className={`hover:bg-gray-50 ${isLow ? 'bg-orange-50' : ''}`}>
                    <td className="table-cell font-mono text-xs text-blue-700">{p.part_code}</td>
                    <td className="table-cell font-medium">
                      <span className="flex items-center gap-1">
                        {p.name}
                        {isLow && <AlertTriangle size={13} className="text-orange-500" />}
                      </span>
                    </td>
                    <td className="table-cell text-gray-500">{p.category ?? '—'}</td>
                    <td className="table-cell font-mono text-xs text-gray-500">{p.barcode ?? '—'}</td>
                    <td className={`table-cell font-semibold ${isLow ? 'text-orange-600' : 'text-gray-900'}`}>
                      {p.quantity_on_hand} {p.unit}
                    </td>
                    <td className="table-cell text-gray-500">{p.reorder_level}</td>
                    <td className="table-cell text-gray-500">{p.unit_cost ? `RM ${p.unit_cost.toFixed(2)}` : '—'}</td>
                    <td className="table-cell text-gray-500">{p.supplier ?? '—'}</td>
                    <td className="table-cell">
                      <div className="flex gap-2">
                        <button
                          className="text-xs text-green-600 hover:underline"
                          onClick={() => { setRestockId(p.id); setRestockQty(0) }}
                        >Restock</button>
                        <button
                          className="text-xs text-red-500 hover:underline"
                          onClick={() => { if (confirm('Delete this part?')) deleteMutation.mutate(p.id) }}
                        >Delete</button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      {/* Add Part Modal */}
      {showModal && (
        <Modal title="Add Spare Part" onClose={() => setShowModal(false)}>
          <div className="space-y-3">
            {[
              { key: 'part_code', label: 'Part Code *' },
              { key: 'name', label: 'Name *' },
              { key: 'category', label: 'Category' },
              { key: 'supplier', label: 'Supplier' },
              { key: 'location', label: 'Storage Location' },
              { key: 'barcode', label: 'Barcode' },
            ].map(({ key, label }) => (
              <div key={key}>
                <label className="label">{label}</label>
                <input className="input" value={(form as any)[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
              </div>
            ))}
            <div className="grid grid-cols-3 gap-3">
              <div>
                <label className="label">Qty on Hand</label>
                <input type="number" className="input" value={form.quantity_on_hand} onChange={(e) => setForm({ ...form, quantity_on_hand: +e.target.value })} />
              </div>
              <div>
                <label className="label">Reorder Level</label>
                <input type="number" className="input" value={form.reorder_level} onChange={(e) => setForm({ ...form, reorder_level: +e.target.value })} />
              </div>
              <div>
                <label className="label">Unit Cost (RM)</label>
                <input type="number" className="input" value={form.unit_cost} onChange={(e) => setForm({ ...form, unit_cost: e.target.value })} />
              </div>
            </div>
            <div className="flex gap-3 pt-2">
              <button className="btn-primary flex-1" onClick={handleSubmit} disabled={createMutation.isPending}>
                {createMutation.isPending ? 'Saving…' : 'Save'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setShowModal(false)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}

      {/* Restock Modal */}
      {restockId && (
        <Modal title="Restock Part" onClose={() => setRestockId(null)}>
          <div className="space-y-4">
            <div>
              <label className="label">Quantity to Add</label>
              <input type="number" className="input" min={1} value={restockQty} onChange={(e) => setRestockQty(+e.target.value)} />
            </div>
            <div className="flex gap-3">
              <button
                className="btn-primary flex-1"
                onClick={() => restockMutation.mutate({ id: restockId, qty: restockQty })}
                disabled={restockQty < 1 || restockMutation.isPending}
              >
                {restockMutation.isPending ? 'Restocking…' : 'Confirm Restock'}
              </button>
              <button className="btn-secondary flex-1" onClick={() => setRestockId(null)}>Cancel</button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  )
}
