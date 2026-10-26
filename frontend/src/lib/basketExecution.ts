import { useCallback, useState } from 'react'
import { type BasketOrderItem, type BasketOrderResult, tradingApi } from '@/api/trading'
import { isLegExecutable, type StrategyLeg } from '@/lib/strategyMath'
import { showToast } from '@/utils/toast'

/**
 * The one basket-execution path (D306). ExecuteBasketDialog and
 * ExecutePlanWizard both build rows, snap ticks, build the payload, submit and
 * handle the per-leg result here. Do not call tradingApi.placeBasketOrder
 * anywhere else.
 */

export type PriceType = 'LIMIT' | 'MARKET'
export type ProductType = 'NRML' | 'MIS'

export interface BasketRow {
  legId: string
  contractKey: string
  include: boolean
  symbol: string
  action: 'BUY' | 'SELL'
  segment: 'OPTION' | 'FUTURE'
  optionType?: 'CE' | 'PE'
  /** Lots the user buys/sells. Contract quantity = lots x lotSize. */
  lots: number
  lotSize: number
  price: number | null
  tickSize: number
}

function toScaledInteger(value: number): { coefficient: bigint; exponent: number } {
  const [significand, exponentText] = value.toString().toLowerCase().split('e')
  const decimalPoint = significand.indexOf('.')
  const fractionDigits = decimalPoint === -1 ? 0 : significand.length - decimalPoint - 1
  return {
    coefficient: BigInt(significand.replace('.', '')),
    exponent: (exponentText ? Number(exponentText) : 0) - fractionDigits,
  }
}

/** Snap `value` to the nearest multiple of `tick` with exact decimal half-up rounding. */
export function roundToTick(value: number, tick: number): number | null {
  if (!Number.isFinite(value) || value <= 0) return null
  if (!Number.isFinite(tick) || tick <= 0) return null
  const valueParts = toScaledInteger(value)
  const tickParts = toScaledInteger(tick)
  let numerator = valueParts.coefficient
  let denominator = tickParts.coefficient
  if (valueParts.exponent > tickParts.exponent) {
    numerator *= 10n ** BigInt(valueParts.exponent - tickParts.exponent)
  } else if (tickParts.exponent > valueParts.exponent) {
    denominator *= 10n ** BigInt(tickParts.exponent - valueParts.exponent)
  }
  let multiples = numerator / denominator
  if ((numerator % denominator) * 2n >= denominator) multiples += 1n
  const rounded = Number(`${multiples * tickParts.coefficient}e${tickParts.exponent}`)
  return Number.isFinite(rounded) && rounded > 0 ? rounded : null
}

export function contractKey(leg: StrategyLeg): string {
  return [
    leg.id,
    leg.exchange ?? '',
    leg.symbol,
    leg.segment,
    leg.expiry,
    leg.strike ?? '',
    leg.optionType ?? '',
    leg.side,
  ].join('|')
}

export function rowFromLeg(leg: StrategyLeg): BasketRow {
  if (!isLegExecutable(leg)) throw new Error('Rows require executable legs')
  return {
    legId: leg.id,
    contractKey: contractKey(leg),
    include: true,
    symbol: leg.symbol,
    action: leg.side,
    segment: leg.segment,
    optionType: leg.optionType,
    lots: leg.lots,
    lotSize: leg.lotSize,
    price: roundToTick(leg.price, leg.tickSize),
    tickSize: leg.tickSize,
  }
}

export function hasInvalidLimitPrice(rows: BasketRow[], pricetype: PriceType): boolean {
  return (
    pricetype === 'LIMIT' &&
    rows.some((r) => r.include && (r.price === null || !Number.isFinite(r.price) || r.price <= 0))
  )
}

/**
 * Included rows -> broker orders. Final tick snap here in case Execute was hit
 * before a manually edited price blurred. Returns the offending row when a LIMIT
 * price is missing/invalid (caller refuses; nothing is sent), and refuses an
 * empty selection.
 */
