/**
 * F&O charge calculator using published broker presets (Groww, INDmoney, Zerodha).
 * Statutory NSE rates are government/exchange mandated — identical across brokers.
 */

import type { PlanCharges } from '@/components/strategy-builder/ChargesBreakdown'
import presets from '@/lib/brokerChargePresets.json'

export type ChargeMarket = 'IN' | 'US'

export interface ChargeLegInput {
  symbol?: string
  side: 'BUY' | 'SELL'
  price: number
  quantity: number
  strike?: number
  option_type?: 'CE' | 'PE'
  segment?: string
}

type PresetFile = typeof presets

export function chargeMarketForExchange(exchange: string): ChargeMarket {
  const ex = (exchange || '').toUpperCase()
  if (['NFO', 'BFO', 'NSE', 'BSE', 'NSE_INDEX', 'BSE_INDEX', 'MCX', 'CDS'].includes(ex)) {
    return 'IN'
  }
  return 'US'
}

export function normalizeBrokerId(broker: string | null | undefined): string {
  const raw = (broker || '').trim().toLowerCase().replace(/\s+/g, '')
  const aliases: Record<string, string> = {
    indmoney: 'indmoney',
    ind: 'indmoney',
    groww: 'groww',
    zerodha: 'zerodha',
    kite: 'zerodha',
  }
  if (aliases[raw]) return aliases[raw]
  if (raw in presets.brokers) return raw
  return presets.default_broker
}

function legQty(leg: ChargeLegInput): number {
  return Math.max(1, Math.round(leg.quantity || 1))
}

function legTurnover(leg: ChargeLegInput): number {
  return leg.price * legQty(leg)
}

function isFuturesLeg(leg: ChargeLegInput): boolean {
  const seg = (leg.segment || '').toUpperCase()
  if (seg === 'FUTURE' || seg === 'FUT') return true
  const sym = (leg.symbol || '').toUpperCase()
  return sym.endsWith('FUT') && !sym.includes('CE') && !sym.includes('PE')
}

function round2(n: number): number {
  return Math.round(n * 100) / 100
}

function statutoryForLeg(turnover: number, side: 'BUY' | 'SELL', segment: 'options' | 'futures') {
  const stat =
    segment === 'futures' ? presets.statutory.nse_futures : presets.statutory.nse_options
  const stt = side === 'SELL' ? turnover * stat.stt_sell_rate : 0
  const exchange = turnover * stat.exchange_rate
  const sebi = turnover * (stat.sebi_per_crore / 1e7)
  const stamp = side === 'BUY' ? turnover * stat.stamp_buy_rate : 0
  return { stt, exchange, sebi, stamp, gstRate: stat.gst_rate }
}

function indianLegCharges(turnover: number, side: 'BUY' | 'SELL', brokerId: string, segment: 'options' | 'futures') {
  const brokerCfg = presets.brokers[brokerId as keyof PresetFile['brokers']]
  const brokerage =
    segment === 'futures'
      ? brokerCfg.fno_futures_brokerage_inr
      : brokerCfg.fno_options_brokerage_inr
  const stat = statutoryForLeg(turnover, side, segment)
  const gst = stat.gstRate * (brokerage + stat.exchange + stat.sebi)
  const total = brokerage + stat.stt + stat.exchange + gst + stat.stamp + stat.sebi
  return {
    brokerage: round2(brokerage),
    stt: round2(stat.stt),
    exchange: round2(stat.exchange),
    gst: round2(gst),
    stamp: round2(stat.stamp),
    sebi: round2(stat.sebi),
    total_charges: round2(total),
    turnover: round2(turnover),
    source: brokerId,
  }
}

function netDebitCredit(legs: ChargeLegInput[]): number {
  return legs.reduce((acc, leg) => {
    const sign = leg.side === 'BUY' ? -1 : 1
    return acc + sign * legTurnover(leg)
  }, 0)
}

export function calculateIndianCharges(
  legs: ChargeLegInput[],
  brokerId: string
): PlanCharges {
  if (!legs.length) return null
  const brokerCfg = presets.brokers[brokerId as keyof PresetFile['brokers']]
  const per_leg = legs.map((leg) => {
    const segment = isFuturesLeg(leg) ? 'futures' : 'options'
    const row = indianLegCharges(legTurnover(leg), leg.side, brokerId, segment)
    return { symbol: leg.symbol, side: leg.side, ...row }
  })
  const totals = {
    brokerage: 0,
    stt: 0,
    exchange: 0,
    gst: 0,
    stamp: 0,
    sebi: 0,
    total_charges: 0,
  }
  for (const row of per_leg) {
    totals.brokerage += row.brokerage ?? 0
    totals.stt += row.stt ?? 0
    totals.exchange += row.exchange ?? 0
    totals.gst += row.gst ?? 0
    totals.stamp += row.stamp ?? 0
    totals.sebi += row.sebi ?? 0
    totals.total_charges += row.total_charges ?? 0
  }
  for (const k of Object.keys(totals) as (keyof typeof totals)[]) {
    totals[k] = round2(totals[k])
  }
  return {
    per_leg,
    total: totals,
    broker_preset: brokerId,
    net_debit_credit: round2(netDebitCredit(legs)),
    charge_source: 'presets',
    broker_display: brokerCfg.display_name,
  }
}

/** @deprecated use calculateIndianCharges — kept for call-site compat */
export function estimateCharges(
  legs: ChargeLegInput[],
  market: ChargeMarket,
  connectedBroker?: string | null
): PlanCharges {
  if (!legs.length) return null
  if (market === 'US') {
    let total = 0
    const per_leg = legs.map((leg) => {
      const contracts = legQty(leg) >= 100 ? Math.max(1, Math.round(legQty(leg) / 100)) : legQty(leg)
      const fees = round2(contracts * 0.65)
      total += fees
      return { symbol: leg.symbol, side: leg.side, total_charges: fees, brokerage: fees, gst: 0 }
    })
    return {
      per_leg,
      total: { total_charges: round2(total), brokerage: round2(total), gst: 0 },
      broker_preset: 'us',
      charge_source: 'us_schedule',
      net_debit_credit: round2(netDebitCredit(legs)),
    }
  }
  return calculateIndianCharges(legs, normalizeBrokerId(connectedBroker))
}

export function legsToChargeInput(
  legs: Array<{
    symbol?: string
    side: 'BUY' | 'SELL'
    price: number
    lots: number
    lotSize: number
    strike?: number
    optionType?: 'CE' | 'PE'
    segment?: string
  }>
): ChargeLegInput[] {
  return legs.map((l) => ({
    symbol: l.symbol,
    side: l.side,
    price: l.price,
    quantity: l.lots * l.lotSize,
    strike: l.strike,
    option_type: l.optionType,
    segment: l.segment,
  }))
}
