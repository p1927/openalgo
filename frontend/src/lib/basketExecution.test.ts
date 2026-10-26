import { describe, expect, it } from 'vitest'
import type { StrategyLeg } from '@/lib/strategyMath'
import { buildBasketOrders, rowFromLeg } from './basketExecution'

function leg(o: Partial<StrategyLeg> = {}): StrategyLeg {
  return {
    id: 'a',
    segment: 'OPTION',
    side: 'BUY',
    lots: 2,
    lotSize: 25,
    expiry: '28AUG26',
    strike: 25000,
    optionType: 'CE',
    price: 10.03,
    iv: 12,
    active: true,
    symbol: 'A',
    exchange: 'NFO',
    tickSize: 0.05,
    contractValid: true,
    ...o,
  }
}

describe('buildBasketOrders (the one shared gate)', () => {
  it('empty included rows builds nothing', () => {
    const r = rowFromLeg(leg())
    expect(buildBasketOrders([{ ...r, include: false }], 'NFO', 'LIMIT', 'NRML')).toEqual({
      bad: null,
    })
    expect(buildBasketOrders([], 'NFO', 'LIMIT', 'NRML')).toEqual({ bad: null })
  })

  it('LIMIT with a missing or zero touch price refuses', () => {
    const r = rowFromLeg(leg({ price: 0 }))
    expect(r.price).toBeNull()
    expect(buildBasketOrders([r], 'NFO', 'LIMIT', 'NRML')).toEqual({ bad: r })
  })

  it('snaps to the tick and multiplies lots by lot size', () => {
    const built = buildBasketOrders([rowFromLeg(leg())], 'NFO', 'LIMIT', 'NRML')
    expect('orders' in built && built.orders[0]).toMatchObject({
      price: 10.05,
      quantity: 50,
      pricetype: 'LIMIT',
    })
  })

  it('an unresolved contract cannot become a row', () => {
    expect(() => rowFromLeg(leg({ contractValid: false }))).toThrow()
  })
})
