import { useEffect, useRef, useState } from 'react'
import { webClient } from '@/api/client'
import {
  type CheeseCombo,
  type CheeseLeg,
  type CheesePayload,
  type CheeseRequest,
  skipReasonText,
} from '@/lib/cheeseRanking'
import { cn } from '@/lib/utils'

/** Wait this long after the last edge change before asking the backend again. */
const RANK_DEBOUNCE_MS = 1500

export type CheeseRankingState =
  | { phase: 'idle' }
  | { phase: 'loading'; previous: CheesePayload | null }
  | { phase: 'ready'; cheese: CheesePayload }
  | { phase: 'error'; message: string }

/** Ranks `request` on the backend, debounced; a later request supersedes an earlier in-flight one. */
export function useCheeseRanking(request: CheeseRequest | null): CheeseRankingState {
  const [state, setState] = useState<CheeseRankingState>({ phase: 'idle' })
  const lastReady = useRef<CheesePayload | null>(null)

  useEffect(() => {
    if (!request) {
      setState({ phase: 'idle' })
      return
    }
    let cancelled = false
    const handle = setTimeout(async () => {
      setState({ phase: 'loading', previous: lastReady.current })
      try {
        const res = await webClient.post<{
          status: string
          message?: string
          cheese?: CheesePayload
        }>('/api/trade-cheese', request, { validateStatus: () => true })
        if (cancelled) return
        if (res.status !== 200 || res.data.status !== 'success' || !res.data.cheese) {
          setState({
            phase: 'error',
            message: res.data?.message || `Ranking unavailable (HTTP ${res.status})`,
          })
          return
        }
        lastReady.current = res.data.cheese
        setState({ phase: 'ready', cheese: res.data.cheese })
      } catch (err) {
        if (!cancelled)
          setState({
            phase: 'error',
            message: err instanceof Error ? err.message : 'Ranking request failed',
          })
      }
    }, RANK_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(handle)
    }
  }, [request])

  return state
}

const fmtPct = (value: number | null) => (value === null ? 'n/a' : `${(value * 100).toFixed(0)}%`)

function PickRow({
  leg,
  side,
  onPick,
}: {
  leg: CheeseLeg
  side: 'BUY' | 'SELL'
  onPick: (strike: number, optionType: 'CE' | 'PE', side: 'BUY' | 'SELL') => void
}) {
  return (
    <button
      type="button"
      onClick={() => onPick(leg.strike, leg.option_type, side)}
      title={leg.rationale.join(' | ')}
      className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1 text-left text-xs hover:bg-muted/50"
    >
      <span className="font-medium">
        {side} {leg.strike} {leg.option_type}
      </span>
      <span className="text-muted-foreground">
        score {leg.deal_score.toFixed(2)} / P(ITM) {fmtPct(leg.p_itm)} / BE{' '}
        {leg.breakeven.toFixed(0)}
      </span>
    </button>
  )
}

function ComboCard({
  combo,
  onAdd,
  formatCurrency,
}: {
  combo: CheeseCombo
  onAdd: (combo: CheeseCombo) => void
  formatCurrency: (value: number) => string
}) {
  const m = combo.metrics
  const money = (v: number | null) => (v === null ? 'n/a' : formatCurrency(v))
  return (
    <div className="rounded-md border p-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">{combo.name.replace(/_/g, ' ')}</span>
        <button
          type="button"
          onClick={() => onAdd(combo)}
          className="rounded-md border px-2 py-0.5 hover:bg-muted/50"
        >
          Add combo
        </button>
      </div>
      <div className="mt-1 text-muted-foreground">{combo.rationale}</div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-muted-foreground">
        <span>PoP {fmtPct(m.pop)}</span>
        <span>Max profit {money(m.max_profit)}</span>
        <span>Max loss {money(m.max_loss)}</span>
        <span>Net {money(m.net_debit_credit)}</span>
      </div>
    </div>
  )
}

export interface CheesePicksProps {
  state: CheeseRankingState
  onPick: (strike: number, optionType: 'CE' | 'PE', side: 'BUY' | 'SELL') => void
  onAddCombo: (combo: CheeseCombo) => void
  formatCurrency: (value: number) => string
}

/** Top-5 BUY and SELL strikes (trend fit x smile-fit edge x P(ITM)) and auto-built combos. */
export function CheesePicks({ state, onPick, onAddCombo, formatCurrency }: CheesePicksProps) {
  if (state.phase === 'idle') return null
  const shown =
    state.phase === 'ready' ? state.cheese : state.phase === 'loading' ? state.previous : null

  return (
    <div className="border-b px-3 py-2" data-testid="cheese-picks">
      <div className="mb-1 flex items-center justify-between text-[10px] text-muted-foreground">
        <span>Top picks: edge x trend fit x P(ITM)</span>
        {state.phase === 'loading' && <span>Updating...</span>}
      </div>
      {state.phase === 'error' && (
        <div role="alert" className="text-xs text-destructive">
          {state.message}
        </div>
      )}
      {shown?.status === 'skipped' && (
        <div className="text-xs text-muted-foreground">{skipReasonText(shown.skip_reason)}</div>
      )}
      {shown?.status === 'ok' && (
        <div className="space-y-2">
          <div className="grid gap-2 md:grid-cols-2">
            {(['BUY', 'SELL'] as const).map((side) => {
              const legs = side === 'BUY' ? shown.buys : shown.sells
              return (
                <div key={side} className="rounded-md border p-1">
                  <div
                    className={cn(
                      'px-2 text-[10px] font-medium',
                      side === 'BUY' ? 'text-emerald-600' : 'text-red-500'
                    )}
                  >
                    {side === 'BUY' ? 'Cheap to buy' : 'Rich to sell'}
                  </div>
                  {legs.length === 0 ? (
                    <div className="px-2 py-1 text-xs text-muted-foreground">
                      Nothing fits the trend.
                    </div>
                  ) : (
                    legs.map((leg) => (
                      <PickRow
                        key={`${leg.strike}-${leg.option_type}`}
                        leg={leg}
                        side={side}
                        onPick={onPick}
                      />
                    ))
                  )}
                </div>
              )
            })}
          </div>
          {shown.combos.length > 0 && (
            <div className="grid gap-2 md:grid-cols-3">
              {shown.combos.map((combo) => (
                <ComboCard
                  key={combo.name}
                  combo={combo}
                  onAdd={onAddCombo}
                  formatCurrency={formatCurrency}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
