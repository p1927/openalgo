/**
 * Relative-value mispricing scan for an option chain ("Find the Cheese").
 *
 * A leg's own implied volatility can't tell you whether it's mispriced —
 * inverting its own price to get IV and then pricing it back off that same
 * IV is circular by construction. What separates a genuine mispricing from
 * ordinary smile shape is comparing a leg against a smooth curve fit through
 * its *neighbors*: a strike whose IV sits well off the curve implied by the
 * rest of the chain is either cheap (buy candidate) or rich (sell candidate)
 * relative to its peers.
 *
 * CE and PE observations at the same strike are fit as one combined curve
 * rather than two independent ones: Black-76 put-call parity means a call
 * and a put at the same strike and forward should carry the same fair
 * volatility, so pooling both sides gives a sturdier fit and any leftover
 * CE/PE divergence becomes part of the mispricing signal itself rather than
 * being fit away.
 */

import { black76Price, priceForGreeks } from './optionGreeks'
import type { OptionData, OptionStrike } from '@/types/option-chain'

export type MispricingClassification = 'cheap' | 'rich' | 'neutral'

export interface MispricingLeg {
  strike: number
  flag: 'c' | 'p'
  price: number
  fairValue: number
  fittedIv: number
  edge: number
  edgePct: number
  /** Signed, clamped to [-1, 1]; magnitude drives fill opacity. */
  score: number
  classification: MispricingClassification
  /** Two-sided, non-crossed quote with open interest — the fit trusts these more. */
  liquid: boolean
}

export interface MispricingRow {
  strike: number
  ce: MispricingLeg | null
  pe: MispricingLeg | null
}

/** Edges smaller than this fraction of fair value, beyond the noise floor, stay neutral. */
const DEFAULT_EDGE_THRESHOLD_PCT = 0.08
/** Edge fraction (beyond the threshold) at which color intensity saturates. */
const COLOR_SATURATION_PCT = 0.35
/** Minimum quoted spread used as a liquidity weight floor, so a near-zero spread can't dominate the fit. */
const MIN_SPREAD_PCT = 0.02
/** Below this many weighted observations, a smile fit is too noisy to trust. */
const MIN_FIT_POINTS = 5

function liquidityWeight(bid: number, ask: number, oi: number): number {
  if (!(bid > 0) || !(ask > 0) || ask < bid || !(oi > 0)) return 0
  const mid = (bid + ask) / 2
  if (!(mid > 0)) return 0
  const spreadPct = (ask - bid) / mid
  return 1 / Math.max(spreadPct, MIN_SPREAD_PCT)
}

function isTwoSided(leg: OptionData): boolean {
  return leg.bid > 0 && leg.ask > 0 && leg.ask >= leg.bid
}

/** Solve a 3x3 linear system via Cramer's rule; null when singular. */
function solve3x3(A: number[][], b: number[]): number[] | null {
  const det3 = (m: number[][]) =>
    m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1]) -
    m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0]) +
    m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])

  const det = det3(A)
  if (!Number.isFinite(det) || Math.abs(det) < 1e-12) return null

  const withColumn = (col: number) =>
    A.map((row, i) => row.map((v, j) => (j === col ? b[i] : v)))

  return [det3(withColumn(0)) / det, det3(withColumn(1)) / det, det3(withColumn(2)) / det]
}

/**
 * Weighted quadratic fit of IV against log-moneyness `ln(K/F)`.
 *
 * Falls back to a flat weighted-average IV when there isn't enough
 * liquid data to trust a curved fit — flat is a safe, non-misleading default
 * since a small or singular sample shouldn't be allowed to invent curvature.
 */
