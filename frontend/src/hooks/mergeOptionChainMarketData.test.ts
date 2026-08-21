import { describe, expect, it } from 'vitest'
import type { SymbolData } from '@/lib/MarketDataManager'
import type { OptionChainResponse } from '@/types/option-chain'
import { mergeOptionChainMarketData } from './useOptionChainLive'

function option(symbol: string, ltp: number) {
  return {
    symbol,
    label: symbol,
    ltp,
    bid: ltp - 1,
    ask: ltp + 1,
    bid_qty: 10,
    ask_qty: 10,
    open: ltp,
    high: ltp + 5,
    low: ltp - 5,
    prev_close: ltp - 2,
    volume: 100,
    oi: 200,
    lotsize: 65,
    tick_size: 5,
    implied_volatility: 15,
  }
}

function polledChain(): OptionChainResponse {
  return {
    status: 'success',
    underlying: 'NIFTY',
    underlying_symbol: 'NIFTY',
    underlying_exchange: 'NSE_INDEX',
    underlying_ltp: 24234,
    underlying_prev_close: 24232,
    expiry_date: '25AUG26',
    expiry_ts: 1787652000,
    server_ts: 1787304582,
    atm_strike: 24250,
    forward_price: 24286.7,
    chain: [
      {
        strike: 24250,
        ce: option('NIFTY25AUG2624250CE', 99.85),
        pe: option('NIFTY25AUG2624250PE', 63.4),
      },
    ],
  }
}

describe('mergeOptionChainMarketData', () => {
  it('keeps the polled price when a WebSocket tick reports ltp/bid/ask as 0', () => {
    // A depth-only or partial tick can legitimately carry ltp: 0 (the field
    // simply wasn't part of that message) rather than omitting it. That must
    // not overwrite a perfectly good polled price with a literal 0 -- doing
    // so zeroes every downstream Greek/IV computation for the leg (the
    // "Find the Cheese" all-dashes bug).
    const marketData = new Map<string, SymbolData>([
      [
        'NFO:NIFTY25AUG2624250CE',
        {
          symbol: 'NIFTY25AUG2624250CE',
          exchange: 'NFO',
          data: { ltp: 0, bid_price: 0, ask_price: 0 },
        },
      ],
    ])

    const merged = mergeOptionChainMarketData(polledChain(), 'NFO', marketData, 0)
    const ce = merged.chain[0].ce
    expect(ce?.ltp).toBe(99.85)
    expect(ce?.bid).toBe(98.85)
    expect(ce?.ask).toBe(100.85)
  })

  it('still applies a genuine nonzero tick price', () => {
    const marketData = new Map<string, SymbolData>([
      [
        'NFO:NIFTY25AUG2624250CE',
        {
          symbol: 'NIFTY25AUG2624250CE',
          exchange: 'NFO',
          data: { ltp: 105, bid_price: 100, ask_price: 110 },
        },
      ],
    ])

    const merged = mergeOptionChainMarketData(polledChain(), 'NFO', marketData, 0)
    const ce = merged.chain[0].ce
    expect(ce?.ltp).toBe(105)
    expect(ce?.bid).toBe(100)
    expect(ce?.ask).toBe(110)
  })
})
