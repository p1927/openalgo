import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'

export type PlanCharges = {
  per_leg?: Array<{
    symbol?: string
    side?: string
    brokerage?: number
    stt?: number
    exchange?: number
    gst?: number
    stamp?: number
    sebi?: number
    total_charges?: number
    source_note?: string
  }>
  total?: {
    brokerage?: number
    stt?: number
    exchange?: number
    gst?: number
    stamp?: number
    sebi?: number
    total_charges?: number
  }
  exit?: { per_leg?: unknown[]; total?: Record<string, number> }
  exit_charges?: number
  round_trip_charges?: number
  net_debit_credit?: number
  broker_preset?: string
  broker_display?: string
  charge_source?: string
  /** Present for US fee schedule only. */
  estimate_range?: {
    total_charges_low?: number
    total_charges_high?: number
    note?: string
  }
} | null

export interface ChargesBreakdownProps {
  planCharges: PlanCharges
  isChargesLoading?: boolean
  compact?: boolean
  className?: string
}

function brokerLabel(planCharges: PlanCharges): string {
  if (planCharges?.broker_display) return planCharges.broker_display
  if (planCharges?.broker_preset) return planCharges.broker_preset
  return ''
}

export function ChargesBreakdown({
  planCharges,
  isChargesLoading = false,
  compact = false,
  className,
}: ChargesBreakdownProps) {
  const [summaryOpen, setSummaryOpen] = useState(false)
  const [perLegOpen, setPerLegOpen] = useState(false)

  const hasData =
    planCharges?.total?.total_charges !== undefined ||
    (planCharges?.per_leg && planCharges.per_leg.length > 0)

  const entryTotal = planCharges?.total?.total_charges
  const legCount = planCharges?.per_leg?.length ?? 0
  const label = brokerLabel(planCharges)

  return (
    <div
      className={cn(
        'border-t bg-muted/10 text-[11px]',
        compact ? 'px-3 py-2' : 'px-3.5 py-2.5',
        className
      )}
    >
      <Collapsible open={summaryOpen} onOpenChange={setSummaryOpen}>
        <CollapsibleTrigger
          className="flex w-full items-center gap-2 rounded-md py-0.5 text-left transition-colors hover:bg-muted/30"
          disabled={!hasData && !isChargesLoading}
        >
          <ChevronDown
            className={cn(
              'h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform',
              summaryOpen && 'rotate-180'
            )}
          />
          <span className="min-w-0 flex-1 font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Transaction charges
            {label ? ` (${label})` : ''}
            {isChargesLoading ? ' · refreshing…' : ''}
          </span>
          {entryTotal !== undefined && (
            <span className="shrink-0 font-semibold tabular-nums text-foreground">
              ₹{entryTotal.toLocaleString('en-IN')}
            </span>
          )}
        </CollapsibleTrigger>

        <CollapsibleContent className="space-y-2 pt-2">
          {planCharges?.estimate_range &&
            planCharges.estimate_range.total_charges_low !== undefined &&
            planCharges.estimate_range.total_charges_high !== undefined && (
              <div className="rounded-md border border-amber-500/30 bg-amber-500/5 px-2.5 py-2 text-[11px] text-amber-900 dark:text-amber-200">
                <span className="font-semibold tabular-nums">
                  Range:{' '}
                  {planCharges.charge_source === 'estimate_us'
                    ? `$${planCharges.estimate_range.total_charges_low.toFixed(2)}–$${planCharges.estimate_range.total_charges_high.toFixed(2)}`
                    : `₹${planCharges.estimate_range.total_charges_low.toLocaleString('en-IN')}–${planCharges.estimate_range.total_charges_high.toLocaleString('en-IN')}`}
                </span>
                {planCharges.estimate_range.note && (
                  <p className="mt-1 text-muted-foreground">{planCharges.estimate_range.note}</p>
                )}
              </div>
            )}

          {!hasData && !isChargesLoading && (
            <p className="text-muted-foreground">Add legs with prices to compute brokerage &amp; GST.</p>
          )}

          {planCharges?.total && (
            <div className="grid grid-cols-2 gap-x-3 gap-y-0.5 tabular-nums text-foreground">
              {planCharges.total.brokerage !== undefined && (
                <span>Brokerage: ₹{planCharges.total.brokerage}</span>
              )}
              {planCharges.total.stt !== undefined && <span>STT: ₹{planCharges.total.stt}</span>}
              {planCharges.total.exchange !== undefined && (
                <span>Exchange: ₹{planCharges.total.exchange}</span>
              )}
              {planCharges.total.gst !== undefined && (
                <span className="font-medium text-amber-700 dark:text-amber-400">
                  GST: ₹{planCharges.total.gst}
                </span>
              )}
              {planCharges.total.stamp !== undefined && (
                <span>Stamp: ₹{planCharges.total.stamp}</span>
              )}
              {planCharges.total.sebi !== undefined && <span>SEBI: ₹{planCharges.total.sebi}</span>}
              {planCharges.round_trip_charges !== undefined && (
                <span className="col-span-2 font-semibold text-amber-600 dark:text-amber-400">
                  Round-trip: ₹{planCharges.round_trip_charges}
                </span>
              )}
              {planCharges.net_debit_credit !== undefined && (
                <span className="col-span-2 font-semibold">
                  Net debit/credit: ₹{planCharges.net_debit_credit}
                </span>
              )}
            </div>
          )}

          {legCount > 0 && (
            <Collapsible open={perLegOpen} onOpenChange={setPerLegOpen}>
              <CollapsibleTrigger className="flex w-full items-center gap-2 rounded-md border border-border/40 bg-background/40 px-2 py-1.5 text-left transition-colors hover:bg-muted/30">
                <ChevronDown
                  className={cn(
                    'h-3 w-3 shrink-0 text-muted-foreground transition-transform',
                    perLegOpen && 'rotate-180'
                  )}
                />
                <span className="flex-1 text-[10px] font-semibold uppercase tracking-[0.1em] text-muted-foreground">
                  Per transaction ({legCount})
                </span>
                <span className="text-[10px] tabular-nums text-muted-foreground">
                  {entryTotal !== undefined ? `₹${entryTotal.toLocaleString('en-IN')} total` : ''}
                </span>
              </CollapsibleTrigger>
              <CollapsibleContent className="space-y-2 pt-2">
                {planCharges?.per_leg?.map((leg, i) => (
                  <div
                    key={`${leg.symbol}-${i}`}
                    className="rounded-md border border-border/40 bg-background/60 px-2 py-1.5"
                  >
                    <div className="flex items-center justify-between gap-2 font-medium text-foreground">
                      <div className="min-w-0 truncate">
                        <span
                          className={cn(
                            'mr-1.5 inline-flex h-4 w-5 items-center justify-center rounded text-[9px] font-bold',
                            leg.side === 'SELL'
                              ? 'bg-rose-500/15 text-rose-700 dark:text-rose-400'
                              : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400'
                          )}
                        >
                          {leg.side === 'SELL' ? 'S' : 'B'}
                        </span>
                        {leg.symbol}
                      </div>
                      {leg.total_charges !== undefined && (
                        <span className="shrink-0 font-semibold tabular-nums">
                          ₹{leg.total_charges}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 grid grid-cols-2 gap-x-2 gap-y-0.5 tabular-nums text-muted-foreground">
                      {leg.brokerage !== undefined && <span>Brokerage ₹{leg.brokerage}</span>}
                      {leg.stt !== undefined && <span>STT ₹{leg.stt}</span>}
                      {leg.gst !== undefined && (
                        <span className="font-medium text-amber-700 dark:text-amber-400">
                          GST ₹{leg.gst}
                        </span>
                      )}
                      {leg.stamp !== undefined && <span>Stamp ₹{leg.stamp}</span>}
                      {leg.exchange !== undefined && <span>Exchange ₹{leg.exchange}</span>}
                      {leg.source_note && (
                        <span className="col-span-2 text-[10px] text-muted-foreground">
                          {leg.source_note}
                        </span>
                      )}
                    </div>
                  </div>
                ))}
              </CollapsibleContent>
            </Collapsible>
          )}
        </CollapsibleContent>
      </Collapsible>
    </div>
  )
}
