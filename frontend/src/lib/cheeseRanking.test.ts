import { describe, expect, it } from 'vitest'
import type { OptionData, OptionStrike } from '@/types/option-chain'
import { buildCheeseRequest, skipReasonText } from './cheeseRanking'
import { computeMispricing } from './optionMispricing'

function leg(ltp: number, iv: number | undefined): OptionData {
  return {
    symbol: 'X',
    label: 'X',
    ltp,
    bid: ltp - 0.1,
    ask: ltp + 0.1,
    bid_qty: 1,
    ask_qty: 1,
    open: 0,
    high: 0,
    low: 0,
    prev_close: 0,
    volume: 0,
    oi: 1000,
    lotsize: 50,
    tick_size: 0.05,
    implied_volatility: iv,
    delta: 0.4,
  }
}

const chain: OptionStrike[] = [90, 95, 100, 105, 110].map((strike) => ({
  strike,
  ce: leg(Math.max(100 - strike, 0) + 3, 20),
  pe: strike === 110 ? null : leg(Math.max(strike - 100, 0) + 3, undefined),
}))

describe('buildCheeseRequest', () => {
  const rows = computeMispricing(chain, 100, 30 / 365)

  it('ships the chain quotes and the smile-fit edges per strike, and the days to expiry', () => {
    const req = buildCheeseRequest('NIFTY', '28OCT26', 100, 30 / 365, chain, rows)
    expect(req).not.toBeNull()
    expect(req?.dte_days).toBe(30)
    expect(req?.chain).toHaveLength(5)
    expect(req?.chain[0].ce).toMatchObject({ ltp: 13, iv: 20, delta: 0.4 })
    expect(req?.chain[0].pe?.iv).toBeNull()
    expect(req?.chain[4].pe).toBeNull()
    const row0 = rows?.[0]
    expect(req?.edges[0].CE).toEqual({
      score: row0?.ce?.score,
      classification: row0?.ce?.classification,
    })
    expect(req?.edges[4].PE).toBeNull()
  })

  it.each([
    ['no chain', null, rows, 100, 0.1],
    ['no mispricing rows', chain, null, 100, 0.1],
    ['no spot', chain, rows, null, 0.1],
    ['expired', chain, rows, 100, 0],
  ])('asks for nothing when there is %s', (_name, c, r, spot, years) => {
    expect(buildCheeseRequest('NIFTY', '28OCT26', spot, years, c, r)).toBeNull()
  })

  it('names every backend skip reason in plain words', () => {
    expect(skipReasonText('closes_unavailable')).toMatch(/price history/)
    expect(skipReasonText('atm_iv_unavailable')).toMatch(/implied volatility/)
    expect(skipReasonText('something_new')).toContain('something_new')
  })
})
