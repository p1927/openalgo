import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { StrategyLeg } from '@/lib/strategyMath'
import { makeFormatCurrency } from '@/lib/utils'
import { PnLTab } from './PnLTab'

const mocks = vi.hoisted(() => ({ useMarketData: vi.fn() }))

vi.mock('@/hooks/useMarketData', () => ({ useMarketData: mocks.useMarketData }))

function activeLeg(id: string, side: 'BUY' | 'SELL', price: number): StrategyLeg {
  return {
    id,
    segment: 'OPTION',
    side,
    lots: 1,
    lotSize: 25,
    expiry: '13AUG26',
    strike: 24_600,
    optionType: 'CE',
    price,
    iv: 12,
    active: true,
    symbol: `NIFTY13AUG2624600${id.toUpperCase()}`,
  }
}

function closedAtZero(): StrategyLeg {
  return {
    id: 'closed-zero',
    segment: 'OPTION',
    side: 'BUY',
    lots: 1,
    lotSize: 25,
    expiry: '13AUG26',
    strike: 24_600,
    optionType: 'CE',
    price: 100,
    iv: 12,
    active: true,
    symbol: 'NIFTY13AUG2624600CE',
    exitPrice: 0,
  }
}

describe('PnLTab closed-leg boundary', () => {
  it('shows zero exit as closed realised P&L without a live subscription', () => {
    mocks.useMarketData.mockReturnValue({
      data: new Map(),
      isConnected: false,
      isPaused: false,
      isFallbackMode: false,
    })

    render(
      <PnLTab
        legs={[closedAtZero()]}
        fnoExchange="NFO"
        fallbackPrices={{}}
        formatCurrency={makeFormatCurrency(null)}
      />
    )

    expect(screen.getByText('0 open · 1 closed')).toBeVisible()
    expect(screen.getByText('Closed')).toBeVisible()
    expect(screen.getByText('₹0.00')).toBeVisible()
    expect(screen.getAllByText('-₹2,500.00')).toHaveLength(2)
    expect(mocks.useMarketData).toHaveBeenCalledWith(
      expect.objectContaining({ symbols: [], enabled: false })
    )
  })

  it('uses the injected formatter for prices and P&L', () => {
    mocks.useMarketData.mockReturnValue({
      data: new Map(),
      isConnected: false,
      isPaused: false,
      isFallbackMode: false,
    })

    render(
      <PnLTab
        legs={[closedAtZero()]}
        fnoExchange="NFO"
        fallbackPrices={{}}
        formatCurrency={makeFormatCurrency('deltaexchange')}
      />
    )

    expect(screen.getByText('$0.00')).toBeVisible()
    expect(screen.getAllByText('-$2,500.00')).toHaveLength(2)
    expect(screen.getByRole('table')).not.toHaveTextContent('₹')
  })
})

describe('PnLTab per-leg Net P&L after charges', () => {
  it('renders a per-leg net P&L cell when perLegChargesMap is supplied', () => {
    mocks.useMarketData.mockReturnValue({
      data: new Map(),
      isConnected: false,
      isPaused: false,
      isFallbackMode: false,
    })

    render(
      <PnLTab
        legs={[activeLeg('legA', 'BUY', 100), activeLeg('legB', 'SELL', 120)]}
        fnoExchange="NFO"
        fallbackPrices={{ legA: 110, legB: 130 }}
        formatCurrency={makeFormatCurrency(null)}
        perLegChargesMap={{ legA: 25, legB: 30 }}
      />
    )

    // Two rendered rows each get a pnl-net cell.
    expect(screen.getAllByTestId('pnl-net').length).toBe(2)
    expect(screen.getAllByTestId('pnl-net-row').length).toBe(2)
    // Per-leg charges for legA=25, legB=30 => 55 total. The dedicated
    // footer row surfaces that "Less" figure.
    expect(screen.getByTestId('pnl-net-footer-charges')).toHaveTextContent('55')
    // Net total for legA (BUY 100→110 = +250 − 25 = +225),
    //            legB (SELL 120→130 = −250 − 30 = −280)
    //                  totals to −55.00
    const footerTotal = screen.getByTestId('pnl-net-footer-total')
    expect(footerTotal).toHaveTextContent('-₹55.00')
  })

  it('renders an em-dash placeholder when no perLegChargesMap is given', () => {
    mocks.useMarketData.mockReturnValue({
      data: new Map(),
      isConnected: false,
      isPaused: false,
      isFallbackMode: false,
    })

    render(
      <PnLTab
        legs={[activeLeg('legA', 'BUY', 100)]}
        fnoExchange="NFO"
        fallbackPrices={{}}
        formatCurrency={makeFormatCurrency(null)}
      />
    )

    expect(screen.queryByTestId('pnl-net')).toBeNull()
    expect(screen.queryByTestId('pnl-net-row')).toBeNull()
    expect(screen.queryByTestId('pnl-net-footer-total')).toBeNull()
  })
})

