import { Minus, Plus } from 'lucide-react'
import { useMemo, useState } from 'react'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { computeMispricing, type MispricingRow } from '@/lib/optionMispricing'
import { cn } from '@/lib/utils'
import type { OptionStrike } from '@/types/option-chain'
import { showToast } from '@/utils/toast'
import type { LegDraft, LegDraftType, ResolveLegContract } from './ManualLegBuilder'

export interface CheeseTabProps {
  chain: OptionStrike[] | null
  forwardPrice: number | null
  years: number
  atmStrike: number | null
  underlyingLtp: number | null
  underlyingPrevClose: number | null
  selectedExpiry: string
  underlyings: string[]
  selectedUnderlying: string
  onUnderlyingChange: (u: string) => void
  expiries: string[]
  onExpiryChange: (e: string) => void
  resolveContract: ResolveLegContract
  onAdd: (draft: LegDraft) => void
  formatCurrency: (value: number) => string
}

const DEFAULT_THRESHOLD_PCT = 8
/** Fixed row height in px — the spot line snaps to whichever row's top edge it's closest to. */
const ROW_HEIGHT = 40
const MAX_BAR_PX = 36
const MIN_BAR_PX = 3
/** Column proportions: a slim 12px outer spacer, two equal-width data cells, a fixed strike
 *  column — the data cells scale with the card's actual width (fr, not a fixed px table) so the
 *  chain fills its container edge-to-edge instead of sitting as a narrow fixed-width island. */
const GRID_COLS = '12px minmax(0,1fr) minmax(0,1fr) 180px minmax(0,1fr) minmax(0,1fr) 12px'

function fmtPct(value: number): string {
  const pct = value * 100
  return `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`
}

interface ValueSplit {
  intrinsic: number
  extrinsic: number
  extrinsicPct: number
}

/** Premium = intrinsic + extrinsic. Intrinsic is capped at the premium itself so a stale/crossed
 *  quote (premium below parity) can't produce a negative extrinsic sliver. */
function splitValue(premium: number, strike: number, spot: number, type: 'CE' | 'PE'): ValueSplit {
  const rawIntrinsic = type === 'CE' ? Math.max(spot - strike, 0) : Math.max(strike - spot, 0)
  const intrinsic = Math.min(rawIntrinsic, premium)
  const extrinsic = Math.max(premium - intrinsic, 0)
  const extrinsicPct = premium > 0 ? (extrinsic / premium) * 100 : 0
  return { intrinsic, extrinsic, extrinsicPct }
}

/** Light background tint so a cell reads as buy/sell without competing with the strike's bar. */
function cellBackground(score: number | undefined): string | undefined {
  if (!score) return undefined
  const alpha = Math.min(Math.abs(score), 1) * 0.22
  return score < 0 ? `rgba(16, 185, 129, ${alpha})` : `rgba(239, 68, 68, ${alpha})`
}

/**
 * Row index whose top edge the spot-price line sits at, offset 10px upward — the same
 * snap-to-nearest-strike placement as the reference chain, rather than continuous interpolation.
 */
function spotLineRowIndex(rows: MispricingRow[], spot: number | null): number | null {
  if (!spot || rows.length === 0) return null
  const aboveIndex = rows.findIndex((r) => r.strike > spot)
  if (aboveIndex === -1) return rows.length
  return aboveIndex
}

