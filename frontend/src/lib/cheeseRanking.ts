/**
 * Wire shapes and request building for Trade's cheese ranking (`POST /api/trade-cheese`).
 *
 * The smile-fit edge per strike (`optionMispricing.ts`) is the only mispricing scorer; this module
 * ships those edges plus the live chain to the backend, which ranks them by trend fit and P(ITM)
 * and builds combos. Nothing here scores a strike.
 */

import type { OptionData, OptionStrike } from '@/types/option-chain'
import type { MispricingRow } from './optionMispricing'

export interface CheeseLeg {
  strike: number
  option_type: 'CE' | 'PE'
  side_hint: 'BUY' | 'SELL' | 'NEITHER'
  ltp: number
  iv: number | null
  /** Probability of finishing in the money at expiry; null when the leg has no IV. */
  p_itm: number | null
  deal_score: number
  breakeven: number
  mispricing_score: number | null
  rationale: string[]
}

export interface CheeseComboLeg {
  side: 'BUY' | 'SELL'
  option_type: 'CE' | 'PE'
  strike: number
}

export interface CheeseCombo {
  name: string
  archetype: string
  rationale: string
  legs: CheeseComboLeg[]
  metrics: {
    pop: number | null
    max_profit: number | null
    max_loss: number | null
    net_debit_credit: number | null
    round_trip_charges: number | null
    breakevens: number[] | null
  }
}

export interface CheesePayload {
  status: 'ok' | 'skipped'
  skip_reason: string | null
  directional_view: {
    trend_score: number | null
    rsi14: number | null
    atm_iv_pct: number | null
    iv_regime: string | null
  }
  buys: CheeseLeg[]
  sells: CheeseLeg[]
  combos: CheeseCombo[]
}

export interface CheeseRequest {
  underlying: string
  expiry: string
  spot: number
  dte_days: number
  chain: {
    strike: number
    ce: CheeseLegQuote | null
    pe: CheeseLegQuote | null
  }[]
  edges: {
    strike: number
    CE: { score: number; classification: string } | null
    PE: { score: number; classification: string } | null
  }[]
}

interface CheeseLegQuote {
  ltp: number
  iv: number | null
  delta: number | null
  bid: number
  ask: number
  oi: number
}

function quote(leg: OptionData | null): CheeseLegQuote | null {
  if (!leg || !(leg.ltp > 0)) return null
  return {
    ltp: leg.ltp,
    iv: leg.implied_volatility ?? null,
    delta: leg.delta ?? null,
    bid: leg.bid,
    ask: leg.ask,
    oi: leg.oi,
  }
}

/** The request for one chain and its smile-fit rows; null while there is nothing to rank. */
export function buildCheeseRequest(
  underlying: string,
  expiry: string,
  spot: number | null,
  years: number,
  chain: OptionStrike[] | null,
  rows: MispricingRow[] | null
): CheeseRequest | null {
  if (!chain || !rows || !spot || !(spot > 0) || !(years > 0) || !underlying) return null
  const edge = (leg: MispricingRow['ce']) =>
    leg ? { score: leg.score, classification: leg.classification } : null
  return {
    underlying,
    expiry,
    spot,
    dte_days: Math.max(0, Math.round(years * 365)),
    chain: chain.map((row) => ({ strike: row.strike, ce: quote(row.ce), pe: quote(row.pe) })),
    edges: rows.map((row) => ({ strike: row.strike, CE: edge(row.ce), PE: edge(row.pe) })),
  }
}

/** Plain-language reason for a skipped ranking (the backend's skip_reason codes). */
export function skipReasonText(reason: string | null): string {
  switch (reason) {
    case 'closes_unavailable':
      return 'Not enough daily price history for this symbol to read a trend.'
    case 'realized_vol_unavailable':
      return 'Not enough price history to measure realized volatility.'
    case 'atm_iv_unavailable':
      return 'No implied volatility near the money in this chain.'
    case 'no_mispricing_edges':
      return 'No mispricing edges to rank yet.'
    default:
      return reason ? `Ranking skipped: ${reason}` : 'Ranking skipped.'
  }
}
