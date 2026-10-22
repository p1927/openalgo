/**
 * Charge-request helpers only. The numbers come from the server (`/api/trade-charges` ->
 * `broker_charges.calculate`), the one calculator; there is no client-side copy to drift.
 */

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

export function chargeMarketForExchange(exchange: string): ChargeMarket {
  const ex = (exchange || '').toUpperCase()
  if (['NFO', 'BFO', 'NSE', 'BSE', 'NSE_INDEX', 'BSE_INDEX', 'MCX', 'CDS'].includes(ex)) {
    return 'IN'
  }
  return 'US'
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
