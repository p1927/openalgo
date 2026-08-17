
import { describe, expect, it } from 'vitest'
import { computePayoff, type OptionType, type Side, type StrategyLeg } from './strategyMath'

const NOW = new Date('2026-07-28T10:00:00.000Z')
const EXPIRY_DAYS = 7

function optionLeg(id: string, side: Side, optionType: OptionType, strike: number, price: number, lots = 1, expiry = '04AUG26'): StrategyLeg {
  return { id, segment: 'OPTION', side, lots, lotSize: 1, expiry, strike, optionType, price, iv: 20, active: true, symbol: `NIFTY04AUG26${strike}${optionType}` }
}

describe('debug', () => {
  it('debug', () => {
    const legs = [optionLeg('lc', 'BUY', 'CE', 100, 5)]
    const without = computePayoff(legs, 100, EXPIRY_DAYS, 0, [80, 120], EXPIRY_DAYS, 0, 20, NOW)
    const withC = computePayoff(legs, 100, EXPIRY_DAYS, 0, [80, 120], EXPIRY_DAYS, 0, 20, NOW, { perLegCharges: { lc: 12.5 } })
    console.log('without.samples.length', without.samples.length)
    console.log('withC.samples.length', withC.samples.length)
    for (let i = 0; i < without.samples.length; i++) {
      const w = without.samples[i]
      const c = withC.samples[i]
      console.log(`i=${i} underlying=${w.underlying} without.expiry=${w.expiry} withC.expiry=${c.expiry} diff=${c.expiry - w.expiry}`)
    }
    console.log('without.maxProfit', without.maxProfit)
    console.log('withC.maxProfit', withC.maxProfit)
    console.log('without.maxLoss', without.maxLoss)
    console.log('withC.maxLoss', withC.maxLoss)
    console.log('without.breakevens', without.breakevens)
    console.log('withC.breakevens', withC.breakevens)
  })
})
