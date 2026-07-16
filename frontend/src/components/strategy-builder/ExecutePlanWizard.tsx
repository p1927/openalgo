import { CheckCircle2, ChevronLeft, ChevronRight, Loader2, ShieldCheck, Wallet } from 'lucide-react'
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

export interface StockPlanOrder {
  symbol: string
  exchange: string
  action: 'BUY' | 'SELL' | 'HOLD'
  quantity: number
  product?: string
  entry?: number | null
  target?: number | null
  stop?: number | null
}

export interface ExecutePlanWizardProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  legs: StrategyLeg[]
  exchange: string
  planName: string
  implementationSteps: PlanImplementationStep[]
  planKind?: 'options' | 'stock'
  stockOrder?: StockPlanOrder | null
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

type WizardStep = 'preview' | 'margin' | 'funds' | 'confirm' | 'execute'

const OPTIONS_STEP_ORDER: WizardStep[] = ['preview', 'margin', 'confirm', 'execute']
const STOCK_STEP_ORDER: WizardStep[] = ['preview', 'funds', 'confirm', 'execute']

/** Hub implementation_steps use margin_check / execute_basket; wizard uses margin / execute. */
const HUB_ACTION_FOR_STEP: Record<WizardStep, string[]> = {
  preview: ['preview'],
  margin: ['margin', 'margin_check'],
  funds: ['funds'],
  confirm: ['confirm'],
  execute: ['execute', 'execute_basket'],
}

function detectPlanKind(steps: PlanImplementationStep[]): 'options' | 'stock' {
  if (steps.some((s) => s.action === 'funds')) return 'stock'
  return 'options'
}

