import React from 'react'

// ── Status badge colours ───────────────────────────────────────────────────

const STATUS_COLORS: Record<string, string> = {
  // Asset
  operational:       'bg-green-100 text-green-800',
  under_maintenance: 'bg-yellow-100 text-yellow-800',
  out_of_service:    'bg-red-100 text-red-800',
  decommissioned:    'bg-gray-100 text-gray-600',
  // Work order
  open:              'bg-blue-100 text-blue-800',
  in_progress:       'bg-indigo-100 text-indigo-800',
  on_hold:           'bg-orange-100 text-orange-800',
  completed:         'bg-green-100 text-green-800',
  cancelled:         'bg-gray-100 text-gray-600',
  // Priority
  low:               'bg-gray-100 text-gray-700',
  medium:            'bg-yellow-100 text-yellow-800',
  high:              'bg-orange-100 text-orange-800',
  critical:          'bg-red-100 text-red-800',
  // Task
  pending:           'bg-gray-100 text-gray-700',
  accepted:          'bg-blue-100 text-blue-800',
}

interface BadgeProps {
  value: string
  label?: string
}

export function StatusBadge({ value, label }: BadgeProps) {
  const color = STATUS_COLORS[value] ?? 'bg-gray-100 text-gray-700'
  const text = label ?? value.replace(/_/g, ' ')
  return (
    <span className={`badge ${color}`}>
      {text.charAt(0).toUpperCase() + text.slice(1)}
    </span>
  )
}

// ── Modal wrapper ──────────────────────────────────────────────────────────

interface ModalProps {
  title: string
  onClose: () => void
  children: React.ReactNode
}

export function Modal({ title, onClose, children }: ModalProps) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="bg-white rounded-xl shadow-xl w-full max-w-lg mx-4 max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100">
          <h2 className="text-lg font-semibold text-gray-900">{title}</h2>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
        </div>
        <div className="px-6 py-4">{children}</div>
      </div>
    </div>
  )
}

// ── Spinner ────────────────────────────────────────────────────────────────

export function Spinner() {
  return (
    <div className="flex items-center justify-center py-12">
      <div className="w-8 h-8 border-4 border-blue-500 border-t-transparent rounded-full animate-spin" />
    </div>
  )
}

// ── Empty state ────────────────────────────────────────────────────────────

export function EmptyState({ message }: { message: string }) {
  return (
    <div className="text-center py-12 text-gray-400">
      <p className="text-lg">{message}</p>
    </div>
  )
}

// ── Page header ────────────────────────────────────────────────────────────

interface PageHeaderProps {
  title: string
  subtitle?: string
  action?: React.ReactNode
}

export function PageHeader({ title, subtitle, action }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between mb-6">
      <div>
        <h1 className="text-2xl font-bold text-gray-900">{title}</h1>
        {subtitle && <p className="text-sm text-gray-500 mt-1">{subtitle}</p>}
      </div>
      {action && <div>{action}</div>}
    </div>
  )
}

// ── KPI card ───────────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string
  value: number | string
  icon: React.ReactNode
  color: string
  alert?: boolean
}

export function KpiCard({ label, value, icon, color, alert }: KpiCardProps) {
  return (
    <div className={`card flex items-center gap-4 ${alert ? 'border-red-200 bg-red-50' : ''}`}>
      <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${color}`}>
        {icon}
      </div>
      <div>
        <p className="text-2xl font-bold text-gray-900">{value}</p>
        <p className="text-sm text-gray-500">{label}</p>
      </div>
    </div>
  )
}