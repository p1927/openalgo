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

import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
// ---- QA defects D4 / D5 / D9 ----
import { act, renderHook } from '@testing-library/react'
import { beforeEach, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import { unfilledSymbols, useBasketSubmit } from './basketExecution'

vi.mock('@/api/trading', async (orig) => {
  const actual = await orig<typeof import('@/api/trading')>()
  return { ...actual, tradingApi: { ...actual.tradingApi, placeBasketOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))
const place = vi.mocked(tradingApi.placeBasketOrder)
const ok = (symbol: string) => ({ symbol, status: 'success' as const, orderid: '1' })
const err = (symbol: string) => ({ symbol, status: 'error' as const })

async function run(rows: ReturnType<typeof rowFromLeg>[]) {
  const { result } = renderHook(() => useBasketSubmit())
  let outcome: Awaited<ReturnType<typeof result.current.submit>> = null
  await act(async () => {
    outcome = await result.current.submit('k', 's', rows, 'NFO', 'LIMIT', 'NRML')
  })
  return outcome as Awaited<ReturnType<typeof result.current.submit>>
}
const two = () => [
  rowFromLeg(leg({ id: 'a', symbol: 'A' })),
  rowFromLeg(leg({ id: 'b', symbol: 'B' })),
]

describe('D4 quantity validation', () => {
  beforeEach(() => place.mockReset())
  it.each([
    ['zero lots', { lots: 0 }],
    ['NaN lots', { lots: Number.NaN }],
    ['fractional lots', { lots: 1.5 }],
    ['negative lots', { lots: -1 }],
    ['Infinity lots', { lots: Number.POSITIVE_INFINITY }],
    ['zero lot size', { lotSize: 0 }],
    ['fractional lot size', { lotSize: 0.5 }],
  ])('%s places nothing', async (_n, patch) => {
    const rows = [rowFromLeg(leg()), { ...rowFromLeg(leg({ id: 'b', symbol: 'B' })), ...patch }]
    const built = buildBasketOrders(rows, 'NFO', 'LIMIT', 'NRML')
    expect('badQuantity' in built).toBe(true)
    expect(await run(rows)).toBeNull()
    expect(place).not.toHaveBeenCalled()
  })
  it('an excluded bad-quantity row is ignored', () => {
    const rows = [rowFromLeg(leg()), { ...rowFromLeg(leg({ id: 'b' })), lots: 0, include: false }]
    expect('orders' in buildBasketOrders(rows, 'NFO', 'LIMIT', 'NRML')).toBe(true)
  })
})

describe('D5 result matching', () => {
  beforeEach(() => place.mockReset())
  it('short result list (1 for 2 orders) is unfilled, never success', async () => {
    place.mockResolvedValue({ status: 'success', results: [ok('A')] })
    expect((await run(two()))?.unfilled).toEqual(['B'])
  })
  it('results in a different order than sent still match by symbol', async () => {
    place.mockResolvedValue({ status: 'success', results: [ok('B'), ok('A')] })
    expect((await run(two()))?.unfilled).toEqual([])
  })
  it('a result without status counts as unfilled', async () => {
    place.mockResolvedValue({
      status: 'success',
      results: [ok('A'), { symbol: 'B' } as unknown as ReturnType<typeof err>],
    })
    expect((await run(two()))?.unfilled).toEqual(['B'])
  })
  it('an extra success for an unknown symbol does not cover a missing leg', async () => {
    place.mockResolvedValue({ status: 'success', results: [ok('A'), ok('Z')] })
    expect((await run(two()))?.unfilled).toEqual(['B'])
  })
  it('duplicate symbols: one success covers one order only', () => {
    const orders = [{ symbol: 'A' }, { symbol: 'A' }] as never
    expect(unfilledSymbols(orders, [ok('A'), err('A')])).toEqual(['A'])
    expect(unfilledSymbols(orders, [ok('A'), ok('A')])).toEqual([])
  })
  it('empty results leave every leg unfilled', async () => {
    place.mockResolvedValue({ status: 'success', results: [] })
    expect((await run(two()))?.unfilled).toEqual(['A', 'B'])
  })
})

describe('D9 one basket path', () => {
  it('only basketExecution.ts and api/trading.ts mention placeBasketOrder outside tests', () => {
    const hits: string[] = []
    const walk = (d: string) => {
      for (const n of readdirSync(d)) {
        const p = join(d, n)
        if (statSync(p).isDirectory()) walk(p)
        else if (
          /\.(ts|tsx)$/.test(n) &&
          !/\.test\./.test(n) &&
          /placeBasketOrder/.test(readFileSync(p, 'utf8'))
        )
          hits.push(p.replace(/.*\/src\//, ''))
      }
    }
    walk(join(__dirname, '..'))
    expect(hits.sort()).toEqual(['api/trading.ts', 'lib/basketExecution.ts'])
  }, 60000)
})
