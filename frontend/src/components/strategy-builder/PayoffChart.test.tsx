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

// The x-axis is autoranged (see PayoffChart's xaxis.autorange comment) so
// its extent isn't in layout.xaxis.range anymore; it's exactly the extent
// of the (extendToAxis-widened) trace x-data instead. Any trace works since
// they all share the same `xs`.
function plottedXRange(): [number, number] {
  const xs = plotCapture.props?.data[0]?.x as number[]
  return [Math.min(...xs), Math.max(...xs)]
}

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

    expect(plottedXRange()).toEqual([40, 160])
    const sigmaShapes = plotCapture.props?.layout.shapes?.filter(
      (shape) => shape.xref === 'x' && typeof shape.x0 === 'number'
    )
    expect(sigmaShapes?.every((shape) => Number(shape.x0) >= 40 && Number(shape.x0) <= 160)).toBe(
      true
    )
  })

  it('caps the x-axis to NIFTY/SENSEX typical daily move when underlyingSymbol is recognized', () => {
    const spot = 24000
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', spot, 200)],
      spot,
      7,
      0,
      [10000, 38000],
      12,
      0,
      50,
      NOW
    )

    const uncapped = render(
      <PayoffChart
        title="Long Call"
        scenario={{ ...BASE_SCENARIO, spot, iv: 50 }}
        remainingYears={1}
        payoff={payoff}
        formatCurrency={formatCurrency}
      />
    )
    const uncappedRange = plottedXRange()
    expect(uncappedRange[1] - spot).toBeGreaterThan(800)
    uncapped.unmount()
    plotCapture.props = null

    render(
      <PayoffChart
        title="Long Call"
        scenario={{ ...BASE_SCENARIO, spot, iv: 50 }}
        remainingYears={1}
        payoff={payoff}
        formatCurrency={formatCurrency}
        underlyingSymbol="NIFTY"
      />
    )
    expect(plottedXRange()).toEqual([spot - 600, spot + 600])
    plotCapture.props = null

    render(
      <PayoffChart
        title="Sensex Call"
        scenario={{ ...BASE_SCENARIO, spot, iv: 50 }}
        remainingYears={1}
        payoff={payoff}
        formatCurrency={formatCurrency}
        underlyingSymbol="sensex"
      />
    )
    expect(plottedXRange()).toEqual([spot - 800, spot + 800])
  })

  it('never crops a real strike even when it lies beyond the underlying move cap', () => {
    const spot = 24000
    const farStrike = spot + 1500
    const payoff = computePayoff(
      [leg('call', 'BUY', 'CE', farStrike, 5)],
      spot,
      7,
      0,
      [22000, 26000],
      12,
      0,
      20,
      NOW
    )

    render(
      <PayoffChart
        title="Far strike"
        scenario={{ ...BASE_SCENARIO, spot }}
        remainingYears={7 / 365}
        payoff={payoff}
        formatCurrency={formatCurrency}
        legs={[leg('call', 'BUY', 'CE', farStrike, 5)]}
        underlyingSymbol="NIFTY"
      />
    )

    const range = plottedXRange()
    expect(range[1]).toBeGreaterThanOrEqual(farStrike)
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

  it('renders a strike line per leg (labelling now lives in StrikeSliderRail, not on the chart)', () => {
    // The chart used to draw a text annotation per strike leg calling out
    // side/strike/option type. That label row moved off the chart entirely
    // (into StrikeSliderRail, rendered below) so the plot itself stays
    // uncluttered — see the reference-image redesign. The vertical strike
    // *lines* still render; only their text annotations are gone.
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
        legs={[leg('lc', 'BUY', 'CE', 100, 5), leg('sp', 'SELL', 'PE', 110, 8)]}
      />
    )

    const shapes = plotCapture.props?.layout.shapes ?? []
    const ceLine = shapes.find(
      (s: any) => s.type === 'line' && s.xref === 'x' && s.x0 === 100 && s.line?.dash === 'dash'
    )
    const peLine = shapes.find(
      (s: any) => s.type === 'line' && s.xref === 'x' && s.x0 === 110 && s.line?.dash === 'dash'
    )
    expect(ceLine).toBeDefined()
    expect(peLine).toBeDefined()

    const annotations = plotCapture.props?.layout.annotations ?? []
    expect(
      annotations.some((a: any) => String(a.text).includes('CE') || String(a.text).includes('PE'))
    ).toBe(false)
  })

  it('offsets strike lines apart when two legs share the same strike', () => {
    // A straddle (long CE + long PE, both struck at 100) used to render its
    // two strike lines exactly on top of each other. Both legs must get
    // distinct x-positions so neither line fully hides the other.
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
      />
    )

    const shapes = (plotCapture.props?.layout.shapes ?? []) as any[]
    const lines = shapes.filter(
      (s) => s.type === 'line' && s.xref === 'x' && s.line?.dash === 'dash'
    )
    expect(lines.length).toBeGreaterThanOrEqual(2)
    const xPositions = lines.map((s) => s.x0)
    // Both legs' true strike is 100, but their rendered x-positions must
    // differ so neither line fully overlaps the other.
    expect(new Set(xPositions).size).toBeGreaterThanOrEqual(2)
    // The offset is small and symmetric around the true strike.
    expect((xPositions[0] + xPositions[1]) / 2).toBeCloseTo(100, 6)
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
      />
    )

    const range = plottedXRange()
    expect(range[1] - 100).toBeCloseTo(100 - range[0], 6)
  })
})
