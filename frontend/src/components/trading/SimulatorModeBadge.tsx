import { useSimulatorStatus } from '@/hooks/useSimulatorStatus'
import { useBrokerStore } from '@/stores/brokerStore'

/** LIVE / REPLAY badge for the stock_simulator broker, sharing the same
 * status hook OptionChain.tsx polls so the trade view never shows
 * unlabeled replay data as if it were live. */
export function SimulatorModeBadge() {
  const { capabilities } = useBrokerStore()
  const isSimulator = capabilities?.broker_name === 'stock_simulator'
  const { status, available } = useSimulatorStatus({ pollMs: 5000, enabled: isSimulator })

  if (!isSimulator || !available || !status) return null

  if (status.mode === 'live') {
    return (
      <span className="flex items-center gap-1.5 rounded border border-emerald-600/40 bg-emerald-600/10 px-2 py-0.5 text-xs font-medium text-emerald-500">
        <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" />
        LIVE
      </span>
    )
  }

  const title =
    status.reason === 'market_closed_fallback'
      ? 'Market is closed — showing the most recently recorded day'
      : 'Replay explicitly started from Sandbox'

  return (
    <span
      title={title}
      className="flex items-center gap-1.5 rounded border border-amber-600/40 bg-amber-600/10 px-2 py-0.5 text-xs font-medium text-amber-500"
    >
      <span className="h-1.5 w-1.5 rounded-full bg-amber-500" />
      REPLAY{status.replay_date ? ` — ${status.replay_date}` : ''}
    </span>
  )
}
