import { render, screen, within } from '@testing-library/react'
import type * as PlotlyTypes from 'plotly.js'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { computePayoff, type ScenarioState, type StrategyLeg } from '@/lib/strategyMath'
import { makeFormatCurrency } from '@/lib/utils'
import { PayoffChart } from './PayoffChart'

const plotCapture = vi.hoisted(() => ({
  props: null as {
    data: PlotlyTypes.Data[]
    layout: Partial<PlotlyTypes.Layout>
  } | null,
}))

vi.mock('@/lib/Plot2D', () => ({
  default: (props: { data: PlotlyTypes.Data[]; layout: Partial<PlotlyTypes.Layout> }) => {
    plotCapture.props = props
    return <div data-testid="payoff-plot" />
  },
}))

const NOW = new Date('2026-07-28T10:00:00.000Z')
const BASE_SCENARIO: ScenarioState = {
  spot: 100,
  iv: 20,
  daysElapsed: 0,
  valuationTime: NOW,
}
const formatCurrency = makeFormatCurrency(null)

function leg(
  id: string,
  side: 'BUY' | 'SELL',
  optionType: 'CE' | 'PE',
  strike: number,
  price: number
): StrategyLeg {
  return {
    id,
    segment: 'OPTION',
    side,
    lots: 1,
    lotSize: 1,
    expiry: '04AUG26',
    strike,
    optionType,
    price,
    iv: 20,
    active: true,
    symbol: id,
  }
}

