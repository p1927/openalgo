import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { tradingApi } from '@/api/trading'
import type { StrategyLeg } from '@/lib/strategyMath'
import { showToast } from '@/utils/toast'
import { ExecutePlanWizard } from './ExecutePlanWizard'

vi.mock('@/api/client', () => ({
  apiClient: {
    post: vi.fn().mockResolvedValue({ data: { data: { total_margin_required: 1000 } } }),
  },
}))
vi.mock('@/api/trading', async () => {
  const actual = await vi.importActual<typeof import('@/api/trading')>('@/api/trading')
  return { ...actual, tradingApi: { ...actual.tradingApi, placeBasketOrder: vi.fn() } }
})
vi.mock('@/utils/toast', () => ({ showToast: { error: vi.fn(), success: vi.fn() } }))

const basket = vi.mocked(tradingApi.placeBasketOrder)

function leg(o: Partial<StrategyLeg> = {}): StrategyLeg {
  return {
    id: 'a',
    segment: 'OPTION',
    side: 'BUY',
    lots: 1,
    lotSize: 25,
    expiry: '28AUG26',
    strike: 25000,
    optionType: 'CE',
    price: 10,
    iv: 12,
    active: true,
    symbol: 'A',
    exchange: 'NFO',
    tickSize: 0.05,
    contractValid: true,
    ...o,
  }
}

async function toConfirm(legs: StrategyLeg[]) {
  render(
    <ExecutePlanWizard
      open
      onOpenChange={vi.fn()}
      legs={legs}
      exchange="NFO"
      planName="iron_condor"
      implementationSteps={[]}
      apiKey="k"
    />
  )
  fireEvent.click(screen.getByRole('button', { name: /Next/ })) // preview -> margin
  await waitFor(() => expect(screen.getByRole('button', { name: /Next/ })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: /Next/ })) // margin -> confirm
  fireEvent.click(screen.getByRole('checkbox'))
}

describe('ExecutePlanWizard basket path', () => {
  beforeEach(() => {
    basket.mockReset()
    vi.mocked(showToast.error).mockReset()
    vi.mocked(showToast.success).mockReset()
  })

  it('blocks execution while an open leg has no resolved contract', async () => {
    await toConfirm([leg(), leg({ id: 'b', symbol: 'B', contractValid: false })])
    expect(screen.getByRole('button', { name: 'Place basket' })).toBeDisabled()
    expect(basket).not.toHaveBeenCalled()
  })

  it('reports a rejected leg as a failure, not as success', async () => {
    basket.mockResolvedValue({
      status: 'success',
      results: [
        { symbol: 'A', status: 'success', orderid: '1' },
        { symbol: 'B', status: 'error', message: 'rejected' },
      ],
    })
    await toConfirm([leg(), leg({ id: 'b', symbol: 'B', side: 'SELL' })])
    fireEvent.click(screen.getByRole('button', { name: 'Place basket' }))
    await waitFor(() => expect(showToast.error).toHaveBeenCalled())
    expect(vi.mocked(showToast.error).mock.calls[0][0]).toMatch(/Basket partial: 1 ok, 1 rejected/)
    expect(showToast.success).not.toHaveBeenCalled()
  })

  it('an empty per-leg result list is an error', async () => {
    basket.mockResolvedValue({ status: 'success', results: [] })
    await toConfirm([leg()])
    fireEvent.click(screen.getByRole('button', { name: 'Place basket' }))
    await waitFor(() => expect(showToast.error).toHaveBeenCalled())
    expect(showToast.success).not.toHaveBeenCalled()
  })

  it('all legs ok in analyzer mode is a success that names the mode', async () => {
    basket.mockResolvedValue({
      status: 'success',
      mode: 'analyze',
      results: [{ symbol: 'A', status: 'success' }],
    })
    await toConfirm([leg()])
    fireEvent.click(screen.getByRole('button', { name: 'Place basket' }))
    await waitFor(() => expect(showToast.success).toHaveBeenCalled())
    expect(vi.mocked(showToast.success).mock.calls[0][0]).toMatch(/analyzer/)
  })
})