function fitSmile(points: { x: number; iv: number; weight: number }[]): (x: number) => number {
  let sw = 0
  let swx = 0
  let swx2 = 0
  let swx3 = 0
  let swx4 = 0
  let swy = 0
  let swxy = 0
  let swx2y = 0

  for (const { x, iv, weight: w } of points) {
    const x2 = x * x
    const x3 = x2 * x
    const x4 = x2 * x2
    sw += w
    swx += w * x
    swx2 += w * x2
    swx3 += w * x3
    swx4 += w * x4
    swy += w * iv
    swxy += w * x * iv
    swx2y += w * x2 * iv
  }

  const flatAverage = sw > 0 ? swy / sw : 0
  if (points.length < MIN_FIT_POINTS || sw <= 0) return () => flatAverage

  const A = [
    [sw, swx, swx2],
    [swx, swx2, swx3],
    [swx2, swx3, swx4],
  ]
  const sol = solve3x3(A, [swy, swxy, swx2y])
  if (!sol) return () => flatAverage

  const [c, b, a] = sol
  return (x: number) => a * x * x + b * x + c
}

function clamp(value: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, value))
}

function classify(edgePct: number, threshold: number): MispricingClassification {
  if (edgePct <= -threshold) return 'cheap'
  if (edgePct >= threshold) return 'rich'
  return 'neutral'
}

function scoreLeg(
  flag: 'c' | 'p',
  strike: number,
  leg: OptionData,
  forward: number,
  years: number,
  r: number,
  fittedIv: (x: number) => number,
  edgeThresholdPct: number
): MispricingLeg | null {
  const price = priceForGreeks(leg.ltp, leg.bid, leg.ask)
  if (!(price > 0)) return null

  const iv = fittedIv(Math.log(strike / forward))
  if (!(iv > 0)) return null

  const fairValue = black76Price(flag, forward, strike, years, r, iv / 100)
  if (!(fairValue > 0)) return null

  const edge = price - fairValue
  const edgePct = edge / fairValue
  const liquid = isTwoSided(leg)
  const halfSpread = liquid ? (leg.ask - leg.bid) / 2 : 0
  const noiseFloor = Math.max(halfSpread, leg.tick_size || 0)
  const netEdge = Math.abs(edge) - noiseFloor
  const netEdgePct = netEdge > 0 ? (netEdge / fairValue) * Math.sign(edge) : 0
  const score = netEdge <= 0 ? 0 : clamp(netEdgePct / COLOR_SATURATION_PCT, -1, 1)

  return {
    strike,
    flag,
    price,
    fairValue,
    fittedIv: iv,
    edge,
    edgePct,
    score,
    classification: classify(netEdgePct, edgeThresholdPct),
    liquid,
  }
}

/**
 * Score every strike in a chain against a smile fit through its own liquid
 * strikes. Returns null when there isn't enough live data (no forward, no
 * time to expiry) to fit anything.
 */
export function computeMispricing(
  chain: OptionStrike[],
  forward: number | null,
  years: number,
  ratePct = 0,
  edgeThresholdPct = DEFAULT_EDGE_THRESHOLD_PCT
): MispricingRow[] | null {
  if (!(forward && forward > 0) || !(years > 0) || chain.length === 0) return null

  const r = ratePct / 100
  const points: { x: number; iv: number; weight: number }[] = []

  for (const row of chain) {
    const x = Math.log(row.strike / forward)
    for (const leg of [row.ce, row.pe]) {
      if (!leg || !leg.implied_volatility || !(leg.implied_volatility > 0)) continue
      const weight = liquidityWeight(leg.bid, leg.ask, leg.oi)
      if (weight <= 0) continue
      points.push({ x, iv: leg.implied_volatility, weight })
    }
  }

  const fittedIv = fitSmile(points)

  return chain.map((row) => ({
    strike: row.strike,
    ce: row.ce
      ? scoreLeg('c', row.strike, row.ce, forward, years, r, fittedIv, edgeThresholdPct)
      : null,
    pe: row.pe
      ? scoreLeg('p', row.strike, row.pe, forward, years, r, fittedIv, edgeThresholdPct)
      : null,
  }))
}
