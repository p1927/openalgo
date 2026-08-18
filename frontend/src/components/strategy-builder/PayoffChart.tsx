import type * as PlotlyTypes from 'plotly.js'
import { MoveHorizontal, RotateCcw } from 'lucide-react'
import { useEffect, useId, useMemo, useRef, useState } from 'react'
import Plot from '@/lib/Plot2D'
import { Button } from '@/components/ui/button'
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
  strikeStep?: number
  onStrikeChange?: (legId: string, strike: number) => void
  onResetStrikes?: () => void
  canResetStrikes?: boolean
  /**
   * Per-leg round-trip charge totals (leg.id → total charges). When supplied,
   * the hover tooltip gains an "After charges" row and the y-axis title is
   * updated to "Net P&L (after charges)". The math is already netted upstream
   * into the `payoff` samples — this prop only changes how the chart presents
   * the "P&L after charges" number in the tooltip.
   */
  perLegCharges?: Record<string, number>
}

const MAX_REPRESENTATIVE_ROWS = 7
const MAX_SUMMARY_BREAKEVENS = 4

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

function snapStrike(value: number, step: number): number {
  if (!step || step <= 0) return Math.round(value)
  return Math.round(value / step) * step
}

// The drag handle's hit target used to be a fixed ±9 data-space (price)
// units regardless of the chart's price scale. That's a comfortable ~18px
// target on a low-priced instrument's chart but shrinks to a couple of
// pixels on NIFTY/BANKNIFTY-scale charts, where the visible x-axis spans
// thousands of rupees over the same pixel width — making the handle nearly
// unclickable. Sizing the handle as a percentage of the *visible domain
// width* instead keeps its rendered pixel size roughly constant across
// price scales, since domain-width-to-pixel-width is roughly constant for a
// given chart container size.
const HANDLE_WIDTH_PCT_OF_DOMAIN = 0.03

/**
 * When two or more legs share (or nearly share) a strike, their lines and
 * drag-handles would otherwise render exactly on top of each other —
 * visually indistinguishable, and only the topmost shape in Plotly's
 * z-order actually receives drag/click events. `offset` nudges the leg's
 * line + handle apart from its strike-mates along the x-axis by a small,
 * scale-invariant amount so every leg in the cluster stays independently
 * visible and draggable; `handleRelayout` subtracts it back out before
 * resolving the dragged x-position to a real strike.
 */
