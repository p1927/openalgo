import { useEffect, useState } from 'react'
import { API_BASE_URL } from '@/api/client'

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
  reason?: string
  replay_date?: string | null
  clock?: SimulatorClock
  hf_replay?: boolean
  week_mode?: boolean
  week_dates?: string[]
  data_watermark?: string | null
  active_expiry?: string | null
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
  // Set while the last poll failed, so a caller shows the status as unknown
  // instead of the last good value (a stale LIVE/REPLAY label is a wrong one).
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!enabled) return

    let cancelled = false

    const fetchStatus = async () => {
      try {
        const response = await fetch(`${API_BASE_URL}/sandbox/api/simulator/status`, { credentials: 'include' })
        if (!response.ok) throw new Error(`simulator status HTTP ${response.status}`)
        const data = await response.json()
        if (cancelled) return
        if (data.status !== 'success') throw new Error(data.message || 'simulator status not ok')
        setAvailable(true)
        setError(null)
        setStatus(data.simulator as SimulatorStatus)
      } catch (err) {
        if (cancelled) return
        setError(err instanceof Error ? err.message : 'simulator status unavailable')
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

  return { status, setStatus, available, error, clock: status?.clock ?? null }
}

/** `clock.sim_now` (ISO, IST) as epoch seconds, for feeding TradingTerminal's
 * setTimeSource() so replayed charts don't stamp candles with wall-clock
 * time. Null when the simulator isn't reporting a clock. */
export function simNowEpochSec(clock: SimulatorClock | null): number | null {
  if (!clock?.sim_now) return null
  const ms = Date.parse(clock.sim_now)
  return Number.isFinite(ms) ? Math.floor(ms / 1000) : null
}
