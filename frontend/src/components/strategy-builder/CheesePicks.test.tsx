import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { CheeseCombo, CheeseLeg, CheesePayload } from '@/lib/cheeseRanking'
import { CheesePicks } from './CheesePicks'

const leg = (
  strike: number,
  option_type: 'CE' | 'PE',
  over: Partial<CheeseLeg> = {}
): CheeseLeg => ({
  strike,
  option_type,
  side_hint: 'BUY',
  ltp: 10,
  iv: 18,
  p_itm: 0.42,
  deal_score: 0.61,
  breakeven: 24_650,
  mispricing_score: -0.7,
  rationale: ['Smile-fit residual below neighbours'],
  ...over,
})

const combo: CheeseCombo = {
  name: 'debit_spread_ce_24500',
  archetype: 'debit_spread',
  rationale: 'Buy cheap CE, sell protection.',
  legs: [
    { side: 'BUY', option_type: 'CE', strike: 24_500 },
    { side: 'SELL', option_type: 'CE', strike: 24_700 },
  ],
  metrics: {
    pop: 0.48,
    max_profit: 1200,
    max_loss: -800,
    net_debit_credit: -800,
    round_trip_charges: 90,
    breakevens: [24_580],
  },
}

const okPayload: CheesePayload = {
  status: 'ok',
  skip_reason: null,
  directional_view: { trend_score: 0.7, rsi14: 62, atm_iv_pct: 18, iv_regime: 'moderate' },
  buys: [leg(24_500, 'CE')],
  sells: [],
  combos: [combo],
}

const noop = () => undefined
const fmt = (v: number) => `Rs ${v}`

describe('CheesePicks', () => {
  it('renders nothing while there is no request', () => {
    const { container } = render(
      <CheesePicks state={{ phase: 'idle' }} onPick={noop} onAddCombo={noop} formatCurrency={fmt} />
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('lists picks and combos, and a click hands the leg / combo to the caller', () => {
    const onPick = vi.fn()
    const onAddCombo = vi.fn()
    render(
      <CheesePicks
        state={{ phase: 'ready', cheese: okPayload }}
        onPick={onPick}
        onAddCombo={onAddCombo}
        formatCurrency={fmt}
      />
    )
    expect(screen.getByText('Nothing fits the trend.')).toBeInTheDocument()
    fireEvent.click(screen.getByText('BUY 24500 CE'))
    expect(onPick).toHaveBeenCalledWith(24_500, 'CE', 'BUY')
    expect(screen.getByText(/P\(ITM\) 42%/)).toBeInTheDocument()
    expect(screen.getByText(/Max loss Rs -800/)).toBeInTheDocument()
    fireEvent.click(screen.getByText('Add combo'))
    expect(onAddCombo).toHaveBeenCalledWith(combo)
  })

  it('says why a ranking was skipped instead of showing empty lists', () => {
    render(
      <CheesePicks
        state={{
          phase: 'ready',
          cheese: { ...okPayload, status: 'skipped', skip_reason: 'closes_unavailable', buys: [] },
        }}
        onPick={noop}
        onAddCombo={noop}
        formatCurrency={fmt}
      />
    )
    expect(screen.getByText(/Not enough daily price history/)).toBeInTheDocument()
    expect(screen.queryByText('Cheap to buy')).not.toBeInTheDocument()
  })

  it('shows a backend failure, and keeps the previous picks visible while updating', () => {
    const { rerender } = render(
      <CheesePicks
        state={{ phase: 'error', message: 'stock_simulator get_index_history unavailable' }}
        onPick={noop}
        onAddCombo={noop}
        formatCurrency={fmt}
      />
    )
    expect(screen.getByRole('alert')).toHaveTextContent('unavailable')

    rerender(
      <CheesePicks
        state={{ phase: 'loading', previous: okPayload }}
        onPick={noop}
        onAddCombo={noop}
        formatCurrency={fmt}
      />
    )
    expect(screen.getByText('Updating...')).toBeInTheDocument()
    expect(screen.getByText('BUY 24500 CE')).toBeInTheDocument()
  })
})
