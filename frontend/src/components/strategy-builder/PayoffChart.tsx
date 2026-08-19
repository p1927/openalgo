import type * as PlotlyTypes from 'plotly.js'
import type { ReactNode } from 'react'
import { useCallback, useId, useMemo, useRef, useState } from 'react'
import Plot from '@/lib/Plot2D'
import {
  lognormalPriceBand,
  type PayoffResult,
  type ScenarioState,
  type StrategyLeg,
} from '@/lib/strategyMath'
import { useThemeStore } from '@/stores/themeStore'

export interface PayoffChartProps {
  title: string
  /** Stable for live updates; changes when the selected strategy identity changes. */
  chartIdentity?: string
  scenario: ScenarioState
  remainingYears: number
  payoff: PayoffResult
  /** Label the terminal event explicitly for multi-expiry strategies. */
  terminalLabel?: string
  /** If true, show the dashed current-value scenario curve in addition to expiry. */
  showTplus0?: boolean
  height?: number
  formatCurrency: (value: number) => string
  legs?: StrategyLeg[]
  /**
   * The selected underlying's symbol (e.g. "NIFTY", "SENSEX"), used only to
   * cap the x-axis to that index's typical daily move — see
   * `MAX_AXIS_HALF_WIDTH_BY_UNDERLYING`. Unrecognized/omitted symbols (any
   * equity, or an index not in that table) fall back to the existing
   * strike/σ-driven axis sizing.
   */
  underlyingSymbol?: string
  /**
   * Per-leg round-trip charge totals (leg.id → total charges). When supplied,
   * the hover tooltip gains an "After charges" row and the y-axis title is
   * updated to "Net P&L (after charges)". The math is already netted upstream
   * into the `payoff` samples — this prop only changes how the chart presents
   * the "P&L after charges" number in the tooltip.
   */
  perLegCharges?: Record<string, number>
  /**
   * Rendered between the chart and the scenario/summary section below it —
   * used to place `StrikeSliderRail` right under the plot, ahead of the
   * scenario-spot text and the max profit/loss/breakeven cards, rather than
   * after all of that.
   */
  belowChart?: ReactNode
}

const MAX_REPRESENTATIVE_ROWS = 7
const MAX_SUMMARY_BREAKEVENS = 4

// Fixed layout margins (see `chartLayout.margin` below). `t` clears both the
// title (container coords, near the very top of the figure) and the
// spot-price label (axis "paper" coords, floating above the plot area) —
// give it enough room that the two never collide, now that the σ tick/label
// row and the per-leg strike text row have moved off the chart (strikes are
// labelled in StrikeSliderRail below instead — see the class doc comment).
const CHART_MARGIN = { l: 70, r: 30, t: 68, b: 50 }
// Anti-overlap offset step for strike lines/labels sharing (or nearly
// sharing) a strike — sized as a percentage of the visible axis width so it
// stays scale-invariant whether the underlying trades at 100 or 24,000.
const CLUSTER_OFFSET_PCT_OF_AXIS = 0.015

// Index-specific cap on the x-axis half-width (points either side of spot),
// set from the typical maximum daily move for that index rather than derived
// from IV/strikes — a stray far-OTM strike or a high-IV scenario would
// otherwise stretch the axis well past what these indices realistically move
// in a session, flattening the payoff curve into an unreadable sliver.
// A real strike beyond the cap is still never cropped (see the
// `rawStrikeHalfWidth` guard below) — this only tightens the *default* zoom.
const MAX_AXIS_HALF_WIDTH_BY_UNDERLYING: Record<string, number> = {
  NIFTY: 600,
  SENSEX: 800,
}

function formatHorizon(elapsedDays: number) {
  if (elapsedDays <= 0) return 'T+0'
  const totalHours = Math.round(elapsedDays * 24 * 10) / 10
  const wholeDays = Math.floor(totalHours / 24)
  const hours = Math.round((totalHours - wholeDays * 24) * 10) / 10
  if (wholeDays === 0) return `T+${hours.toLocaleString()}h`
  if (hours === 0) return `T+${wholeDays}d`
  return `T+${wholeDays}d ${hours.toLocaleString()}h`
}

function selectEvenly<T>(items: T[], limit: number): T[] {
  if (items.length <= limit) return items
  if (limit <= 0) return []
  if (limit === 1) return [items[Math.floor(items.length / 2)]]
  return Array.from({ length: limit }, (_, index) => {
    const sourceIndex = Math.round((index * (items.length - 1)) / (limit - 1))
    return items[sourceIndex]
  })
}