type StrikeOffsetInfo = { offset: number; groupSize: number; groupIndex: number }

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
  strikeStep = 50,
  onStrikeChange,
  onResetStrikes,
  canResetStrikes = false,
  perLegCharges,
}: PayoffChartProps) {
  const regionHeadingId = useId()
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

  // Previous-strike cache used by `handleRelayout` to suppress duplicate
  // onStrikeChange emissions. Plotly's plotly_relayout event fires per
  // commit-not-per-pixel, but the handle circle shares its x with the line
  // (both edits resolve to the same strike), and Plotly sometimes fires two
  // relayout events for one drag — checking against the snapshot prevents
  // back-to-back identical emits that would still trigger upstream
  // setLegs(...) work.
  const previousStrikesRef = useRef<Map<string, number>>(new Map())

  // Populated each render from the useMemo below (see the sync effect further
  // down) so `handleRelayout` — which runs outside the memo, in response to a
  // user drag — can undo the anti-overlap offset before resolving the real
  // strike.
  const strikeOffsetsRef = useRef<Map<string, StrikeOffsetInfo>>(new Map())

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
      sigma1Band: isDark ? 'rgba(148,163,184,0.18)' : 'rgba(100,116,139,0.10)',
      sigma2Band: isDark ? 'rgba(148,163,184,0.07)' : 'rgba(100,116,139,0.04)',
      sigmaTick: isDark ? 'rgba(148,163,184,0.30)' : 'rgba(15,23,42,0.18)',
      cardBorder: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(15,23,42,0.08)',
    }),
    [isDark, isAnalyzer]
  )

  const { data, layout, config, strikeOffsetByLegId } = useMemo(() => {
    const { spot, iv, daysElapsed } = scenario
    const { samples } = payoff
    if (samples.length === 0) {
      return {
        data: [] as PlotlyTypes.Data[],
        layout: {} as Partial<PlotlyTypes.Layout>,
        config: {},
        strikeOffsetByLegId: new Map<string, StrikeOffsetInfo>(),
      }
    }

    const xs = samples.map((s) => s.underlying)
    const ysExpiry = samples.map((s) => s.expiry)
    const ysT0 = samples.map((s) => s.tplus0)

    const pctFromSpot = samples.map((s) => {
      const pct = ((s.underlying - spot) / spot) * 100
      const sign = pct >= 0 ? '+' : ''
      return `${sign}${pct.toFixed(2)}%`
    })

    const profitFill = samples.map((s) => (s.expiry >= 0 ? s.expiry : 0))
    const lossFill = samples.map((s) => (s.expiry < 0 ? s.expiry : 0))

    const b1 = lognormalPriceBand(spot, iv, remainingYears, 1)
    const b2 = lognormalPriceBand(spot, iv, remainingYears, 2)
    const domainLo = xs[0]
    const domainHi = xs[xs.length - 1]
    const inDomain = (x: number) => x >= domainLo && x <= domainHi
    const clipToDomain = (x: number) => Math.min(domainHi, Math.max(domainLo, x))

    // The sampled data domain (domainLo/domainHi above) is whatever
    // payoffPriceRange/computePayoff needed to cover every strike and
    // breakeven — it's frequently NOT symmetric around spot (e.g. a single
    // naked call struck well above spot pulls the domain rightward with
    // nothing to balance it on the left). Left as-is, that puts the spot
    // line off-center, unlike Groww's payoff chart which always keeps spot
    // in the visual middle. Widen the shorter side of the *visible axis
    // window* (not the sampled data — the P&L curve itself is untouched) so
    // spot sits at the horizontal midpoint; the shorter side simply gets a
    // blank margin past its last real sample, same as the longer side
    // already has past its most extreme relevant strike.
    const axisHalfWidth = Math.max(spot - domainLo, domainHi - spot)
    const axisLo = Math.max(0, spot - axisHalfWidth)
    const axisHi = spot + axisHalfWidth
    const axisWidth = axisHi - axisLo
    // Scale-invariant hit target: see HANDLE_WIDTH_PCT_OF_DOMAIN above.
    const handleHalfWidth = (axisWidth * HANDLE_WIDTH_PCT_OF_DOMAIN) / 2

    const currentLabel = formatHorizon(daysElapsed)
    // When `perLegCharges` is supplied, the tooltip gains an extra row at
    // customdata[3] that surfaces the "After charges" P&L. The chart
    // contract is fixed even when no charges are present (customdata length
    // stays at 3 in that case so the hovertemplate keeps rendering).
    const chargesRow = perLegCharges
      ? '<br>After charges: %{customdata[3]}'
      : ''
    const hoverTemplate = (label: string) =>
      `<b>${label}</b>` +
      '<br>Underlying: %{customdata[0]}' +
      '<br>Chg. from Scenario: %{customdata[1]}' +
      '<br>P&L: %{customdata[2]}' +
      chargesRow +
      '<extra></extra>'
    const hoverData = (values: number[]) =>
      samples.map((sample, index) => {
        const row: (string | number)[] = [
          formatCurrency(sample.underlying),
          pctFromSpot[index],
          formatCurrency(values[index]),
        ]
        if (perLegCharges) {
          // sample.expiry is already net-of-charges because StrategyBuilder
          // threads perLegCharges into computePayoff() upstream of this chart.
          row.push(formatCurrency(sample.expiry))
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

    // Plotly's `Partial<Shape>` type is strict — it doesn't expose the
    // `editable: true` flag (per Plotly docs, a shape-level flag that
    // makes individual shapes draggable) or a string `name`/`legendwidth`
    // used for round-tripping through `plotly_relayout`. Locally we extend
    // Shape with the bits we actually drive; the runtime cast at render
    // time is harmless because we hand the array back to Plotly unmodified.
    type EditableShape = Partial<PlotlyTypes.Shape> & {
      editable?: boolean
      name?: string
      legendwidth?: number
    }
    const shapes: EditableShape[] = []

    // Cluster legs whose strikes fall within one handle-width of each other
    // (e.g. a straddle/strangle collapsed to one strike, or a calendar
    // spread) and assign each a small deterministic x-offset so their lines
    // and handles don't stack exactly on top of one another. See
    // `StrikeOffsetInfo` above for why this exists.
    const strikeOffsetByLegId = new Map<string, StrikeOffsetInfo>()
    {
      const collisionThreshold = handleHalfWidth * 2.2
      const offsetStep = handleHalfWidth * 2.4
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

    // Vertical strike lines + on-line handle dots. When `onStrikeChange` is
    // supplied the user can drag these on-chart affordances to change a leg's
    // strike; see the `handleRelayout` callback wired below. When the callback
    // is absent we still render a non-editable shape so the legend stays
    // consistent — strike identity is meaningful even when interaction is off.
    const strikeEditable = Boolean(onStrikeChange)
    strikeLegs.forEach((leg, i) => {
      const strike = leg.strike ?? spot
      const renderX = strike + (strikeOffsetByLegId.get(leg.id)?.offset ?? 0)
      const isCe = leg.optionType === 'CE'
      const isSell = leg.side === 'SELL'
      const color = isCe ? colors.ceStrike : colors.peStrike
      // 1) Vertical strike line — drawn above plots but below annotations
      //    so the handles render on top of it.
      shapes.push({
        type: 'line',
        xref: 'x',
        yref: 'paper',
        x0: renderX,
        x1: renderX,
        y0: 0,
        y1: 1,
        line: {
          color,
          width: isSell ? 2 : 3,
          dash: isSell ? 'dash' : 'solid',
        },
        editable: strikeEditable,
        // `name` is the round-trip key — handleRelayout parses `strike:<id>`
        // back into a leg id and re-snaps the strike on commit.
        name: `strike:${leg.id}`,
        legendgroup: `strike-${i}`,
        legendwidth: 1.4,
        layer: 'below',
      })
      // 2) Small circle handle anchored just above the plot area so it
      //    doesn't fight with the strike-line for clicks/hover. Sized as a
      //    percentage of the visible axis width (see HANDLE_WIDTH_PCT_OF_DOMAIN)
      //    so the rendered hit target stays comfortably clickable whether
      //    the underlying trades at 100 or 24,000.
      if (strikeEditable) {
        shapes.push({
          type: 'circle',
          xref: 'x',
          yref: 'paper',
          x0: renderX - handleHalfWidth,
          x1: renderX + handleHalfWidth,
          y0: 0.985,
          y1: 1.025,
          fillcolor: color,
          line: { color, width: 2 },
          editable: true,
          name: `handle:${leg.id}`,
        })
      }
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

    // Stepped σ bands: the wider 2σ band is drawn first so the 1σ band
    // overlays on top of it, producing a visually distinct inner (darker)
    // and outer (lighter) zone rather than one uniform wash.
    if (b1 && b2) {
      const pushBand = (x0: number, x1: number, fillcolor: string) => {
        const clippedX0 = clipToDomain(x0)
        const clippedX1 = clipToDomain(x1)
        if (clippedX1 <= clippedX0) return
        shapes.push({
          type: 'rect',
          xref: 'x',
          x0: clippedX0,
          x1: clippedX1,
          yref: 'paper',
          y0: 0,
          y1: 1,
          fillcolor,
          line: { width: 0 },
          layer: 'below',
        })
      }
      // Left outer band: from -2σ to -1σ
      pushBand(b2.lower, b1.lower, colors.sigma2Band)
      // Right outer band: from +1σ to +2σ
      pushBand(b1.upper, b2.upper, colors.sigma2Band)
      // Inner 1σ band
      pushBand(b1.lower, b1.upper, colors.sigma1Band)
      // Thin vertical ticks at each σ boundary
      for (const x of [b2.lower, b1.lower, b1.upper, b2.upper]) {
        if (!inDomain(x)) continue
        shapes.push({
          type: 'line',
          xref: 'x',
          x0: x,
          x1: x,
          yref: 'paper',
          y0: 0,
          y1: 1,
          line: { color: colors.sigmaTick, width: 1, dash: 'dot' },
          layer: 'below',
        })
      }
    }

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

    // Spot label — anchored at spot x but on `y: 1.06 (paper)` just like
    // the σ labels. When a strike is within ~3% of spot, push the spot
    // label left/right of the strike to avoid overlap; otherwise it sits
    // directly above spot.
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
      y: 1.06,
      xref: 'x',
      yref: 'paper',
      text: `<b>${spot.toFixed(2)}</b>`,
      showarrow: false,
      xanchor: spotXAnchor,
      yanchor: 'bottom',
      font: { size: 12, color: colors.spotLine },
    })

    if (b1 && b2) {
      const sigmaLabels: Array<{ x: number; text: string }> = [
        { x: b2.lower, text: '-2σ' },
        { x: b1.lower, text: '-1σ' },
        { x: b1.upper, text: '+1σ' },
        { x: b2.upper, text: '+2σ' },
      ]
      for (const s of sigmaLabels) {
        if (!inDomain(s.x)) continue
        annotations.push({
          x: s.x,
          y: 1.06,
          xref: 'x',
          yref: 'paper',
          text: s.text,
          showarrow: false,
          yanchor: 'bottom',
          font: { size: 11, color: colors.mutedText },
        })
      }
    }

    // Per-leg strike annotations — same y row as the σ / spot labels. Each
    // bold S/B + currency + CE/PE identifies the leg and current strike at
    // a glance. Annotations are pushed AFTER σ and spot so Plotly's renderer
    // lays them last (annotations paint on top of shapes by default).
    strikeLegs.forEach((leg) => {
      const strike = leg.strike ?? spot
      const offsetInfo = strikeOffsetByLegId.get(leg.id)
      const renderX = strike + (offsetInfo?.offset ?? 0)
      const isCe = leg.optionType === 'CE'
      const isSell = leg.side === 'SELL'
      const color = isCe ? colors.ceStrike : colors.peStrike
      const sideMark = isSell ? 'S' : 'B'
      const optMark = leg.optionType ?? ''
      annotations.push({
        x: renderX,
        // Labels for a colliding cluster are additionally staggered
        // vertically (on top of the x-offset shared with their line/handle)
        // so overlapping strike text doesn't collapse into an unreadable
        // blur — mirrors the anti-overlap treatment already used for the
        // spot label above.
        y: 1.045 + (offsetInfo ? offsetInfo.groupIndex * 0.02 : 0),
        xref: 'x',
        yref: 'paper',
        text: `<b>${sideMark}</b> ${formatCurrency(strike)} <b>${optMark}</b>`,
        showarrow: false,
        xanchor: 'center',
        yanchor: 'bottom',
        font: {
          size: 10,
          color,
          family: 'system-ui, sans-serif',
        },
      })
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
      // Globals that gate Plotly's shape-edit interactions:
      //   - editable:true at the layout level tells Plotly to wire up its
      //     shape-drag handlers and emit plotly_relayout events as the user
      //     drags. Without it, the per-shape `editable: true` is silently
      //     ignored — every pointerdown on a shape is captured by the
      //     default 'zoom' drag handler instead.
      //   - dragmode:'pan' means left-click drags the viewport horizontally/
      //     vertically instead of drawing a zoom-rectangle. With 'zoom',
      //     shape drags are intercepted by the chart panel's mousedown;
      //     'pan' lets them flow through to the shape layer. Users still
      //     get a "zoom" toggle button via the modeBar (see config below).
      // @types/plotly.js v8 still types Layout.editable as optional; we
      // pass it through as an explicit `any`-shaped clause to avoid a
      // type mismatch without polluting the rest of the layout.
      ...({
        editable: strikeEditable,
        dragmode: strikeEditable ? 'pan' : 'zoom',
      } as any),
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
      margin: { l: 70, r: 30, t: 80, b: 50 },
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
      strikeOffsetByLegId,
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

  // Keep the ref in sync with the latest per-leg anti-overlap offsets so
  // `handleRelayout` (which fires from a Plotly event outside this render,
  // not from the memo above) can undo the right offset for whichever leg
  // was dragged.
  useEffect(() => {
    strikeOffsetsRef.current = strikeOffsetByLegId
  }, [strikeOffsetByLegId])

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

  // re-plotly.js's `onRelayout` payload includes `layout.shapes` whenever a
  // shape was dragged. We only react to lines whose `name` starts with
  // "strike:" — the matching "handle:<id>" circle's edit resolves to the
  // same strike and is ignored to avoid double-emit.
  const handleRelayout = (gd: { layout?: { shapes?: PlotlyTypes.Shape[] } } | null | undefined) => {
    if (!onStrikeChange) return
    const shapes = gd?.layout?.shapes
    if (!Array.isArray(shapes) || shapes.length === 0) return
    for (const sh of shapes) {
      const name = (sh as { name?: string }).name
      if (typeof name !== 'string' || !name.startsWith('strike:')) continue
      const legId = name.slice('strike:'.length)
      const x = Number(sh.x0)
      if (!Number.isFinite(x)) continue
      const leg = strikeLegs.find((l) => l.id === legId)
      if (!leg) continue
      // Undo the anti-overlap offset (see StrikeOffsetInfo) before snapping
      // — the shape's rendered x is nudged away from the true strike when
      // it shares one with another leg.
      const offset = strikeOffsetsRef.current.get(legId)?.offset ?? 0
      const snapped = snapStrike(x - offset, strikeStep)
      const prev = previousStrikesRef.current.get(legId)
      if (prev === snapped) continue
      // First drag ever — flip the local "seen-drag" toggle so the chart
      // header drops its one-time "drag any strike line" hint callout.
      if (!hasInteractedRef.current) {
        hasInteractedRef.current = true
        setHintVisible(false)
      }
      // Emit first, then record — `handleStrikeFromChart` in StrategyBuilder
      // mirrors `leg.strike` so on the next relayout (e.g. the handle's
      // own drift back to the same x) `prev === snapped` will already hold.
      onStrikeChange(legId, snapped)
      previousStrikesRef.current.set(legId, snapped)
    }
  }

  // Track whether the user has dragged a strike at least once. The
  // local hint callout disappears the moment they've touched a strike —
  // it's a discovery aid, not a permanent label. `strikeEditable` and
  // `strikeLegs` are read from the chart's render closure; we can't
  // capture them outside the useMemo without re-deriving, so declare a
  // tiny memo here for the same gate the hint uses.
  const dragHintEligible = Boolean(onStrikeChange) && strikeLegs.length > 0
  const hasInteractedRef = useRef(false)
  const [hintVisible, setHintVisible] = useState(dragHintEligible)
  // Keep hintVisible in sync with the gate: re-enable it if a future flag
  // flips editability back on for a populated chart.
  useEffect(() => {
    if (!hasInteractedRef.current) {
      setHintVisible(dragHintEligible)
    }
  }, [dragHintEligible])

  return (
    <section aria-labelledby={regionHeadingId} className="min-w-0 max-w-full overflow-hidden">
      <h2 id={regionHeadingId} className="sr-only">
        {title} payoff analysis
      </h2>
      <Plot
        data={data}
        layout={layout}
        config={config}
        useResizeHandler
        onRelayout={handleRelayout}
        style={{ width: '100%', height }}
      />
      {onStrikeChange && strikeLegs.length > 0 && onResetStrikes && canResetStrikes && (
        <div className="flex items-center justify-end px-1 pb-1.5">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-7 gap-1 text-[10px]"
            onClick={onResetStrikes}
          >
            <RotateCcw className="h-3 w-3" /> Reset strikes
          </Button>
        </div>
      )}
      {hintVisible && (
        <div
          className="flex items-center justify-center gap-1.5 -mt-2 pb-1.5 text-[10px] font-medium text-muted-foreground"
          aria-live="polite"
        >
          <MoveHorizontal className="h-3 w-3" aria-hidden="true" />
          Drag any strike line on the chart to adjust its value
        </div>
      )}
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
