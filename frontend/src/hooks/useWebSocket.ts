import { useEffect, useRef, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import toast from 'react-hot-toast'

const WS_URL = import.meta.env.VITE_WS_URL || 'ws://localhost:8000'

// Map WS room → React Query cache keys to invalidate
const ROOM_QUERY_MAP: Record<string, string[][]> = {
  assets:      [['assets'], ['kpi']],
  work_orders: [['work_orders'], ['kpi']],
  inventory:   [['inventory'], ['kpi']],
  tasks:       [['work_orders'], ['tasks']],
  pm_schedules:[['pm_schedules'], ['kpi']],
}

// Human-readable toast messages per event type
const EVENT_MESSAGES: Record<string, string> = {
  'asset.created':        '🏭 New asset added',
  'asset.updated':        '✏️ Asset updated',
  'asset.deleted':        '🗑️ Asset removed',
  'work_order.created':   '📋 New work order created',
  'work_order.updated':   '🔄 Work order updated',
  'work_order.deleted':   '🗑️ Work order removed',
  'inventory.created':    '📦 New part added',
  'inventory.updated':    '📦 Inventory updated',
  'inventory.restocked':  '✅ Part restocked',
  'task.assigned':        '👷 Task assigned',
  'task.updated':         '✏️ Task status updated',
  'pm.created':           '📅 PM schedule created',
  'connection.established': null as any, // suppress
}

interface WSEvent {
  room: string
  type: string
  payload: Record<string, unknown>
}

export function useWebSocket(room: string, onEvent?: (event: WSEvent) => void) {
  const queryClient = useQueryClient()
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>()

  const connect = useCallback(() => {
    const token = localStorage.getItem('cmms_token')
    if (!token) return

    const url = `${WS_URL}/ws/${room}?token=${token}`
    const ws = new WebSocket(url)
    wsRef.current = ws

    ws.onopen = () => {
      console.log(`[WS] Connected to room: ${room}`)
    }

    ws.onmessage = (e) => {
      try {
        const event: WSEvent = JSON.parse(e.data)

        // Invalidate relevant query caches
        const keys = ROOM_QUERY_MAP[event.room] || []
        keys.forEach((key) => queryClient.invalidateQueries({ queryKey: key }))

        // Show toast (unless suppressed)
        const msg = EVENT_MESSAGES[event.type]
        if (msg) toast(msg, { duration: 2500 })

        // Call optional custom handler
        onEvent?.(event)
      } catch (err) {
        console.warn('[WS] Failed to parse message', err)
      }
    }

    ws.onclose = (e) => {
      console.log(`[WS] Disconnected from room: ${room} (code: ${e.code})`)
      if (e.code !== 4001) {
        // Reconnect after 3s unless auth failure
        reconnectTimer.current = setTimeout(connect, 3000)
      }
    }

    ws.onerror = (err) => {
      console.warn(`[WS] Error in room ${room}:`, err)
    }
  }, [room, queryClient, onEvent])

  useEffect(() => {
    connect()
    return () => {
      clearTimeout(reconnectTimer.current)
      wsRef.current?.close()
    }
  }, [connect])

  // Manual ping to keep connection alive
  useEffect(() => {
    const interval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping')
      }
    }, 30_000)
    return () => clearInterval(interval)
  }, [])

  return wsRef
}