import { describe, expect, it } from 'vitest'
import { black76Price } from './optionGreeks'
import { computeMispricing } from './optionMispricing'
import type { OptionData, OptionStrike } from '@/types/option-chain'

const FORWARD = 24000
const YEARS = 7 / 365
const STRIKE_STEP = 200

/** A mild smile: further from the forward costs a bit more vol. */
function trueSmileIv(strike: number): number {
  const x = Math.log(strike / FORWARD)
  return 0.14 + 0.6 * x * x
}

function buildLeg(strike: number, flag: 'c' | 'p', ivDecimal: number): OptionData {
  const price = black76Price(flag, FORWARD, strike, YEARS, 0, ivDecimal)
  const spread = Math.max(price * 0.004, 0.05)
  return {
    symbol: `TEST${strike}${flag.toUpperCase()}`,
    label: `${strike}${flag === 'c' ? 'CE' : 'PE'}`,
    ltp: price,
    bid: price - spread / 2,
    ask: price + spread / 2,
    bid_qty: 100,
    ask_qty: 100,
    open: price,
    high: price,
    low: price,
    prev_close: price,
    volume: 1000,
    oi: 5000,
    lotsize: 50,
    tick_size: 0.05,
    implied_volatility: ivDecimal * 100,
  }
}

/** Seven strikes around the forward, all priced exactly on the fair smile. */
function buildFairChain(): OptionStrike[] {
  const strikes = [-3, -2, -1, 0, 1, 2, 3].map((n) => FORWARD + n * STRIKE_STEP)
  return strikes.map((strike) => ({
    strike,
    ce: buildLeg(strike, 'c', trueSmileIv(strike)),
    pe: buildLeg(strike, 'p', trueSmileIv(strike)),
  }))
}

describe('computeMispricing', () => {
  it('returns null without a forward or time to expiry', () => {
    const chain = buildFairChain()
    expect(computeMispricing(chain, null, YEARS)).toBeNull()
    expect(computeMispricing(chain, FORWARD, 0)).toBeNull()
  })

  it('flags every leg neutral when the whole chain sits on one smooth smile', () => {
    const rows = computeMispricing(buildFairChain(), FORWARD, YEARS, 0, 0.08)
    expect(rows).not.toBeNull()
    for (const row of rows ?? []) {
      expect(row.ce?.classification).toBe('neutral')
      expect(row.pe?.classification).toBe('neutral')
    }
  })

  it('flags a strike trading well under its neighbors implied fair value as cheap', () => {
    const chain = buildFairChain()
    const target = FORWARD + STRIKE_STEP
    const row = chain.find((r) => r.strike === target)
    if (!row) throw new Error('fixture strike missing')
    // This call actually trades at a much lower implied vol than its
    // neighbors imply for that strike — a real relative-value discount.
    row.ce = buildLeg(target, 'c', trueSmileIv(target) - 0.05)

    const rows = computeMispricing(chain, FORWARD, YEARS, 0, 0.08)
    const scored = rows?.find((r) => r.strike === target)?.ce
    expect(scored).toBeTruthy()
    expect(scored?.classification).toBe('cheap')
    expect(scored?.edge).toBeLessThan(0)
    expect(scored?.score).toBeLessThan(0)
  })

  it('flags a strike trading well over its neighbors implied fair value as rich', () => {
    const chain = buildFairChain()
    const target = FORWARD - STRIKE_STEP
    const row = chain.find((r) => r.strike === target)
    if (!row) throw new Error('fixture strike missing')
    row.pe = buildLeg(target, 'p', trueSmileIv(target) + 0.05)

    const rows = computeMispricing(chain, FORWARD, YEARS, 0, 0.08)
    const scored = rows?.find((r) => r.strike === target)?.pe
    expect(scored).toBeTruthy()
    expect(scored?.classification).toBe('rich')
    expect(scored?.edge).toBeGreaterThan(0)
    expect(scored?.score).toBeGreaterThan(0)
  })

  it('leaves an illiquid one-sided quote unscored rather than misclassified', () => {
    const chain = buildFairChain()
    const target = FORWARD
    const row = chain.find((r) => r.strike === target)
    if (!row || !row.ce) throw new Error('fixture strike missing')
    row.ce = { ...row.ce, bid: 0, ask: 0 }

    const rows = computeMispricing(chain, FORWARD, YEARS, 0, 0.08)
    const scored = rows?.find((r) => r.strike === target)?.ce
    // No bid/ask means priceForGreeks falls back to ltp, so it can still
    // score off the last trade — but it must never be reported liquid.
    expect(scored?.liquid).toBe(false)
  })
})
