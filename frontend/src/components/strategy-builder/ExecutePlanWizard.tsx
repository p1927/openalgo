import { CheckCircle2, ChevronLeft, ChevronRight, Loader2, ShieldCheck } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiClient } from '@/api/client'
import { type BasketOrderItem, type BasketOrderResult, tradingApi } from '@/api/trading'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import type { StrategyLeg } from '@/lib/strategyMath'
import { cn } from '@/lib/utils'
import { showToast } from '@/utils/toast'

export interface PlanImplementationStep {
  step: number
  action: string
  description?: string
  mcp_tool?: string | null
  payload?: Record<string, unknown> | null
}

export interface ExecutePlanWizardProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  legs: StrategyLeg[]
  exchange: string
  planName: string
  implementationSteps: PlanImplementationStep[]
  charges?: {
    total?: { total_charges?: number }
    net_debit_credit?: number
    round_trip_charges?: number
  } | null
  netPnl?: {
    net_max_profit?: number | null
    net_max_loss?: number | null
  } | null
  apiKey: string
}

type WizardStep = 'preview' | 'margin' | 'confirm' | 'execute'

const STEP_ORDER: WizardStep[] = ['preview', 'margin', 'confirm', 'execute']

export function ExecutePlanWizard({
  open,
  onOpenChange,
  legs,
  exchange,
  planName,
  implementationSteps,
  charges,
  netPnl,
  apiKey,
}: ExecutePlanWizardProps) {
  const [idx, setIdx] = useState(0)
  const [marginRequired, setMarginRequired] = useState<number | null>(null)
  const [marginLoading, setMarginLoading] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [results, setResults] = useState<BasketOrderResult[] | null>(null)

  const current = STEP_ORDER[idx]

  useEffect(() => {
    if (!open) return
    setIdx(0)
    setMarginRequired(null)
    setConfirmed(false)
    setResults(null)
  }, [open, planName])

  const activeLegs = useMemo(() => legs.filter((l) => l.active && l.symbol), [legs])

  const fetchMargin = useCallback(async () => {
    if (!apiKey || activeLegs.length === 0) return
    setMarginLoading(true)
    try {
      const positions = activeLegs.map((l) => ({
        exchange,
        symbol: l.symbol,
        action: l.side,
        quantity: String(l.lots * l.lotSize),
        product: 'NRML',
        pricetype: 'MARKET',
        price: '0',
      }))
      const res = await apiClient.post<{
        status: string
        data?: { total_margin_required?: number }
      }>('/margin', { positions })
      const total = res.data?.total_margin_required
      setMarginRequired(typeof total === 'number' ? total : null)
    } catch {
      setMarginRequired(null)
      showToast.error('Margin check failed')
    } finally {
      setMarginLoading(false)
    }
  }, [apiKey, activeLegs, exchange])

  useEffect(() => {
    if (open && current === 'margin') {
      void fetchMargin()
    }
  }, [open, current, fetchMargin])

  const executeBasket = async () => {
    if (!apiKey) return
    setSubmitting(true)
    try {
      const orders: BasketOrderItem[] = activeLegs.map((l) => ({
        symbol: l.symbol,
        exchange,
        action: l.side,
        quantity: l.lots * l.lotSize,
        pricetype: 'MARKET',
        product: 'NRML',
      }))
      const res = await tradingApi.placeBasketOrder(apiKey, planName, orders)
      setResults(res.results ?? [])
      showToast.success('Basket order submitted')
      setIdx(STEP_ORDER.length - 1)
    } catch (err) {
      showToast.error(err instanceof Error ? err.message : 'Basket order failed')
    } finally {
      setSubmitting(false)
    }
  }

  const canNext =
    (current === 'preview') ||
    (current === 'margin' && marginRequired !== null) ||
    (current === 'confirm' && confirmed) ||
    current === 'execute'

  const goNext = () => {
    if (current === 'confirm' && idx === STEP_ORDER.indexOf('confirm')) {
      void executeBasket()
      return
    }
    if (idx < STEP_ORDER.length - 1) setIdx(idx + 1)
  }

  const goBack = () => {
    if (idx > 0) setIdx(idx - 1)
  }

  const stepMeta = implementationSteps.find((s) => s.action === current)

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Execute trade plan — {planName}</DialogTitle>
          <DialogDescription>
            Step {idx + 1} of {STEP_ORDER.length}: {current.replace('_', ' ')}
            {stepMeta?.description ? ` — ${stepMeta.description}` : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          {current === 'preview' && (
            <>
              <div className="rounded-lg border bg-muted/30 p-3">
                <div className="font-semibold">Legs ({activeLegs.length})</div>
                <ul className="mt-2 space-y-1 text-xs">
                  {activeLegs.map((l) => (
                    <li key={l.id}>
                      {l.side} {l.lots}×{l.lotSize} {l.symbol} @ ₹{l.price}
                    </li>
                  ))}
                </ul>
              </div>
              {charges && (
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <span>Entry charges: ₹{charges.total?.total_charges ?? '—'}</span>
                  <span>Net debit/credit: ₹{charges.net_debit_credit ?? '—'}</span>
                  <span>Round-trip: ₹{charges.round_trip_charges ?? '—'}</span>
                  {netPnl && (
                    <>
                      <span>Net max profit: ₹{netPnl.net_max_profit ?? '—'}</span>
                      <span>Net max loss: ₹{netPnl.net_max_loss ?? '—'}</span>
                    </>
                  )}
                </div>
              )}
            </>
          )}

          {current === 'margin' && (
            <div className="flex items-center gap-3 rounded-lg border p-4">
              <ShieldCheck className="h-8 w-8 text-violet-500" />
              <div>
                <div className="font-semibold">Margin required</div>
                {marginLoading ? (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Checking…
                  </div>
                ) : (
                  <div className="text-lg font-bold tabular-nums">
                    {marginRequired !== null ? `₹${marginRequired.toLocaleString('en-IN')}` : '—'}
                  </div>
                )}
              </div>
            </div>
          )}

          {current === 'confirm' && (
            <label className="flex cursor-pointer items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/5 p-4">
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="mt-1"
              />
              <span>
                I confirm live execution of <strong>{planName}</strong> with {activeLegs.length}{' '}
                legs on {exchange}. I understand this places real orders.
              </span>
            </label>
          )}

          {current === 'execute' && results && (
            <div className="space-y-2">
              {results.map((r, i) => (
                <div
                  key={i}
                  className={cn(
                    'flex items-center gap-2 rounded border px-3 py-2 text-xs',
                    r.status === 'success' ? 'border-emerald-500/30' : 'border-rose-500/30'
                  )}
                >
                  {r.status === 'success' ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" />
                  ) : (
                    <span className="text-rose-500">✕</span>
                  )}
                  <span>{r.symbol}</span>
                  <span className="text-muted-foreground">{r.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <DialogFooter className="gap-2 sm:justify-between">
          <Button variant="outline" onClick={goBack} disabled={idx === 0 || submitting}>
            <ChevronLeft className="mr-1 h-4 w-4" /> Back
          </Button>
          {current !== 'execute' || !results ? (
            <Button onClick={goNext} disabled={!canNext || submitting}>
              {current === 'confirm' ? (
                submitting ? (
                  <>
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" /> Placing…
                  </>
                ) : (
                  'Place basket'
                )
              ) : (
                <>
                  Next <ChevronRight className="ml-1 h-4 w-4" />
                </>
              )}
            </Button>
          ) : (
            <Button onClick={() => onOpenChange(false)}>Done</Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