describe('PayoffChart exact geometry', () => {
  beforeEach(() => {
    plotCapture.props = null
  })

  it('PG-08 joins profit and loss fills at the exact breakevens', () => {
    const payoff = computePayoff(
      [
        leg('lp', 'BUY', 'PE', 90, 0.5),
        leg('sp', 'SELL', 'PE', 95, 2),
        leg('sc', 'SELL', 'CE', 105, 2),
        leg('lc', 'BUY', 'CE', 110, 0.5),
      ],
      100,
      7,
      0,
      [90, 110],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Iron Condor"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const traces = plotCapture.props?.data ?? []
    const profit = traces.find((trace) => trace.name === 'Profit zone')
    const loss = traces.find((trace) => trace.name === 'Loss zone')
    const expiry = traces.find((trace) => trace.name === 'At Expiry')
    expect(profit).toBeDefined()
    expect(loss).toBeDefined()
    expect(expiry).toBeDefined()
    const expiryXs = expiry?.x as number[]
    const profitYs = profit?.y as number[]
    const lossYs = loss?.y as number[]

    expect(expiryXs).toEqual(expect.arrayContaining([90, 92, 95, 105, 108, 110]))
    for (const root of [92, 108]) {
      const index = expiryXs.indexOf(root)
      expect(index).toBeGreaterThanOrEqual(0)
      expect(profitYs[index]).toBe(0)
      expect(lossYs[index]).toBe(0)
    }
  })

  it('PG-06 pins the x-axis to the curve domain containing every sigma marker', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [40, 160],
      12,
      0,
      30,
      NOW
    )

    render(
      <PayoffChart
        title="Long Call"
        scenario={{ ...BASE_SCENARIO, iv: 30 }}
        remainingYears={1}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    expect(plotCapture.props?.layout.xaxis?.range).toEqual([40, 160])
    const sigmaShapes = plotCapture.props?.layout.shapes?.filter(
      (shape) => shape.xref === 'x' && typeof shape.x0 === 'number'
    )
    expect(sigmaShapes?.every((shape) => Number(shape.x0) >= 40 && Number(shape.x0) <= 160)).toBe(
      true
    )
  })

  it('PG-06 omits physically invalid negative sigma markers', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [0, 400],
      12,
      0,
      100,
      NOW
    )

    render(
      <PayoffChart
        title="High IV Call"
        scenario={{ ...BASE_SCENARIO, iv: 100 }}
        remainingYears={1}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const xShapes = plotCapture.props?.layout.shapes?.filter((shape) => shape.xref === 'x') ?? []
    const xAnnotations =
      plotCapture.props?.layout.annotations?.filter((annotation) => annotation.xref === 'x') ?? []

    expect(
      xShapes.every(
        (shape) =>
          (typeof shape.x0 !== 'number' || shape.x0 >= 0) &&
          (typeof shape.x1 !== 'number' || shape.x1 >= 0)
      )
    ).toBe(true)
    expect(
      xAnnotations.every((annotation) => typeof annotation.x !== 'number' || annotation.x >= 0)
    ).toBe(true)
  })

  it('uses the shifted scenario for its marker and hand-derived lognormal bands', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      110,
      7,
      0.25,
      [70, 160],
      12,
      0,
      30,
      NOW
    )

    render(
      <PayoffChart
        title="Shifted Call"
        chartIdentity="NFO:NIFTY:04AUG26"
        scenario={{ ...BASE_SCENARIO, spot: 110, iv: 30, daysElapsed: 0.25 }}
        remainingYears={0.25}
        terminalLabel="At First Expiry"
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const layout = plotCapture.props?.layout
    expect(layout?.uirevision).toBe('NFO:NIFTY:04AUG26')
    expect(
      layout?.shapes?.some(
        (shape) =>
          shape.type === 'line' && shape.xref === 'x' && shape.x0 === 110 && shape.x1 === 110
      )
    ).toBe(true)

    const bands = layout?.shapes?.filter((shape) => shape.type === 'rect') ?? []
    expect(bands.some((shape) => Math.abs(Number(shape.x0) - 80.5783792325) < 1e-4)).toBe(true)
    expect(bands.some((shape) => Math.abs(Number(shape.x0) - 93.6187202159) < 1e-4)).toBe(true)
  })

  it('keeps zoom for live updates but resets it when the strategy identity changes', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      12,
      0,
      20,
      NOW
    )
    const view = render(
      <PayoffChart
        title="Long Call"
        chartIdentity="NFO:NIFTY:04AUG26"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    expect(plotCapture.props?.layout.uirevision).toBe('NFO:NIFTY:04AUG26')
    view.rerender(
      <PayoffChart
        title="Long Call"
        chartIdentity="NFO:NIFTY:04AUG26"
        scenario={{ ...BASE_SCENARIO, spot: 101 }}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )
    expect(plotCapture.props?.layout.uirevision).toBe('NFO:NIFTY:04AUG26')

    view.rerender(
      <PayoffChart
        title="Long Call"
        chartIdentity="NFO:NIFTY:11AUG26"
        scenario={{ ...BASE_SCENARIO, spot: 101 }}
        remainingYears={14 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )
    expect(plotCapture.props?.layout.uirevision).toBe('NFO:NIFTY:11AUG26')
  })

  it('labels the selected horizon and gives both curves the same precise hover fields', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0.25,
      [80, 120],
      12,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Calendar"
        scenario={{ ...BASE_SCENARIO, daysElapsed: 0.25 }}
        remainingYears={6.75 / 365}
        terminalLabel="At First Expiry"
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const curves = (plotCapture.props?.data ?? []).filter(
      (trace) => trace.name === 'At First Expiry' || trace.name === 'T+6h'
    )
    expect(curves).toHaveLength(2)
    for (const curve of curves) {
      expect(curve.hovertemplate).toContain('Underlying: %{customdata[0]}')
      expect(curve.hovertemplate).toContain('Chg. from Scenario: %{customdata[1]}')
      expect(curve.hovertemplate).toContain('P&L: %{customdata[2]}')
    }
  })

  it('formats Delta Exchange hover values in USD without a rupee chart label', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      12,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="USD Call"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={makeFormatCurrency('deltaexchange')}
      />
    )

    const expiry = plotCapture.props?.data.find((trace) => trace.name === 'At Expiry')
    const customdata = expiry?.customdata as unknown as string[][]
    expect(customdata[0][0]).toBe('$80.00')
    expect(customdata[0][2]).toMatch(/^[-$]/)
    expect(plotCapture.props?.layout.yaxis?.title?.text).toBe('Profit / Loss')
    expect(expiry?.hovertemplate).not.toContain('₹')
  })

  it('SB-18 supplements the visual plot with a named summary and representative payoff table', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      12,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Long Call"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const region = screen.getByRole('region', { name: 'Long Call payoff analysis' })
    expect(within(region).getByText(/scenario spot/i)).toHaveTextContent('₹100.00')
    expect(within(region).getByRole('status')).toHaveTextContent(/at expiry/i)
    const table = within(region).getByRole('table', { name: /representative payoff values/i })
    expect(within(table).getAllByRole('row').length).toBeGreaterThanOrEqual(4)
    expect(within(table).getAllByRole('row').length).toBeLessThan(10)
  })

  it('SB-18 bounds many breakevens and discloses both summary and table omissions', () => {
    const basePayoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      12,
      0,
      20,
      NOW
    )
    const payoff = {
      ...basePayoff,
      breakevens: [82, 86, 90, 94, 98, 102, 106, 110, 114, 118],
    }

    render(
      <PayoffChart
        title="Many roots"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    const region = screen.getByRole('region', { name: 'Many roots payoff analysis' })
    const breakevenSummary = within(region).getByTestId('breakeven-summary')
    expect(breakevenSummary).toHaveTextContent('4 of 10 shown')
    expect((breakevenSummary.textContent ?? '').split(' (')[0].split(', ')).toHaveLength(4)

    const table = within(region).getByRole('table', { name: /representative payoff values/i })
    expect(within(table).getAllByRole('row')).toHaveLength(8)
    expect(within(region).getByTestId('representative-payoff-disclosure')).toHaveTextContent(
      '7 of 13 representative points shown'
    )
  })

  it('rewrites the hover template and y-axis title when per-leg charges are wired', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Charged"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        perLegCharges={{ call: 12.5 }}
      />
    )

    const expiry = (plotCapture.props?.data ?? []).find((t) => t.name === 'At Expiry') as
      | (PlotlyTypes.Data & { hovertemplate?: string })
      | undefined
    expect(expiry?.hovertemplate).toContain('After charges')
    expect(expiry?.hovertemplate).toContain('%{customdata[3]}')
    expect(plotCapture.props?.layout.yaxis?.title?.text).toBe('Net P&L (after charges)')

    // Cleanup so a follow-up test renders clean
    plotCapture.props = null
  })

  it('keeps the y-axis title as Profit / Loss when no charges are supplied', () => {
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="No charges"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )

    expect(plotCapture.props?.layout.yaxis?.title?.text).toBe('Profit / Loss')
  })

  it('renders a dashed strike line per leg, with no draggable handle and no slider', () => {
    // Strike adjustment now lives entirely in StrikeSliderRail, rendered
    // separately below this chart — PayoffChart itself is read-only, so it
    // must not render any circle "handle" shape, any Plotly editable-shape
    // wiring, or its own <input type="range">.
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Straddle"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 100, 2)]}
      />
    )

    const shapes = plotCapture.props?.layout.shapes ?? []
    const strikeLine = shapes.find(
      (s: any) => s.type === 'line' && s.xref === 'x' && s.x0 === 100 && s.line?.dash === 'dash'
    )
    const handleDot = shapes.find((s: any) => s.type === 'circle')
    expect(strikeLine).toBeDefined()
    expect(handleDot).toBeUndefined()
    expect((plotCapture.props?.layout as any).editable).toBeFalsy()

    expect(screen.queryByRole('slider')).toBeNull()
  })

  it('always uses dragmode "zoom" — the chart is never pan-locked', () => {
    // Strike dragging used to force dragmode:'pan' (with an on-chart
    // handle) whenever onStrikeChange was supplied, which made the entire
    // viewport pannable on any click, not just the handle. Now that
    // interaction lives in StrikeSliderRail instead, the chart has no
    // reason to ever leave the default box-zoom behavior.
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Read-only"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 100, 2)]}
      />
    )

    expect(plotCapture.props?.layout.dragmode).toBe('zoom')
  })

  it('renders one annotation per strike leg with the side (B/S), strike and option type', () => {
    // Each strike line should have a matching annotation that calls out
    // which leg it belongs to — without this the chart shows coloured
    // vertical lines but the user can't tell which leg is which.
    const payoff = computePayoff(
      [leg('lc', 'BUY', 'CE', 100, 5), leg('sp', 'SELL', 'PE', 110, 8)],
      105,
      7,
      0,
      [80, 140],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Annotated"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[
          leg('lc', 'BUY', 'CE', 100, 5),
          leg('sp', 'SELL', 'PE', 110, 8),
        ]}
      />
    )

    const annotations = plotCapture.props?.layout.annotations ?? []
    const ceAnnotations = annotations.filter(
      (a: any) => a.xref === 'x' && a.x === 100 && String(a.text).includes('CE')
    )
    const peAnnotations = annotations.filter(
      (a: any) => a.xref === 'x' && a.x === 110 && String(a.text).includes('PE')
    )
    expect(ceAnnotations.length).toBeGreaterThan(0)
    expect(peAnnotations.length).toBeGreaterThan(0)
    // Side mark (B or S) must be present in the text body so the user
    // can distinguish a long call from a short put at the same strike.
    const ceText = String(ceAnnotations[0].text)
    expect(ceText).toContain('<b>B</b>')
    const peText = String(peAnnotations[0].text)
    expect(peText).toContain('<b>S</b>')
  })

  it('renders a drag-any-strike-line callout only when strikes are editable', () => {
    // With onStrikeChange + at least one strike leg, the chart shows a
    // one-line "Drag any strike line" hint so the user knows strikes are
    // interactive. Without onStrikeChange the hint is hidden, and without
    // any legs there is nothing to drag.
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    const { rerender, queryByText } = render(
      <PayoffChart
        title="With hint"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 100, 2)]}
        strikeStep={50}
        onStrikeChange={() => {}}
      />
    )

    expect(queryByText(/drag any strike line/i)).toBeTruthy()

    rerender(
      <PayoffChart
        title="Read-only"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 100, 2)]}
        strikeStep={50}
      />
    )

    expect(queryByText(/drag any strike line/i)).toBeNull()
  })

  it('sizes the drag handle as a percentage of the visible axis width, not a fixed price amount', () => {
    // A fixed data-space handle width (e.g. always ±9 rupees) renders as a
    // comfortable target on a low-priced instrument but shrinks to a
    // couple of pixels once the axis spans thousands of rupees (NIFTY/
    // BANKNIFTY scale) over the same chart width. The handle must instead
    // track the *visible axis width* so its rendered pixel size — and thus
    // how easy it is to grab and drag — stays roughly constant regardless
    // of the underlying's price scale.
    const lowScenario: ScenarioState = { ...BASE_SCENARIO, spot: 100 }
    const lowPayoff = computePayoff(
      [leg('call', 'BUY', 'CE', 100, 2)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )
    const { unmount } = render(
      <PayoffChart
        title="Low price"
        scenario={lowScenario}
        remainingYears={7 / 365}
        payoff={lowPayoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 100, 2)]}
        strikeStep={5}
        onStrikeChange={() => {}}
      />
    )
    const lowShapes = plotCapture.props?.layout.shapes ?? []
    const lowRange = plotCapture.props?.layout.xaxis?.range as [number, number]
    const lowHandle = lowShapes.find(
      (s: any) => s.type === 'circle' && typeof s.name === 'string' && s.name.startsWith('handle:')
    ) as any
    expect(lowHandle).toBeDefined()
    const lowWidth = lowHandle.x1 - lowHandle.x0
    const lowDomain = lowRange[1] - lowRange[0]
    unmount()

    const highScenario: ScenarioState = { ...BASE_SCENARIO, spot: 24000 }
    const highPayoff = computePayoff(
      [leg('call', 'BUY', 'CE', 24000, 200)],
      24000,
      7,
      0,
      [21600, 26400],
      7,
      0,
      20,
      NOW
    )
    render(
      <PayoffChart
        title="High price"
        scenario={highScenario}
        remainingYears={7 / 365}
        payoff={highPayoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 24000, 200)]}
        strikeStep={50}
        onStrikeChange={() => {}}
      />
    )
    const highShapes = plotCapture.props?.layout.shapes ?? []
    const highRange = plotCapture.props?.layout.xaxis?.range as [number, number]
    const highHandle = highShapes.find(
      (s: any) => s.type === 'circle' && typeof s.name === 'string' && s.name.startsWith('handle:')
    ) as any
    expect(highHandle).toBeDefined()
    const highWidth = highHandle.x1 - highHandle.x0
    const highDomain = highRange[1] - highRange[0]

    // Both scales should reserve the same fraction of the visible axis
    // for the handle, even though the raw rupee widths differ by ~240x.
    expect(lowWidth / lowDomain).toBeCloseTo(highWidth / highDomain, 6)
  })

  it('offsets strike lines/handles apart when two legs share the same strike, and resolves drags back to the true strike', () => {
    // A straddle (long CE + long PE, both struck at 100) used to render its
    // two strike lines and handles exactly on top of each other — only the
    // topmost shape in Plotly's z-order could actually be dragged. Both
    // legs must now get distinct x-positions for their line+handle pair.
    const payoff = computePayoff(
      [leg('c', 'BUY', 'CE', 100, 5), leg('p', 'BUY', 'PE', 100, 5)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Straddle"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('c', 'BUY', 'CE', 100, 5), leg('p', 'BUY', 'PE', 100, 5)]}
        strikeStep={5}
        onStrikeChange={() => {}}
      />
    )

    const shapes = (plotCapture.props?.layout.shapes ?? []) as any[]
    const callLine = shapes.find((s) => s.name === 'strike:c')
    const putLine = shapes.find((s) => s.name === 'strike:p')
    const callHandle = shapes.find((s) => s.name === 'handle:c')
    const putHandle = shapes.find((s) => s.name === 'handle:p')
    expect(callLine).toBeDefined()
    expect(putLine).toBeDefined()
    expect(callHandle).toBeDefined()
    expect(putHandle).toBeDefined()

    // Both legs' true strike is 100, but their rendered x-positions must
    // differ so neither shape's hit region fully swallows the other's.
    expect(callLine.x0).not.toBe(putLine.x0)
    expect(callHandle.x0).not.toBe(putHandle.x0)
    // The offset is small and symmetric around the true strike.
    expect((callLine.x0 + putLine.x0) / 2).toBeCloseTo(100, 6)

    // Per-leg annotations should also separate onto distinct positions.
    const annotations = (plotCapture.props?.layout.annotations ?? []) as any[]
    const callAnnotation = annotations.find((a) => a.xref === 'x' && String(a.text).includes('CE'))
    const putAnnotation = annotations.find((a) => a.xref === 'x' && String(a.text).includes('PE'))
    expect(callAnnotation.x).not.toBe(putAnnotation.x)
  })

  it('resolves a dragged strike-line offset back to the true (non-offset) strike on relayout', () => {
    const payoff = computePayoff(
      [leg('c', 'BUY', 'CE', 100, 5), leg('p', 'BUY', 'PE', 100, 5)],
      100,
      7,
      0,
      [80, 120],
      7,
      0,
      20,
      NOW
    )
    const onStrikeChange = vi.fn()

    render(
      <PayoffChart
        title="Straddle"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('c', 'BUY', 'CE', 100, 5), leg('p', 'BUY', 'PE', 100, 5)]}
        strikeStep={5}
        onStrikeChange={onStrikeChange}
      />
    )

    const onRelayout = (plotCapture.props as any)?.onRelayout as (gd: unknown) => void
    const shapes = (plotCapture.props?.layout.shapes ?? []) as any[]
    const callLine = shapes.find((s) => s.name === 'strike:c')
    // Simulate dragging the call's rendered (offset) line 10 rupees to the
    // right of wherever it was rendered.
    const draggedX = callLine.x0 + 10
    onRelayout({
      layout: {
        shapes: [{ ...callLine, x0: draggedX, x1: draggedX }],
      },
    })

    expect(onStrikeChange).toHaveBeenCalledTimes(1)
    // The emitted strike must be relative to the true strike (100), not the
    // rendered/offset position — snapped to the 5-rupee step.
    const [legId, newStrike] = onStrikeChange.mock.calls[0]
    expect(legId).toBe('c')
    expect(newStrike).toBe(110)
  })

  it('keeps the spot price at the horizontal midpoint of the axis even when strikes only extend one side', () => {
    // A single call struck well above spot with nothing on the downside
    // used to leave the sampled domain (and thus the axis) skewed right,
    // putting the spot line off-center — unlike Groww's payoff chart,
    // which always keeps spot in the visual middle.
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', 150, 2)],
      100,
      7,
      0,
      [90, 200],
      7,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Skewed strikes"
        scenario={BASE_SCENARIO}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', 150, 2)]}
        strikeStep={5}
        onStrikeChange={() => {}}
      />
    )

    const range = plotCapture.props?.layout.xaxis?.range as [number, number]
    expect(range[1] - 100).toBeCloseTo(100 - range[0], 6)
  })
})