export function buildBasketOrders(
  rows: BasketRow[],
  exchange: string,
  pricetype: PriceType,
  product: ProductType
): { orders: BasketOrderItem[] } | { bad: BasketRow | null } | { badQuantity: BasketRow } {
  const included = rows.filter((r) => r.include)
  if (included.length === 0) return { bad: null }
  // Quantity must be a positive whole number of contracts; 0 / NaN / fractional never reach the broker.
  const badQuantity = included.find((r) => {
    const q = r.lots * r.lotSize
    return (
      !Number.isInteger(r.lots) || !Number.isInteger(r.lotSize) || !Number.isInteger(q) || q <= 0
    )
  })
  if (badQuantity) return { badQuantity }
  const normalized = included.map((row) => ({
    row,
    price: pricetype === 'LIMIT' ? roundToTick(row.price ?? 0, row.tickSize) : 0,
  }))
  if (pricetype === 'LIMIT') {
    const bad = normalized.find(({ price }) => price === null || price <= 0)
    if (bad) return { bad: bad.row }
  }
  return {
    orders: normalized.map(({ row, price }) => ({
      symbol: row.symbol,
      exchange,
      action: row.action,
      quantity: row.lots * row.lotSize,
      pricetype,
      product,
      price: price ?? 0,
      trigger_price: 0,
    })),
  }
}

export interface BasketOutcome {
  results: BasketOrderResult[]
  /** Symbols of legs the broker rejected: a partial basket leaves these unfilled. */
  unfilled: string[]
}

/**
 * Match broker results to the orders we sent. The backend returns results
 * keyed by symbol only (BUYs first, completion order, a failed leg may be
 * dropped), so index/count matching is wrong. Each order consumes one
 * `success` result with its symbol; any order left without one is unfilled.
 */
export function unfilledSymbols(orders: BasketOrderItem[], results: BasketOrderResult[]): string[] {
  const ok = new Map<string, number>()
  for (const r of results) if (r.status === 'success') ok.set(r.symbol, (ok.get(r.symbol) ?? 0) + 1)
  const unfilled: string[] = []
  for (const o of orders) {
    const n = ok.get(o.symbol) ?? 0
    if (n > 0) ok.set(o.symbol, n - 1)
    else unfilled.push(o.symbol)
  }
  return unfilled
}

export function useBasketSubmit() {
  const [submitting, setSubmitting] = useState(false)
  const [results, setResults] = useState<BasketOrderResult[] | null>(null)
  const [unfilled, setUnfilled] = useState<string[]>([])

  const reset = useCallback(() => {
    setResults(null)
    setUnfilled([])
  }, [])

  /** Returns the outcome when the broker answered, null when nothing was placed. */
  const submit = async (
    apiKey: string,
    strategyName: string,
    rows: BasketRow[],
    exchange: string,
    pricetype: PriceType,
    product: ProductType,
    onBadPrice?: (row: BasketRow) => void
  ): Promise<BasketOutcome | null> => {
    if (!apiKey || strategyName.trim().length === 0) return null
    const built = buildBasketOrders(rows, exchange, pricetype, product)
    if ('badQuantity' in built) {
      onBadPrice?.(built.badQuantity)
      showToast.error(
        `${built.badQuantity.symbol}: quantity must be a positive whole number of lots`
      )
      return null
    }
    if ('bad' in built) {
      if (built.bad) {
        onBadPrice?.(built.bad)
        showToast.error(`${built.bad.symbol}: LIMIT needs a valid price`)
      }
      return null
    }
    setSubmitting(true)
    try {
      const resp = await tradingApi.placeBasketOrder(apiKey, strategyName.trim(), built.orders)
      if (resp.status !== 'success') {
        showToast.error(resp.message || 'Basket order failed')
        return null
      }
      // Top-level status is "success" even when individual legs were rejected:
      // the per-leg results are the truth, so a rejected leg must be loud.
      const list = resp.results ?? []
      const failedSymbols = unfilledSymbols(built.orders, list)
      const modeNote = resp.mode === 'analyze' ? ' (analyzer, no real orders)' : ''
      setResults(list)
      setUnfilled(failedSymbols)
      const sent = built.orders.length
      if (list.length === 0) {
        showToast.error('Basket response carried no per-leg results; check the order book')
      } else if (failedSymbols.length > 0) {
        showToast.error(
          `Basket partial: ${sent - failedSymbols.length} ok, ${failedSymbols.length} rejected or unconfirmed (of ${sent})${modeNote}. Review the legs and close any filled leg.`
        )
      } else {
        showToast.success(`Basket placed: ${sent}/${sent} orders${modeNote}`)
      }
      return { results: list, unfilled: failedSymbols }
    } catch (err) {
      showToast.error(err instanceof Error ? err.message : 'Basket order failed')
      return null
    } finally {
      setSubmitting(false)
    }
  }

  return { submitting, results, unfilled, submit, reset }
}