export function ExecutePlanWizard({
  open,
  onOpenChange,
  legs,
  exchange,
  planName,
  implementationSteps,
  planKind: planKindProp,
  stockOrder,
  charges,
  netPnl,
  apiKey,
}: ExecutePlanWizardProps) {
  const planKind = planKindProp ?? detectPlanKind(implementationSteps)
  const stepOrder = planKind === 'stock' ? STOCK_STEP_ORDER : OPTIONS_STEP_ORDER

  const [idx, setIdx] = useState(0)
  const [marginRequired, setMarginRequired] = useState<number | null>(null)
  const [marginLoading, setMarginLoading] = useState(false)
  const [marginChecked, setMarginChecked] = useState(false)
  const [availableCash, setAvailableCash] = useState<number | null>(null)
  const [fundsLoading, setFundsLoading] = useState(false)
  const [fundsChecked, setFundsChecked] = useState(false)
  const [confirmed, setConfirmed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [results, setResults] = useState<BasketOrderResult[] | null>(null)

  const current = stepOrder[idx] ?? 'preview'

  const executePayload = useMemo(() => {
    const step = implementationSteps.find((s) => HUB_ACTION_FOR_STEP.execute.includes(s.action))
    return (step?.payload || null) as Record<string, unknown> | null
  }, [implementationSteps])

  const activeLegs = useMemo(() => legs.filter((l) => l.active && l.symbol), [legs])

  const orderNotional = useMemo(() => {
    if (planKind !== 'stock' || !stockOrder) return null
    const px = stockOrder.entry ?? 0
    return px > 0 ? px * stockOrder.quantity : null
  }, [planKind, stockOrder])

  useEffect(() => {
    if (!open) return
    setIdx(0)
    setMarginRequired(null)
    setMarginChecked(false)
    setAvailableCash(null)
    setFundsChecked(false)
    setConfirmed(false)
    setResults(null)
  }, [open, planName, planKind])

  const fetchMargin = useCallback(async () => {
    if (!apiKey || activeLegs.length === 0) {
      setMarginChecked(true)
      return
    }
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
      const total = res.data?.data?.total_margin_required
      setMarginRequired(typeof total === 'number' ? total : null)
    } catch {
      setMarginRequired(null)
      showToast.error('Margin check failed — you may proceed after reviewing legs manually')
    } finally {
      setMarginLoading(false)
      setMarginChecked(true)
    }
  }, [apiKey, activeLegs, exchange])

  const fetchFunds = useCallback(async () => {
    if (!apiKey) {
      setFundsChecked(true)
      return
    }
    setFundsLoading(true)
    try {
      const res = await tradingApi.getFunds(apiKey)
      const cash = res.data?.availablecash
      setAvailableCash(typeof cash === 'number' ? cash : null)
    } catch {
      setAvailableCash(null)
      showToast.error('Funds check failed — verify cash in broker app before proceeding')
    } finally {
      setFundsLoading(false)
      setFundsChecked(true)
    }
  }, [apiKey])

  useEffect(() => {
    if (!open || current !== 'margin') return
    void fetchMargin()
  }, [open, current, fetchMargin])

  useEffect(() => {
    if (!open || current !== 'funds') return
    void fetchFunds()
  }, [open, current, fetchFunds])

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
      if (res.status !== 'success') {
        showToast.error(res.message || 'Basket order failed')
        return
      }
      setResults(res.results ?? [])
      showToast.success('Basket order submitted')
      setIdx(stepOrder.length - 1)
    } catch (err) {
      showToast.error(err instanceof Error ? err.message : 'Basket order failed')
    } finally {
      setSubmitting(false)
    }
  }

  const executeStockOrder = async () => {
    if (!apiKey || !stockOrder) return
    if (stockOrder.action === 'HOLD') {
      showToast.error('Recommended action is HOLD — no order to place')
      return
    }
    const payload = executePayload ?? {}
    setSubmitting(true)
    try {
      const res = await tradingApi.placeOrder({
        apikey: apiKey,
        strategy: planName,
        exchange: String(payload.exchange || stockOrder.exchange || 'NSE'),
        symbol: String(payload.symbol || stockOrder.symbol),
        action: (payload.action as 'BUY' | 'SELL') || stockOrder.action,
        quantity: Number(payload.quantity ?? stockOrder.quantity),
        product: (payload.product as 'CNC' | 'MIS' | 'NRML') || 'CNC',
        pricetype: (payload.pricetype as 'MARKET' | 'LIMIT') || 'MARKET',
      })
      if (res.status !== 'success') {
        showToast.error(res.message || 'Order failed')
        setResults([
          {
            symbol: stockOrder.symbol,
            status: 'error',
            message: res.message || 'Order rejected',
          },
        ])
        setIdx(stepOrder.length - 1)
        return
      }
      setResults([
        {
          symbol: stockOrder.symbol,
          status: 'success',
          message: res.data?.orderid ? `Order ${res.data.orderid}` : 'Order placed',
        },
      ])
      showToast.success('Stock order submitted')
      setIdx(stepOrder.length - 1)
    } catch (err) {
      showToast.error(err instanceof Error ? err.message : 'Order failed')
    } finally {
      setSubmitting(false)
    }
  }

  const canNext =
    (current === 'preview') ||
    (current === 'margin' && marginChecked && !marginLoading) ||
    (current === 'funds' && fundsChecked && !fundsLoading) ||
    (current === 'confirm' && confirmed) ||
    current === 'execute'

  const goNext = () => {
    if (current === 'confirm' && idx === stepOrder.indexOf('confirm')) {
      if (planKind === 'stock') {
        void executeStockOrder()
      } else {
        void executeBasket()
      }
      return
    }
    if (idx < stepOrder.length - 1) setIdx(idx + 1)
  }

  const goBack = () => {
    if (idx > 0) setIdx(idx - 1)
  }

  const stepMeta = implementationSteps.find((s) =>
    HUB_ACTION_FOR_STEP[current].includes(s.action)
  )

  const insufficientCash =
    planKind === 'stock' &&
    orderNotional !== null &&
    availableCash !== null &&
    stockOrder?.action === 'BUY' &&
    availableCash < orderNotional

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Execute trade plan — {planName.replace(/_/g, ' ')}</DialogTitle>
          <DialogDescription>
            Step {idx + 1} of {stepOrder.length}: {current.replace('_', ' ')}
            {stepMeta?.description ? ` — ${stepMeta.description}` : ''}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 text-sm">
          {current === 'preview' && planKind === 'stock' && stockOrder && (
            <>
              <div className="rounded-lg border bg-muted/30 p-3">
                <div className="font-semibold">Equity order</div>
                <ul className="mt-2 space-y-1 text-xs">
                  <li>
                    {stockOrder.action} {stockOrder.quantity}× {stockOrder.symbol} @ ₹
                    {stockOrder.entry ?? '—'} ({stockOrder.product ?? 'CNC'})
                  </li>
                  {stockOrder.target != null && <li>Target: ₹{stockOrder.target}</li>}
                  {stockOrder.stop != null && <li>Stop: ₹{stockOrder.stop}</li>}
                </ul>
              </div>
              {charges && (
                <div className="grid grid-cols-2 gap-2 text-xs">
                  <span>Entry charges: ₹{charges.total?.total_charges ?? '—'}</span>
                </div>
              )}
            </>
          )}

          {current === 'preview' && planKind === 'options' && (
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
                ) : marginRequired !== null ? (
                  <div className="text-lg font-bold tabular-nums">
                    ₹{marginRequired.toLocaleString('en-IN')}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">
                    Unavailable — verify margin in broker app before proceeding
                  </div>
                )}
              </div>
            </div>
          )}

          {current === 'funds' && (
            <div className="flex items-center gap-3 rounded-lg border p-4">
              <Wallet className="h-8 w-8 text-emerald-500" />
              <div>
                <div className="font-semibold">Available cash</div>
                {fundsLoading ? (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" /> Checking…
                  </div>
                ) : availableCash !== null ? (
                  <div className="text-lg font-bold tabular-nums">
                    ₹{availableCash.toLocaleString('en-IN')}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground">
                    Unavailable — verify funds in broker app before proceeding
                  </div>
                )}
                {orderNotional !== null && stockOrder?.action === 'BUY' && (
                  <div
                    className={cn(
                      'mt-1 text-xs tabular-nums',
                      insufficientCash ? 'text-rose-600' : 'text-muted-foreground'
                    )}
                  >
                    Est. order value: ₹{orderNotional.toLocaleString('en-IN')}
                    {insufficientCash ? ' — may exceed available cash' : ''}
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
                I confirm live execution of <strong>{planName.replace(/_/g, ' ')}</strong>
                {planKind === 'stock' && stockOrder
                  ? ` — ${stockOrder.action} ${stockOrder.quantity} ${stockOrder.symbol} on ${stockOrder.exchange}`
                  : ` with ${activeLegs.length} legs on ${exchange}`}
                . I understand this places real orders.
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
                ) : planKind === 'stock' ? (
                  'Place order'
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