/**
 * Extends a piecewise-linear payoff series flat-out to the visible axis
 * edges by linearly extrapolating the outermost segment's slope. Needed
 * because the *visible* x-axis window is often wider than the *sampled*
 * data domain (see `axisLo`/`axisHi` further down, which re-center the
 * window on spot) — without this, the P&L line/fill simply stops short of
 * the axis edge, leaving a blank void. Expiry payoffs are exactly linear
 * beyond the outermost strike/breakeven that bounded the sample domain, so
 * this is exact for the expiry curve and a close approximation for the
 * curved T+0 curve over the (typically modest) extra margin.
 */
function extendToAxis(xs: number[], ys: number[], axisLo: number, axisHi: number) {
  if (xs.length < 2) return { xs, ys }
  const outXs = [...xs]
  const outYs = [...ys]
  if (axisLo < xs[0]) {
    const slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
    outXs.unshift(axisLo)
    outYs.unshift(ys[0] + slope * (axisLo - xs[0]))
  }
  const lastIdx = xs.length - 1
  if (axisHi > xs[lastIdx]) {
    const slope = (ys[lastIdx] - ys[lastIdx - 1]) / (xs[lastIdx] - xs[lastIdx - 1])
    outXs.push(axisHi)
    outYs.push(ys[lastIdx] + slope * (axisHi - xs[lastIdx]))
  }
  return { xs: outXs, ys: outYs }
}

/**
 * When two or more legs share (or nearly share) a strike, their lines and
 * labels would otherwise render exactly on top of each other. `offset`
 * nudges the leg's line apart from its strike-mates along the x-axis by a
 * small, scale-invariant amount so every leg in the cluster stays
 * independently visible.
 */
type StrikeOffsetInfo = { offset: number; groupSize: number; groupIndex: number }

/**
 * State for the custom crosshair overlay drawn on hover — a dashed vertical
 * line plus a floating price/P&L card, positioned with plain CSS instead of
 * Plotly's own hover box (which we hide via the `.hoverlayer` CSS override
 * below). Plotly still needs to be the thing that fires hover events (that's
 * what gives us the exact snapped underlying/P&L values from `customdata`) —
 * only the *rendering* of the hover box is replaced.
 */
interface CrosshairInfo {
  /** Cursor position, relative to the chart container. */
  xPixel: number
  yPixel: number
  underlying: number
  pnl: number
  pctText: string
}

/**
 * Read-only payoff diagram. Strike adjustment lives entirely in
 * `StrikeSliderRail` (rendered separately, below this chart) — this
 * component only draws the current legs' strike lines, it doesn't handle
 * any pointer interaction for them. That split exists because Plotly has no
 * built-in way to make shapes draggable without making the *entire chart*
 * pannable on every other click (a confirmed open limitation:
 * https://community.plotly.com/t/editable-mode-preventing-shape-and-annotation-dragging/83795),
 * and a from-scratch pointer-drag layer (an earlier version of this
 * component had one) is real interaction surface to maintain for a job a
 * plain HTML range slider does natively, accessibly, and for free.
 */