export function CheeseTab({
  chain,
  forwardPrice,
  years,
  atmStrike,
  underlyingLtp,
  underlyingPrevClose,
  selectedExpiry,
  underlyings,
  selectedUnderlying,
  onUnderlyingChange,
  expiries,
  onExpiryChange,
  resolveContract,
  onAdd,
  formatCurrency,
}: CheeseTabProps) {
  const [thresholdPct, setThresholdPct] = useState(DEFAULT_THRESHOLD_PCT)
  const [addingKey, setAddingKey] = useState<string | null>(null)

  const rows = useMemo<MispricingRow[] | null>(() => {
    if (!chain) return null
    const scored = computeMispricing(chain, forwardPrice, years, 0, thresholdPct / 100)
    return scored ? [...scored].sort((a, b) => a.strike - b.strike) : null
  }, [chain, forwardPrice, years, thresholdPct])

  const spotRowIndex = useMemo(
    () => (rows ? spotLineRowIndex(rows, underlyingLtp) : null),
    [rows, underlyingLtp]
  )

  /** Chain-wide ceiling for the gutter heat bars, so a single rich strike doesn't wash out the
   *  rest of the strip — every row's bar height is relative to the richest extrinsic value seen. */
  const maxExtrinsic = useMemo(() => {
    if (!rows || underlyingLtp === null) return 0
    let max = 0
    for (const row of rows) {
      if (row.ce)
        max = Math.max(max, splitValue(row.ce.price, row.strike, underlyingLtp, 'CE').extrinsic)
      if (row.pe)
        max = Math.max(max, splitValue(row.pe.price, row.strike, underlyingLtp, 'PE').extrinsic)
    }
    return max
  }, [rows, underlyingLtp])

  const spotChange =
    underlyingLtp !== null && underlyingPrevClose ? underlyingLtp - underlyingPrevClose : null
  const spotChangePct =
    spotChange !== null && underlyingPrevClose ? (spotChange / underlyingPrevClose) * 100 : null

  const handleAddLeg = async (strike: number, optionType: LegDraftType, side: 'BUY' | 'SELL') => {
    const key = `${strike}-${optionType}-${side}`
    setAddingKey(key)
    try {
      const resolved = await resolveContract(selectedExpiry, 'OPTION', strike, optionType)
      if (!resolved) {
        showToast.warning(`Could not resolve ${strike} ${optionType}`)
        return
      }
      onAdd({
        segment: 'OPTION',
        side,
        expiry: resolved.expiry,
        strike,
        optionType,
        lots: 1,
        price: resolved.marketPrice,
        iv: resolved.iv,
        symbol: resolved.symbol,
        exchange: resolved.exchange,
        expiryTs: resolved.expiryTs,
        lotSize: resolved.lotSize,
        tickSize: resolved.tickSize,
        contractValid: true,
        marketPrice: resolved.marketPrice,
        referenceUnderlying: resolved.referenceUnderlying,
        forwardPrice: resolved.forwardPrice,
        greeks: resolved.greeks,
      })
      showToast.success(`Added ${side} ${strike}${optionType}`)
    } finally {
      setAddingKey(null)
    }
  }

  const handleCellClick = (strike: number, optionType: LegDraftType, side: 'BUY' | 'SELL') => {
    void handleAddLeg(strike, optionType, side)
  }

  return (
    <div className="overflow-hidden rounded-xl border bg-card">
      {/* Header row — column labels either side, symbol/expiry pickers in the center. */}
      <div
        className="grid items-center border-b px-3 py-1.5 text-[10px] text-muted-foreground"
        style={{ gridTemplateColumns: GRID_COLS }}
      >
        <div />
        <div className="text-left">Call Edge</div>
        <div className="text-left">Call LTP</div>
        <div className="flex items-center justify-center gap-1">
          <Select value={selectedUnderlying} onValueChange={onUnderlyingChange}>
            <SelectTrigger
              aria-label="Underlying"
              className="h-6 min-w-0 gap-1 rounded-md border-0 bg-transparent px-1 text-[11px] font-normal text-foreground shadow-none focus:ring-0 focus:ring-offset-0 [&>svg]:h-3 [&>svg]:w-3"
            >
              <SelectValue placeholder="Symbol" />
            </SelectTrigger>
            <SelectContent>
              {underlyings.map((u) => (
                <SelectItem key={u} value={u}>
                  {u}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <span className="text-muted-foreground">·</span>
          <Select value={selectedExpiry} onValueChange={onExpiryChange}>
            <SelectTrigger
              aria-label="Expiry"
              className="h-6 min-w-0 gap-1 rounded-md border-0 bg-transparent px-1 text-[11px] font-normal text-foreground shadow-none focus:ring-0 focus:ring-offset-0 [&>svg]:h-3 [&>svg]:w-3"
            >
              <SelectValue placeholder="Expiry" />
            </SelectTrigger>
            <SelectContent>
              {expiries.map((ex) => (
                <SelectItem key={ex} value={ex}>
                  {ex}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="text-right">Put LTP</div>
        <div className="text-right">Put Edge</div>
        <div />
      </div>

      {!chain || !rows ? (
        <div className="flex h-[300px] items-center justify-center text-sm text-muted-foreground">
          Load an option chain to scan for mispriced strikes.
        </div>
      ) : (
        <div className="relative overflow-x-auto">
          {rows.map((row) => {
            const buyPressure = Math.max(
              0,
              row.ce && row.ce.score < 0 ? -row.ce.score : 0,
              row.pe && row.pe.score < 0 ? -row.pe.score : 0
            )
            const sellPressure = Math.max(
              0,
              row.ce && row.ce.score > 0 ? row.ce.score : 0,
              row.pe && row.pe.score > 0 ? row.pe.score : 0
            )
            const barWidth = (pressure: number) =>
              pressure > 0 ? MIN_BAR_PX + pressure * (MAX_BAR_PX - MIN_BAR_PX) : MIN_BAR_PX

            const ceSplit =
              row.ce && underlyingLtp !== null
                ? splitValue(row.ce.price, row.strike, underlyingLtp, 'CE')
                : null
            const peSplit =
              row.pe && underlyingLtp !== null
                ? splitValue(row.pe.price, row.strike, underlyingLtp, 'PE')
                : null
            /** Gutter heat bar: height scales with this strike's extrinsic value relative to the
             *  richest strike in the chain — a glance down the column shows where extrinsic value
             *  is concentrated (typically near ATM, thinning out toward the wings). */
            const heatHeightPct = (extrinsic: number | undefined) =>
              maxExtrinsic > 0 && extrinsic ? Math.max(6, (extrinsic / maxExtrinsic) * 100) : 0

            return (
              <div
                key={row.strike}
                style={{ height: ROW_HEIGHT, gridTemplateColumns: GRID_COLS }}
                className={cn(
                  'group grid items-center border-b px-3 last:border-b-0',
                  row.strike === atmStrike && 'bg-muted/20'
                )}
              >
                <div
                  className="flex h-full items-end justify-center"
                  title={
                    ceSplit ? `Call extrinsic: ${formatCurrency(ceSplit.extrinsic)}` : undefined
                  }
                >
                  <span
                    className="w-1.5 rounded-full bg-amber-500/70"
                    style={{ height: `${heatHeightPct(ceSplit?.extrinsic)}%` }}
                  />
                </div>
                <div
                  className="flex h-full cursor-pointer flex-col items-start justify-center transition-opacity hover:opacity-70"
                  style={{ backgroundColor: cellBackground(row.ce?.score) }}
                  onClick={() =>
                    row.ce &&
                    row.ce.classification !== 'neutral' &&
                    handleCellClick(
                      row.strike,
                      'CE',
                      row.ce.classification === 'cheap' ? 'BUY' : 'SELL'
                    )
                  }
                >
                  <div
                    className={cn(
                      'text-[11px] font-normal tabular-nums',
                      row.ce?.classification === 'cheap' &&
                        'text-emerald-700 dark:text-emerald-400',
                      row.ce?.classification === 'rich' && 'text-rose-700 dark:text-rose-400'
                    )}
                  >
                    {row.ce ? fmtPct(row.ce.edgePct) : '—'}
                  </div>
                  <div className="text-[9px] text-muted-foreground">
                    {row.ce?.fittedIv ? `${row.ce.fittedIv.toFixed(1)} IV` : '—'}
                  </div>
                </div>
                <div
                  className="flex h-full cursor-pointer flex-col items-start justify-center transition-opacity hover:opacity-70"
                  style={{ backgroundColor: cellBackground(row.ce?.score) }}
                  onClick={() =>
                    row.ce &&
                    row.ce.classification !== 'neutral' &&
                    handleCellClick(
                      row.strike,
                      'CE',
                      row.ce.classification === 'cheap' ? 'BUY' : 'SELL'
                    )
                  }
                  title={
                    ceSplit
                      ? `Intrinsic ${formatCurrency(ceSplit.intrinsic)} · Extrinsic ${formatCurrency(ceSplit.extrinsic)}`
                      : undefined
                  }
                >
                  <div className="text-[11px] font-normal tabular-nums">
                    {row.ce ? formatCurrency(row.ce.price) : '—'}
                    {addingKey?.startsWith(`${row.strike}-CE-`) && '…'}
                  </div>
                  {ceSplit && row.ce ? (
                    <div className="flex w-full max-w-[72px] flex-col gap-0.5">
                      <span className="flex h-[3px] w-full overflow-hidden rounded-full bg-muted">
                        <span
                          className="h-full bg-slate-400 dark:bg-slate-500"
                          style={{ width: `${100 - ceSplit.extrinsicPct}%` }}
                        />
                        <span
                          className="h-full bg-amber-500"
                          style={{ width: `${ceSplit.extrinsicPct}%` }}
                        />
                      </span>
                      <span className="text-[9px] text-muted-foreground">
                        Extr {formatCurrency(ceSplit.extrinsic)}
                      </span>
                    </div>
                  ) : (
                    <div className="text-[9px] text-muted-foreground">—</div>
                  )}
                </div>

                <div className="relative flex items-center justify-center px-2">
                  {/* CE side B/S buttons — appear on row hover, sit to the LEFT of strike */}
                  <div
                    className={cn(
                      'absolute left-1 top-1/2 z-20 flex -translate-y-1/2 gap-0.5',
                      'opacity-0 transition-opacity group-hover:opacity-100'
                    )}
                  >
                    <button
                      type="button"
                      aria-label={`Buy ${row.strike} CE`}
                      data-testid={`cheese-buy-ce-${row.strike}`}
                      disabled={!row.ce || addingKey === `${row.strike}-CE-BUY`}
                      onClick={(e) => {
                        e.stopPropagation()
                        row.ce && handleCellClick(row.strike, 'CE', 'BUY')
                      }}
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold leading-none',
                        'bg-emerald-600 text-white shadow-sm transition-colors hover:bg-emerald-700',
                        'disabled:cursor-not-allowed disabled:opacity-50'
                      )}
                    >
                      B
                    </button>
                    <button
                      type="button"
                      aria-label={`Sell ${row.strike} CE`}
                      data-testid={`cheese-sell-ce-${row.strike}`}
                      disabled={!row.ce || addingKey === `${row.strike}-CE-SELL`}
                      onClick={(e) => {
                        e.stopPropagation()
                        row.ce && handleCellClick(row.strike, 'CE', 'SELL')
                      }}
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold leading-none',
                        'bg-rose-600 text-white shadow-sm transition-colors hover:bg-rose-700',
                        'disabled:cursor-not-allowed disabled:opacity-50'
                      )}
                    >
                      S
                    </button>
                  </div>

                  <div className="flex flex-col items-center gap-1">
                    <span className="text-[12px] font-semibold tabular-nums">{row.strike}</span>
                    <span className="flex h-1 w-[90px] items-center justify-center gap-0.5">
                      <span
                        className="h-1 rounded-full bg-rose-500"
                        style={{ width: barWidth(sellPressure) }}
                      />
                      <span
                        className="h-1 rounded-full bg-emerald-500"
                        style={{ width: barWidth(buyPressure) }}
                      />
                    </span>
                  </div>

                  {/* PE side B/S buttons — appear on row hover, sit to the RIGHT of strike */}
                  <div
                    className={cn(
                      'absolute right-1 top-1/2 z-20 flex -translate-y-1/2 gap-0.5',
                      'opacity-0 transition-opacity group-hover:opacity-100'
                    )}
                  >
                    <button
                      type="button"
                      aria-label={`Buy ${row.strike} PE`}
                      data-testid={`cheese-buy-pe-${row.strike}`}
                      disabled={!row.pe || addingKey === `${row.strike}-PE-BUY`}
                      onClick={(e) => {
                        e.stopPropagation()
                        row.pe && handleCellClick(row.strike, 'PE', 'BUY')
                      }}
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold leading-none',
                        'bg-emerald-600 text-white shadow-sm transition-colors hover:bg-emerald-700',
                        'disabled:cursor-not-allowed disabled:opacity-50'
                      )}
                    >
                      B
                    </button>
                    <button
                      type="button"
                      aria-label={`Sell ${row.strike} PE`}
                      data-testid={`cheese-sell-pe-${row.strike}`}
                      disabled={!row.pe || addingKey === `${row.strike}-PE-SELL`}
                      onClick={(e) => {
                        e.stopPropagation()
                        row.pe && handleCellClick(row.strike, 'PE', 'SELL')
                      }}
                      className={cn(
                        'flex h-5 w-5 items-center justify-center rounded text-[10px] font-bold leading-none',
                        'bg-rose-600 text-white shadow-sm transition-colors hover:bg-rose-700',
                        'disabled:cursor-not-allowed disabled:opacity-50'
                      )}
                    >
                      S
                    </button>
                  </div>
                </div>

                <div
                  className="flex h-full cursor-pointer flex-col items-end justify-center transition-opacity hover:opacity-70"
                  style={{ backgroundColor: cellBackground(row.pe?.score) }}
                  onClick={() =>
                    row.pe &&
                    row.pe.classification !== 'neutral' &&
                    handleCellClick(
                      row.strike,
                      'PE',
                      row.pe.classification === 'cheap' ? 'BUY' : 'SELL'
                    )
                  }
                  title={
                    peSplit
                      ? `Intrinsic ${formatCurrency(peSplit.intrinsic)} · Extrinsic ${formatCurrency(peSplit.extrinsic)}`
                      : undefined
                  }
                >
                  <div className="text-[11px] font-normal tabular-nums">
                    {row.pe ? formatCurrency(row.pe.price) : '—'}
                    {addingKey?.startsWith(`${row.strike}-PE-`) && '…'}
                  </div>
                  {peSplit && row.pe ? (
                    <div className="flex w-full max-w-[72px] flex-col items-end gap-0.5">
                      <span className="flex h-[3px] w-full overflow-hidden rounded-full bg-muted">
                        <span
                          className="h-full bg-amber-500"
                          style={{ width: `${peSplit.extrinsicPct}%` }}
                        />
                        <span
                          className="h-full bg-slate-400 dark:bg-slate-500"
                          style={{ width: `${100 - peSplit.extrinsicPct}%` }}
                        />
                      </span>
                      <span className="text-[9px] text-muted-foreground">
                        Extr {formatCurrency(peSplit.extrinsic)}
                      </span>
                    </div>
                  ) : (
                    <div className="text-[9px] text-muted-foreground">—</div>
                  )}
                </div>
                <div
                  className="flex h-full cursor-pointer flex-col items-end justify-center transition-opacity hover:opacity-70"
                  style={{ backgroundColor: cellBackground(row.pe?.score) }}
                  onClick={() =>
                    row.pe &&
                    row.pe.classification !== 'neutral' &&
                    handleCellClick(
                      row.strike,
                      'PE',
                      row.pe.classification === 'cheap' ? 'BUY' : 'SELL'
                    )
                  }
                >
                  <div
                    className={cn(
                      'text-[11px] font-normal tabular-nums',
                      row.pe?.classification === 'cheap' &&
                        'text-emerald-700 dark:text-emerald-400',
                      row.pe?.classification === 'rich' && 'text-rose-700 dark:text-rose-400'
                    )}
                  >
                    {row.pe ? fmtPct(row.pe.edgePct) : '—'}
                  </div>
                  <div className="text-[9px] text-muted-foreground">
                    {row.pe?.fittedIv ? `${row.pe.fittedIv.toFixed(1)} IV` : '—'}
                  </div>
                </div>
                <div
                  className="flex h-full items-end justify-center"
                  title={
                    peSplit ? `Put extrinsic: ${formatCurrency(peSplit.extrinsic)}` : undefined
                  }
                >
                  <span
                    className="w-1.5 rounded-full bg-amber-500/70"
                    style={{ height: `${heatHeightPct(peSplit?.extrinsic)}%` }}
                  />
                </div>
              </div>
            )
          })}

          {/* Live spot-price line — snapped to the nearest strike row's top edge, offset up 8px,
              matching the reference chain's own placement (not a continuous interpolation). */}
          {spotRowIndex !== null && underlyingLtp !== null && (
            <div
              className="pointer-events-none absolute inset-x-0 z-10 flex flex-col items-center justify-center"
              style={{ top: spotRowIndex * ROW_HEIGHT, transform: 'translateY(-8px)' }}
            >
              <div
                className="absolute inset-x-0 h-px"
                style={{
                  background:
                    'linear-gradient(90deg, transparent 0%, hsl(var(--muted-foreground)) 40%, hsl(var(--muted-foreground)) 60%, transparent 100%)',
                }}
              />
              <div className="relative z-10 flex min-w-[150px] items-center justify-center whitespace-nowrap rounded-full bg-gray-500 px-3 py-0.5 text-white shadow dark:bg-gray-600">
                <div className="flex items-center gap-1.5">
                  <span className="text-[10px] font-normal">{formatCurrency(underlyingLtp)}</span>
                  {spotChange !== null && spotChangePct !== null && (
                    <>
                      <span className="inline-block h-2.5 w-px rounded-full bg-white/40" />
                      <span
                        className={cn(
                          'text-[10px] font-normal',
                          spotChange >= 0 ? 'text-emerald-200' : 'text-rose-200'
                        )}
                      >
                        {spotChange >= 0 ? '+' : ''}
                        {spotChange.toFixed(2)} ({spotChangePct >= 0 ? '+' : ''}
                        {spotChangePct.toFixed(2)}%)
                      </span>
                    </>
                  )}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Bottom toolbar — matches the pill-toggle footer of the reference chain view. */}
      <div className="flex items-center justify-between gap-3 border-t px-3 py-1.5">
        <div className="flex items-center gap-3 text-[9px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-3 rounded-full bg-emerald-500" />
            Cheap
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-3 rounded-full bg-rose-500" />
            Rich
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-3 rounded-full bg-slate-400 dark:bg-slate-500" />
            Intrinsic
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block h-1.5 w-3 rounded-full bg-amber-500" />
            Extrinsic
          </span>
        </div>
        <div className="flex items-center gap-1.5 rounded-full border bg-background px-1.5 py-0.5">
          <span className="pl-1 text-[9px] text-muted-foreground">Edge threshold</span>
          <button
            type="button"
            aria-label="Lower threshold"
            onClick={() => setThresholdPct((p) => Math.max(2, p - 1))}
            className="flex h-4 w-4 items-center justify-center rounded-full hover:bg-muted"
          >
            <Minus className="h-2.5 w-2.5" />
          </button>
          <span className="w-6 text-center text-[9px] font-normal tabular-nums">
            {thresholdPct}%
          </span>
          <button
            type="button"
            aria-label="Raise threshold"
            onClick={() => setThresholdPct((p) => Math.min(25, p + 1))}
            className="flex h-4 w-4 items-center justify-center rounded-full hover:bg-muted"
          >
            <Plus className="h-2.5 w-2.5" />
          </button>
        </div>
      </div>
    </div>
  )
}
