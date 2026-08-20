import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { Simulators } from './Simulators'

describe('Simulators sub-day horizon', () => {
  it('disables time advancement when the strategy is already at expiry', () => {
    render(
      <Simulators
        spotShiftPct={0}
        ivShiftPct={0}
        daysElapsed={0}
        maxDays={0}
        onSpotShiftChange={vi.fn()}
        onIvShiftChange={vi.fn()}
        onDaysElapsedChange={vi.fn()}
        onReset={vi.fn()}
      />
    )

    expect(screen.getByRole('slider', { name: 'Time forward' })).toBeDisabled()
  })

  it('formats very short expiries in minutes instead of rounding both ends to zero hours', () => {
    render(
      <Simulators
        spotShiftPct={0}
        ivShiftPct={0}
        daysElapsed={0}
        maxDays={2 / (24 * 60)}
        onSpotShiftChange={vi.fn()}
        onIvShiftChange={vi.fn()}
        onDaysElapsedChange={vi.fn()}
        onReset={vi.fn()}
      />
    )

    expect(screen.getByText('+2m')).toBeVisible()
    expect(screen.queryAllByText('+0h')).toHaveLength(0)
  })

  it('exposes remaining hours without offering an unreachable +1d value', () => {
    const onDaysElapsedChange = vi.fn()
    render(
      <Simulators
        spotShiftPct={0}
        ivShiftPct={0}
        daysElapsed={0}
        maxDays={0.25}
        onSpotShiftChange={vi.fn()}
        onIvShiftChange={vi.fn()}
        onDaysElapsedChange={onDaysElapsedChange}
        onReset={vi.fn()}
      />
    )

    expect(screen.getByText('Hours Forward')).toBeVisible()
    expect(screen.getByText('+6h')).toBeVisible()
    expect(screen.queryByText('+1d')).not.toBeInTheDocument()

    const timeSlider = screen.getAllByRole('slider')[2]
    expect(timeSlider).toHaveAttribute('max', '6')
    expect(timeSlider).toHaveAttribute('step', '1')
    fireEvent.change(timeSlider, { target: { value: '6' } })
    expect(onDaysElapsedChange).toHaveBeenCalledWith(0.25)
  })

  it.each([0.02, 0.23])('partitions a %s-day maximum into reachable sub-hour steps', (maxDays) => {
    const onDaysElapsedChange = vi.fn()
    render(
      <Simulators
        spotShiftPct={0}
        ivShiftPct={0}
        daysElapsed={0}
        maxDays={maxDays}
        onSpotShiftChange={vi.fn()}
        onIvShiftChange={vi.fn()}
        onDaysElapsedChange={onDaysElapsedChange}
        onReset={vi.fn()}
      />
    )

    const timeSlider = screen.getAllByRole('slider')[2]
    const partitions = Math.ceil(maxDays / (1 / 24))

    expect(timeSlider).toHaveAttribute('min', '0')
    expect(timeSlider).toHaveAttribute('max', partitions.toString())
    expect(timeSlider).toHaveAttribute('step', '1')

    fireEvent.change(timeSlider, { target: { value: partitions.toString() } })
    expect(onDaysElapsedChange).toHaveBeenLastCalledWith(maxDays)
  })

  it('preserves quarter-day steps for day-mode horizons', () => {
    const onDaysElapsedChange = vi.fn()
    render(
      <Simulators
        spotShiftPct={0}
        ivShiftPct={0}
        daysElapsed={0}
        maxDays={3.5}
        onSpotShiftChange={vi.fn()}
        onIvShiftChange={vi.fn()}
        onDaysElapsedChange={onDaysElapsedChange}
        onReset={vi.fn()}
      />
    )

    const timeSlider = screen.getAllByRole('slider')[2]
    expect(timeSlider).toHaveAttribute('step', '0.25')
    fireEvent.change(timeSlider, { target: { value: '3.5' } })
    expect(onDaysElapsedChange).toHaveBeenLastCalledWith(3.5)
  })
})

describe('Simulators compact variant', () => {
  const baseProps = {
    spotShiftPct: 0,
    ivShiftPct: 0,
    daysElapsed: 0,
    maxDays: 5,
    onSpotShiftChange: vi.fn(),
    onIvShiftChange: vi.fn(),
    onDaysElapsedChange: vi.fn(),
    onReset: vi.fn(),
  }

  it('renders the inline strip instead of the card chrome', () => {
    render(<Simulators {...baseProps} variant="compact" />)

    expect(screen.getByTestId('simulators-compact')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'What-If Simulator' })).not.toBeInTheDocument()
  })

  it('exposes all three sliders with the same accessible names as the card variant', () => {
    render(<Simulators {...baseProps} variant="compact" />)

    expect(screen.getByRole('slider', { name: 'Spot price shift' })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'Implied volatility shift' })).toBeInTheDocument()
    expect(screen.getByRole('slider', { name: 'Time forward' })).toBeInTheDocument()
  })

  it('still wires through to the onChange callbacks and onReset', () => {
    const onSpotShiftChange = vi.fn()
    const onIvShiftChange = vi.fn()
    const onReset = vi.fn()
    render(
      <Simulators
        {...baseProps}
        variant="compact"
        spotShiftPct={2.5}
        ivShiftPct={10}
        onSpotShiftChange={onSpotShiftChange}
        onIvShiftChange={onIvShiftChange}
        onReset={onReset}
      />
    )

    fireEvent.change(screen.getByRole('slider', { name: 'Spot price shift' }), {
      target: { value: '3' },
    })
    expect(onSpotShiftChange).toHaveBeenCalledWith(3)

    fireEvent.change(screen.getByRole('slider', { name: 'Implied volatility shift' }), {
      target: { value: '-20' },
    })
    expect(onIvShiftChange).toHaveBeenCalledWith(-20)

    fireEvent.click(screen.getByRole('button', { name: 'Reset what-if simulators' }))
    expect(onReset).toHaveBeenCalledTimes(1)
  })
})