export function PayoffChart({
  title,
  chartIdentity = title,
  scenario,
  remainingYears,
  payoff,
  terminalLabel = 'At Expiry',
  showTplus0 = true,
  height = 440,
  formatCurrency,
  legs = [],
  perLegCharges,
  underlyingSymbol,
  belowChart,
}: PayoffChartProps) {
  const regionHeadingId = useId()
  const chartScopeId = useId().replace(/:/g, '')
  const chartContainerRef = useRef<HTMLDivElement>(null)
  const [crosshair, setCrosshair] = useState<CrosshairInfo | null>(null)
  const { mode, appMode } = useThemeStore()
  const isAnalyzer = appMode === 'analyzer'
  const isDark = mode === 'dark' || isAnalyzer

  const strikeLegs = useMemo(
    () =>
      legs.filter(
        (l) =>
          l.active &&
          l.segment === 'OPTION' &&
          l.strike !== undefined &&
          !(l.exitPrice && l.exitPrice > 0)
      ),
    [legs]
  )

  const colors = useMemo(
    () => ({
      paper: isDark ? (isAnalyzer ? '#171226' : '#0b1220') : '#ffffff',
      bg: isDark ? (isAnalyzer ? '#1f1936' : '#111827') : '#fafbfc',
      text: isDark ? '#e5e7eb' : '#0f172a',
      mutedText: isDark ? '#94a3b8' : '#64748b',
      grid: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(15,23,42,0.06)',
      profit: isDark ? 'rgba(16,185,129,0.20)' : 'rgba(16,185,129,0.14)', // Groww emerald #10b981
      loss: isDark ? 'rgba(244,63,94,0.20)' : 'rgba(244,63,94,0.14)', // Groww rose #f43f5e
      expiryLine: isDark ? '#10b981' : '#059669', // deeper emerald
      tplus0Line: isDark ? '#60a5fa' : '#2563eb', // slate blue
      zeroLine: isDark ? 'rgba(226,232,240,0.4)' : 'rgba(15,23,42,0.4)',
      spotLine: isDark ? '#ec4899' : '#db2777', // groww-ish pink
      ceStrike: isDark ? '#4ade80' : '#16a34a',
      peStrike: isDark ? '#f87171' : '#dc2626',
      sigmaTick: isDark ? 'rgba(148,163,184,0.30)' : 'rgba(15,23,42,0.18)',
      cardBorder: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(15,23,42,0.08)',
    }),
    [isDark, isAnalyzer]
  )

  const { data, layout, config } = useMemo(() => {
    const { spot, iv, daysElapsed } = scenario
    const { samples } = payoff
    if (samples.length === 0) {
      return {
        data: [] as PlotlyTypes.Data[],
        layout: {} as Partial<PlotlyTypes.Layout>,
        config: {},
      }
    }

    const rawXs = samples.map((s) => s.underlying)
    const rawYsExpiry = samples.map((s) => s.expiry)
    const rawYsT0 = samples.map((s) => s.tplus0)

    const b2 = lognormalPriceBand(spot, iv, remainingYears, 2)
    const domainLo = rawXs[0]
    const domainHi = rawXs[rawXs.length - 1]

    // The sampled data domain (domainLo/domainHi above) is whatever
    // payoffPriceRange/computePayoff needed to cover every strike and
    // breakeven. For an unbounded-risk leg (a naked short/long with no
    // offsetting strike) that breakeven search can run arbitrarily far from
    // spot — sometimes tens of thousands of rupees — because computePayoff
    // has no reason to stop closer in. Using that raw span as the *visible*
    // axis window squashes the strategy's actual strikes into a sliver of
    // the chart: labels collide, and the payoff's real (finite-slope) rise
    // near the strike renders as a near-vertical wall against a domain
    // that's 50-100x wider than the interesting region.
    //
    // The visible window is therefore sized independently of that raw
    // domain: wide enough to comfortably show every strike (15% padding
    // past the furthest one) and the statistically-likely 2σ price move,
    // but capped to a fixed percentage of spot so a single unbounded leg
    // can't blow the chart out — unlimited profit/loss is already
    // communicated as text via the Maximum profit/loss cards below the
    // chart, it doesn't need to be visually plotted out to where the line
    // goes flat. `Math.min` against the raw domain means a strategy whose
    // natural domain is already tighter than this window (the common case)
    // is left alone — this only kicks in for the wide-domain outlier.
    // 1.4x rather than a tighter pad keeps the strikes from sitting right at
    // the axis edges — with less padding the curve's slope near the strikes
    // reads as steep and the T+0/expiry lines look clubbed together instead
    // of legibly separated.
    const strikeHalfWidth =
      strikeLegs.length > 0
        ? Math.max(...strikeLegs.map((l) => Math.abs((l.strike ?? spot) - spot))) * 1.4
        : 0
    const sigmaHalfWidth = b2 ? Math.max(spot - b2.lower, b2.upper - spot) : 0
    const rawHalfWidth = Math.max(spot - domainLo, domainHi - spot)
    const MAX_AXIS_HALF_WIDTH_PCT = 0.55
    const cappedHalfWidth = Math.min(
      rawHalfWidth,
      Math.max(sigmaHalfWidth, strikeHalfWidth, spot * MAX_AXIS_HALF_WIDTH_PCT)
    )
    // Never let the cap crop a strike itself, however extreme the cap ends
    // up relative to it.
    let axisHalfWidth = Math.max(cappedHalfWidth, strikeHalfWidth)
    // Index-specific override: NIFTY/SENSEX rarely move beyond their typical
    // daily range, so default to that instead of letting IV/σ stretch the
    // axis out. A real strike further out than the cap still isn't cropped
    // (compared against the *unpadded* strike distance, not strikeHalfWidth's
    // 1.4x-padded version, so this only relaxes the cap exactly as far as it
    // needs to).
    const underlyingMaxMove =
      MAX_AXIS_HALF_WIDTH_BY_UNDERLYING[underlyingSymbol?.trim().toUpperCase() ?? '']
    if (underlyingMaxMove !== undefined) {
      const rawStrikeHalfWidth =
        strikeLegs.length > 0
          ? Math.max(...strikeLegs.map((l) => Math.abs((l.strike ?? spot) - spot)))
          : 0
      axisHalfWidth = Math.max(Math.min(axisHalfWidth, underlyingMaxMove), rawStrikeHalfWidth)
    }
    const axisLo = Math.max(0, spot - axisHalfWidth)
    const axisHi = spot + axisHalfWidth
    const axisWidth = axisHi - axisLo

    // Stretch the P&L line/fill out to the visible axis edges (see the
    // widening comment above — one side of the axis is often wider than the
    // sampled domain) instead of leaving a blank void past the last real
    // sample. `xs` below is shared by every y-series since the widening is
    // x-only and identical for all of them.
    const { xs, ys: ysExpiry } = extendToAxis(rawXs, rawYsExpiry, axisLo, axisHi)
    const { ys: ysT0 } = extendToAxis(rawXs, rawYsT0, axisLo, axisHi)
    const profitFill = ysExpiry.map((y) => (y >= 0 ? y : 0))
    const lossFill = ysExpiry.map((y) => (y < 0 ? y : 0))

    // Plotly's default y-autorange looks at every point in a trace, not just
    // the ones inside the visible xaxis window. `xs`/`ysExpiry` above still
    // carry the *full* sampled domain (only ever widened, never trimmed, by
    // extendToAxis) — for an unbounded-risk leg that domain can run tens of
    // thousands of rupees past the visible window (see the axisHalfWidth
    // comment above), so those far-off-screen extreme P&L values were
    // stretching the y-axis out to fit them, squashing the actually-visible
    // curve into a sliver near the middle. Computing the range explicitly
    // from only the on-screen points — the same treatment xaxis.range
    // already gets — makes the y-axis fit the graph that's actually drawn.
    let yLo = 0
    let yHi = 0
    for (let i = 0; i < xs.length; i++) {
      if (xs[i] < axisLo - 1e-6 || xs[i] > axisHi + 1e-6) continue
      if (ysExpiry[i] < yLo) yLo = ysExpiry[i]
      if (ysExpiry[i] > yHi) yHi = ysExpiry[i]
      if (ysT0[i] < yLo) yLo = ysT0[i]
      if (ysT0[i] > yHi) yHi = ysT0[i]
    }
    if (yLo === yHi) {
      yLo -= 1
      yHi += 1
    }
    const yPad = (yHi - yLo) * 0.12
    const yAxisRange: [number, number] = [yLo - yPad, yHi + yPad]

    const pctFromSpot = xs.map((x) => {
      const pct = ((x - spot) / spot) * 100
      const sign = pct >= 0 ? '+' : ''
      return `${sign}${pct.toFixed(2)}%`
    })

    const currentLabel = formatHorizon(daysElapsed)
    // When `perLegCharges` is supplied, the tooltip gains an extra row at
    // customdata[3] that surfaces the "After charges" P&L. The chart
    // contract is fixed even when no charges are present (customdata length
    // stays at 3 in that case so the hovertemplate keeps rendering).
    const chargesRow = perLegCharges ? '<br>After charges: %{customdata[3]}' : ''
    const hoverTemplate = (label: string) =>
      `<b>${label}</b>` +
      '<br>Underlying: %{customdata[0]}' +
      '<br>Chg. from Scenario: %{customdata[1]}' +
      '<br>P&L: %{customdata[2]}' +
      chargesRow +
      '<extra></extra>'
    const hoverData = (values: number[]) =>
      xs.map((x, index) => {
        const row: (string | number)[] = [
          formatCurrency(x),
          pctFromSpot[index],
          formatCurrency(values[index]),
        ]
        if (perLegCharges) {
          // ysExpiry is already net-of-charges because StrategyBuilder
          // threads perLegCharges into computePayoff() upstream of this
          // chart; the two axis-edge points added by extendToAxis carry
          // that same charges-netted value forward via linear extrapolation.
          row.push(formatCurrency(ysExpiry[index]))
        }
        return row
      })

    const traces: PlotlyTypes.Data[] = [
      {
        x: xs,
        y: profitFill,
        type: 'scatter',
        mode: 'none',
        fill: 'tozeroy',
        fillcolor: colors.profit,
        showlegend: false,
        hoverinfo: 'skip',
        name: 'Profit zone',
      },
      {
        x: xs,
        y: lossFill,
        type: 'scatter',
        mode: 'none',
        fill: 'tozeroy',
        fillcolor: colors.loss,
        showlegend: false,
        hoverinfo: 'skip',
        name: 'Loss zone',
      },
      {
        x: xs,
        y: ysExpiry,
        type: 'scatter',
        mode: 'lines',
        name: terminalLabel,
        line: { color: colors.expiryLine, width: 2.6 },
        // customdata carries broker-aware price/P&L strings and percent change.
        customdata: hoverData(ysExpiry) as unknown as PlotlyTypes.Datum[],
        hovertemplate: hoverTemplate(terminalLabel),
      },
    ]

    if (showTplus0) {
      traces.push({
        x: xs,
        y: ysT0,
        type: 'scatter',
        mode: 'lines',
        name: currentLabel,
        line: { color: colors.tplus0Line, width: 1.8, dash: 'dash' },
        customdata: hoverData(ysT0) as unknown as PlotlyTypes.Datum[],
        hovertemplate: hoverTemplate(currentLabel),
      })
    }

    const shapes: Partial<PlotlyTypes.Shape>[] = []

    // Cluster legs whose strikes fall within one offset-step of each other
    // (e.g. a straddle/strangle collapsed to one strike, or a calendar
    // spread) and assign each a small deterministic x-offset so their lines
    // don't stack exactly on top of one another. See `StrikeOffsetInfo`
    // above for why this exists.
    const strikeOffsetByLegId = new Map<string, StrikeOffsetInfo>()
    {
      const offsetStep = axisWidth * CLUSTER_OFFSET_PCT_OF_AXIS
      const collisionThreshold = offsetStep * 1.5
      const sortedByStrike = [...strikeLegs]
        .map((leg) => ({ leg, strike: leg.strike ?? spot }))
        .sort((a, b) => a.strike - b.strike)
      let clusterStart = 0
      for (let idx = 1; idx <= sortedByStrike.length; idx++) {
        const atEnd = idx === sortedByStrike.length
        const gapExceeded =
          !atEnd &&
          sortedByStrike[idx].strike - sortedByStrike[idx - 1].strike >= collisionThreshold
        if (atEnd || gapExceeded) {
          const cluster = sortedByStrike.slice(clusterStart, idx)
          if (cluster.length > 1) {
            const n = cluster.length
            cluster.forEach(({ leg }, i) => {
              strikeOffsetByLegId.set(leg.id, {
                offset: (i - (n - 1) / 2) * offsetStep,
                groupSize: n,
                groupIndex: i,
              })
            })
          }
          clusterStart = idx
        }
      }
    }

    // Vertical strike lines — always dashed now (previously solid for a
    // long leg, dashed for short; that distinction is redundant once B/S is
    // already spelled out in the label above each line) and extended a bit
    // below the plot area's own bottom edge, into the margin whitespace, so
    // they visually run toward the strike slider rail rendered just below
    // this chart rather than stopping abruptly at the axis.
    strikeLegs.forEach((leg, i) => {
      const strike = leg.strike ?? spot
      const renderX = strike + (strikeOffsetByLegId.get(leg.id)?.offset ?? 0)
      const isCe = leg.optionType === 'CE'
      const isSell = leg.side === 'SELL'
      const color = isCe ? colors.ceStrike : colors.peStrike
      shapes.push({
        type: 'line',
        xref: 'x',
        yref: 'paper',
        x0: renderX,
        x1: renderX,
        y0: -(CHART_MARGIN.b / height) * 0.9,
        y1: 1,
        line: {
          color,
          width: isSell ? 1.5 : 2,
          dash: 'dash',
        },
        opacity: 0.85,
        legendgroup: `strike-${i}`,
        layer: 'below',
      })
    })

    // Zero-line baseline (drawn under strikes so vertical strike marks
    // remain visually prominent).
    shapes.push({
      type: 'line',
      xref: 'paper',
      x0: 0,
      x1: 1,
      yref: 'y',
      y0: 0,
      y1: 0,
      line: { color: colors.zeroLine, width: 1 },
    })

    shapes.push({
      type: 'line',
      xref: 'x',
      x0: spot,
      x1: spot,
      yref: 'paper',
      y0: 0,
      y1: 1,
      line: { color: colors.spotLine, width: 1.5, dash: 'dot' },
    })

    const annotations: Partial<PlotlyTypes.Annotations>[] = []

    // Spot label — anchored at spot x, sitting just above the plot area in
    // axis "paper" coords. Kept close (`y: 1.05`) rather than pushed further
    // up, since the title above it lives in a separate coordinate system
    // (container coords, near the very top of the whole figure) — CHART_MARGIN.t
    // is sized to keep clearance between the two regardless. When a strike is
    // within ~3% of spot, push the spot label left/right of the strike to
    // avoid overlap; otherwise it sits directly above spot.
    const strikeXValues = strikeLegs.map((l) => l.strike ?? scenario.spot)
    const minStrikeDistance = strikeXValues.reduce((acc, x) => {
      const d = Math.abs(x - scenario.spot)
      return d > 0 && (acc === 0 || d < acc) ? d : acc
    }, 0)
    const spotXAnchor: 'left' | 'right' | 'center' =
      minStrikeDistance > 0 && minStrikeDistance < (xs[xs.length - 1] - xs[0]) * 0.05
        ? strikeXValues.some((x) => x > spot)
          ? 'left'
          : 'right'
        : 'center'
    annotations.push({
      x: spot,
      y: 1.05,
      xref: 'x',
      yref: 'paper',
      text: `<b>${spot.toFixed(2)}</b>`,
      showarrow: false,
      xanchor: spotXAnchor,
      yanchor: 'bottom',
      font: { size: 12, color: colors.spotLine },
    })

    annotations.push({
      x: 1,
      y: 0,
      xref: 'paper',
      yref: 'paper',
      text: 'openalgo.in',
      showarrow: false,
      xanchor: 'right',
      yanchor: 'top',
      yshift: -36,
      xshift: -6,
      font: { size: 10, color: colors.mutedText },
      opacity: 0.85,
    })

    const chartLayout: Partial<PlotlyTypes.Layout> = {
      uirevision: chartIdentity,
      dragmode: 'zoom',
      title: {
        text: title,
        font: { color: colors.text, size: 14 },
        y: 0.98,
        yanchor: 'top',
      },
      paper_bgcolor: colors.paper,
      plot_bgcolor: colors.bg,
      font: { color: colors.text, family: 'system-ui, sans-serif' },
      hovermode: 'x unified',
      hoverlabel: {
        bgcolor: isDark ? '#0f172a' : '#ffffff',
        font: { color: colors.text, size: 12 },
        bordercolor: colors.mutedText,
      },
      margin: CHART_MARGIN,
      showlegend: true,
      legend: {
        orientation: 'h',
        x: 0.5,
        xanchor: 'center',
        y: -0.18,
        font: { color: colors.text, size: 12 },
      },
      xaxis: {
        title: { text: 'Underlying Price', font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text, size: 10 },
        gridcolor: colors.grid,
        zeroline: false,
        range: [axisLo, axisHi],
        // Caps how many gridlines/labels Plotly packs in — without this it
        // can pick a small dtick that crowds ticks together on a narrow
        // strike spread, which is also what made the curves look clubbed up.
        nticks: 7,
      },
      yaxis: {
        title: {
          text: perLegCharges ? 'Net P&L (after charges)' : 'Profit / Loss',
          font: { color: colors.text, size: 12 },
        },
        tickfont: { color: colors.text, size: 10 },
        gridcolor: colors.grid,
        zeroline: true,
        zerolinecolor: colors.zeroLine,
        zerolinewidth: 1,
        range: yAxisRange,
      },
      shapes,
      annotations,
    }

    return {
      data: traces,
      layout: chartLayout,
      config: {
        displayModeBar: true,
        displaylogo: false,
        modeBarButtonsToRemove: ['pan2d', 'select2d', 'lasso2d', 'autoScale2d', 'toggleSpikelines'],
        responsive: true,
      } as Partial<PlotlyTypes.Config>,
    }
  }, [
    payoff,
    scenario,
    remainingYears,
    terminalLabel,
    showTplus0,
    title,
    chartIdentity,
    colors,
    isDark,
    formatCurrency,
    strikeLegs,
    perLegCharges,
    underlyingSymbol,
    height,
  ])

  const representativeSelection = useMemo(() => {
    const samples = payoff.samples
    if (samples.length === 0) return { samples: [], candidateCount: 0 }
    const nearest = (target: number) =>
      samples.reduce((best, sample) =>
        Math.abs(sample.underlying - target) < Math.abs(best.underlying - target) ? sample : best
      )
    const maxExpiry = samples.reduce((best, sample) =>
      sample.expiry > best.expiry ? sample : best
    )
    const minExpiry = samples.reduce((best, sample) =>
      sample.expiry < best.expiry ? sample : best
    )
    const candidateMap = new Map(
      [
        samples[0],
        nearest(scenario.spot),
        ...payoff.breakevens.map(nearest),
        maxExpiry,
        minExpiry,
        samples[samples.length - 1],
      ].map((sample) => [sample.underlying, sample])
    )
    const candidates = Array.from(candidateMap.values()).sort(
      (left, right) => left.underlying - right.underlying
    )
    const essentialMap = new Map(
      [samples[0], nearest(scenario.spot), maxExpiry, minExpiry, samples[samples.length - 1]].map(
        (sample) => [sample.underlying, sample]
      )
    )
    const remainingSlots = Math.max(0, MAX_REPRESENTATIVE_ROWS - essentialMap.size)
    const optionalCandidates = candidates.filter((sample) => !essentialMap.has(sample.underlying))
    const selected = [...essentialMap.values(), ...selectEvenly(optionalCandidates, remainingSlots)]
      .sort((left, right) => left.underlying - right.underlying)
      .slice(0, MAX_REPRESENTATIVE_ROWS)
    return { samples: selected, candidateCount: candidates.length }
  }, [payoff, scenario.spot])

  const summaryBreakevens = selectEvenly(payoff.breakevens, MAX_SUMMARY_BREAKEVENS)

  const scenarioSample = useMemo(() => {
    if (payoff.samples.length === 0) return null
    return payoff.samples.reduce((best, sample) =>
      Math.abs(sample.underlying - scenario.spot) < Math.abs(best.underlying - scenario.spot)
        ? sample
        : best
    )
  }, [payoff.samples, scenario.spot])

  const formatLimit = (value: number) =>
    Number.isFinite(value) ? formatCurrency(value) : value > 0 ? 'Unlimited' : 'Unlimited loss'
  const currentLabel = formatHorizon(scenario.daysElapsed)

  const handlePlotHover = useCallback(
    (event: PlotlyTypes.PlotHoverEvent) => {
      const container = chartContainerRef.current
      if (!container || event.points.length === 0) return
      const point = event.points.find((p) => p.data.name === terminalLabel) ?? event.points[0]
      const underlying = Number(point.x)
      const pnl = Number(point.y)
      if (!Number.isFinite(underlying) || !Number.isFinite(pnl)) return
      const rect = container.getBoundingClientRect()
      const pct = ((underlying - scenario.spot) / scenario.spot) * 100
      const sign = pct >= 0 ? '+' : ''
      setCrosshair({
        xPixel: event.event.clientX - rect.left,
        yPixel: event.event.clientY - rect.top,
        underlying,
        pnl,
        pctText: `${sign}${pct.toFixed(2)}%`,
      })
    },
    [terminalLabel, scenario.spot]
  )

  const handlePlotUnhover = useCallback(() => setCrosshair(null), [])

  return (
    <section aria-labelledby={regionHeadingId} className="min-w-0 max-w-full overflow-hidden">
      <h2 id={regionHeadingId} className="sr-only">
        {title} payoff analysis
      </h2>
      <div
        ref={chartContainerRef}
        data-payoff-chart-scope={chartScopeId}
        className="relative"
        onMouseLeave={handlePlotUnhover}
      >
        {/* Plotly's own hover box is disabled visually so the custom
            crosshair below is the only thing drawn on hover — Plotly still
            fires the hover events that drive it. Scoped to this instance so
            it doesn't affect other Plotly charts on the page. */}
        <style>{`[data-payoff-chart-scope="${chartScopeId}"] .hoverlayer { display: none; }`}</style>
        <Plot
          data={data}
          layout={layout}
          config={config}
          useResizeHandler
          style={{ width: '100%', height }}
          onHover={handlePlotHover}
          onUnhover={handlePlotUnhover}
        />
        {crosshair && (
          <div className="pointer-events-none absolute inset-0 overflow-hidden">
            <div
              className="absolute top-0 bottom-0 border-l border-dashed"
              style={{ left: crosshair.xPixel, borderColor: colors.spotLine }}
            />
            <div
              className="absolute -translate-x-1/2 whitespace-nowrap rounded-md border px-2 py-1.5 text-xs shadow-sm"
              style={{
                left: crosshair.xPixel,
                top: Math.min(Math.max(crosshair.yPixel, 34), height - 60),
                backgroundColor: colors.paper,
                borderColor: colors.cardBorder,
                color: colors.text,
              }}
            >
              <div className="tabular-nums">
                {formatCurrency(crosshair.underlying)} ({crosshair.pctText})
              </div>
              <div
                className="font-semibold tabular-nums"
                style={{ color: crosshair.pnl >= 0 ? colors.expiryLine : colors.peStrike }}
              >
                {terminalLabel}: {crosshair.pnl >= 0 ? '+' : ''}
                {formatCurrency(crosshair.pnl)}
              </div>
            </div>
          </div>
        )}
      </div>
      {belowChart && <div className="px-3 pt-3">{belowChart}</div>}
      <div className="space-y-3 border-t px-3 py-3 text-xs">
        <output aria-live="polite" aria-atomic="true" className="block text-muted-foreground">
          Scenario spot <strong className="text-foreground">{formatCurrency(scenario.spot)}</strong>
          .
          {scenarioSample && (
            <>
              {' '}
              {terminalLabel}{' '}
              <strong className="text-foreground">{formatCurrency(scenarioSample.expiry)}</strong>
              {showTplus0 && (
                <>
                  {' '}
                  and {currentLabel}{' '}
                  <strong className="text-foreground">
                    {formatCurrency(scenarioSample.tplus0)}
                  </strong>
                  .
                </>
              )}
            </>
          )}
        </output>

        <dl className="grid grid-cols-1 gap-2 sm:grid-cols-3">
          <div className="rounded-md border bg-muted/20 px-2 py-1.5">
            <dt className="text-muted-foreground">Maximum profit</dt>
            <dd className="font-semibold tabular-nums">{formatLimit(payoff.maxProfit)}</dd>
          </div>
          <div className="rounded-md border bg-muted/20 px-2 py-1.5">
            <dt className="text-muted-foreground">Maximum loss</dt>
            <dd className="font-semibold tabular-nums">{formatLimit(payoff.maxLoss)}</dd>
          </div>
          <div className="rounded-md border bg-muted/20 px-2 py-1.5">
            <dt className="text-muted-foreground">Breakevens</dt>
            <dd data-testid="breakeven-summary" className="font-semibold tabular-nums">
              {payoff.breakevens.length > 0
                ? `${summaryBreakevens.map(formatCurrency).join(', ')}${
                    payoff.breakevens.length > summaryBreakevens.length
                      ? ` (${summaryBreakevens.length} of ${payoff.breakevens.length} shown)`
                      : ''
                  }`
                : 'None in range'}
            </dd>
          </div>
        </dl>

        {representativeSelection.candidateCount > representativeSelection.samples.length && (
          <p
            id={`${regionHeadingId}-table-disclosure`}
            data-testid="representative-payoff-disclosure"
            className="text-muted-foreground"
          >
            {representativeSelection.samples.length} of {representativeSelection.candidateCount}{' '}
            representative points shown; the interactive chart retains the full sampled payoff.
          </p>
        )}

        <table
          aria-describedby={
            representativeSelection.candidateCount > representativeSelection.samples.length
              ? `${regionHeadingId}-table-disclosure`
              : undefined
          }
          className="w-full table-fixed border-collapse text-left"
        >
          <caption className="sr-only">Representative payoff values</caption>
          <thead>
            <tr className="border-b text-muted-foreground">
              <th scope="col" className="break-words px-2 py-1 font-medium">
                Underlying
              </th>
              <th scope="col" className="break-words px-2 py-1 font-medium">
                {terminalLabel}
              </th>
              {showTplus0 && (
                <th scope="col" className="break-words px-2 py-1 font-medium">
                  {currentLabel}
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {representativeSelection.samples.map((sample) => (
              <tr key={sample.underlying} className="border-b last:border-0">
                <th scope="row" className="break-words px-2 py-1 font-medium tabular-nums">
                  {formatCurrency(sample.underlying)}
                </th>
                <td className="break-words px-2 py-1 tabular-nums">
                  {formatCurrency(sample.expiry)}
                </td>
                {showTplus0 && (
                  <td className="break-words px-2 py-1 tabular-nums">
                    {formatCurrency(sample.tplus0)}
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}
