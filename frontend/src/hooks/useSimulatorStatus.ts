import { useEffect, useState } from 'react'

export interface SimulatorClock {
  replay_date?: string
  sim_now?: string
  speed?: number
  stepped?: boolean
  session_open?: boolean
  loop?: boolean
}

export interface SimulatorStatus {
  mode?: string
  clock?: SimulatorClock
  hf_replay?: boolean
}

interface UseSimulatorStatusOptions {
  /** Poll interval in ms (default 1000). Set 0 to fetch once only. */
  pollMs?: number
  enabled?: boolean
}

export function useSimulatorStatus(options: UseSimulatorStatusOptions = {}) {
  const { pollMs = 1000, enabled = true } = options
  const [status, setStatus] = useState<SimulatorStatus | null>(null)
  const [available, setAvailable] = useState(false)

  useEffect(() => {
    if (!enabled) return

    let cancelled = false

    const fetchStatus = async () => {
      try {
        const response = await fetch('/sandbox/api/simulator/status', { credentials: 'include' })
        if (!response.ok) return
        const data = await response.json()
        if (cancelled || data.status !== 'success') return
        setAvailable(true)
        setStatus(data.simulator as SimulatorStatus)
      } catch {
        // Simulator API optional when Trade stack path unavailable
      }
    }

    fetchStatus()
    if (pollMs <= 0) return () => { cancelled = true }

    const id = setInterval(fetchStatus, pollMs)
    return () => {
      cancelled = true
      clearInterval(id)
    }
  }, [enabled, pollMs])

  return { status, available, clock: status?.clock ?? null }
}
