import type * as PlotlyTypes from 'plotly.js'
import { RotateCcw } from 'lucide-react'
import { useId, useMemo } from 'react'
import Plot from '@/lib/Plot2D'
import { Button } from '@/components/ui/button'
import {
  lognormalPriceBand,
  type PayoffResult,
  type ScenarioState,
  type StrategyLeg,
} from '@/lib/strategyMath'
import { cn } from '@/lib/utils'
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
}

function legLabel(leg: StrategyLeg): string {
  const side = leg.side === 'SELL' ? 'S' : 'B'
  const opt = leg.optionType ?? 'CE'
  const strike = leg.strike ?? 0
  return `${side} ${opt} ${strike}`
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

  const colors = useMemo(
    () => ({
      paper: isDark ? (isAnalyzer ? '#1a1530' : '#0f172a') : '#ffffff',
      bg: isDark ? (isAnalyzer ? '#221a3a' : '#1e293b') : '#f8fafc',
      text: isDark ? '#e2e8f0' : '#1e293b',
      mutedText: isDark ? '#94a3b8' : '#64748b',
      grid: isDark ? 'rgba(148,163,184,0.18)' : 'rgba(15,23,42,0.08)',
      profit: isDark ? 'rgba(34,197,94,0.22)' : 'rgba(34,197,94,0.18)',
      loss: isDark ? 'rgba(239,68,68,0.22)' : 'rgba(239,68,68,0.18)',
      expiryLine: isDark ? '#fb923c' : '#ea580c',
      tplus0Line: isDark ? '#60a5fa' : '#2563eb',
      zeroLine: isDark ? 'rgba(226,232,240,0.5)' : 'rgba(15,23,42,0.5)',
      spotLine: isDark ? '#f472b6' : '#db2777',
      ceStrike: isDark ? '#4ade80' : '#16a34a',
      peStrike: isDark ? '#f87171' : '#dc2626',
      sigma1Band: isDark ? 'rgba(148,163,184,0.22)' : 'rgba(100,116,139,0.16)',
      sigma2Band: isDark ? 'rgba(148,163,184,0.10)' : 'rgba(100,116,139,0.07)',
      sigmaTick: isDark ? 'rgba(226,232,240,0.35)' : 'rgba(15,23,42,0.3)',
    }),
    [isDark, isAnalyzer]
  )

  const xBounds = useMemo(() => {
    const xs = payoff.samples.map((s) => s.underlying)
    if (!xs.length) return { min: scenario.spot * 0.9, max: scenario.spot * 1.1 }
    return { min: Math.min(...xs), max: Math.max(...xs) }
  }, [payoff.samples, scenario.spot])

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

    const xs = samples.map((s) => s.underlying)
    const ysExpiry = samples.map((s) => s.expiry)
    const ysT0 = samples.map((s) => s.tplus0)
    const yLo = Math.min(...ysExpiry, ...ysT0, 0) * 1.05
    const yHi = Math.max(...ysExpiry, ...ysT0, 0) * 1.05

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

    const currentLabel = formatHorizon(daysElapsed)
    const hoverTemplate = (label: string) =>
      `<b>${label}</b>` +
      '<br>Underlying: %{customdata[0]}' +
      '<br>Chg. from Scenario: %{customdata[1]}' +
      '<br>P&L: %{customdata[2]}' +
      '<extra></extra>'
    const hoverData = (values: number[]) =>
      samples.map((sample, index) => [
        formatCurrency(sample.underlying),
        pctFromSpot[index],
        formatCurrency(values[index]),
      ])

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
        line: { color: colors.expiryLine, width: 2.2 },
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
        line: { color: colors.tplus0Line, width: 2, dash: 'dash' },
        customdata: hoverData(ysT0) as unknown as PlotlyTypes.Datum[],
        hovertemplate: hoverTemplate(currentLabel),
      })
    }

    // Bold vertical strike lines (always visible on chart)
    for (const leg of strikeLegs) {
      const strike = leg.strike ?? spot
      const isCe = leg.optionType === 'CE'
      const isSell = leg.side === 'SELL'
      traces.push({
        x: [strike, strike],
        y: [yLo, yHi],
        type: 'scatter',
        mode: 'lines',
        name: legLabel(leg),
        line: {
          color: isCe ? colors.ceStrike : colors.peStrike,
          width: isSell ? 2 : 3,
          dash: isSell ? 'dash' : 'solid',
        },
        hovertemplate: `<b>${leg.side} ${leg.optionType}</b> @ %{x:,.0f}<extra></extra>`,
        showlegend: true,
      })
    }

    const shapes: Partial<PlotlyTypes.Shape>[] = [
      {
        type: 'line',
        xref: 'paper',
        x0: 0,
        x1: 1,
        yref: 'y',
        y0: 0,
        y1: 0,
        line: { color: colors.zeroLine, width: 1 },
      },
    ]

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

    const annotations: Partial<PlotlyTypes.Annotations>[] = [
      {
        x: spot,
        y: 1.06,
        xref: 'x',
        yref: 'paper',
        text: `<b>${spot.toFixed(2)}</b>`,
        showarrow: false,
        yanchor: 'bottom',
        font: { size: 12, color: colors.spotLine },
      },
    ]

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
        font: { color: colors.text, size: 11 },
      },
      xaxis: {
        title: { text: 'Underlying Price', font: { color: colors.text, size: 12 } },
        tickfont: { color: colors.text, size: 10 },
        gridcolor: colors.grid,
        zeroline: false,
        range: [xs[0], xs[xs.length - 1]],
      },
      yaxis: {
        title: { text: 'Profit / Loss', font: { color: colors.text, size: 12 } },
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
        style={{ width: '100%', height }}
      />
      {onStrikeChange && strikeLegs.length > 0 && (
        <div className="space-y-2 rounded-lg border bg-muted/20 px-3 py-2.5">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
              Adjust strikes (updates symbol, price &amp; charges)
            </div>
            {onResetStrikes && canResetStrikes && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-7 gap-1 text-[10px]"
                onClick={onResetStrikes}
              >
                <RotateCcw className="h-3 w-3" />
                Reset
              </Button>
            )}
          </div>
          {strikeLegs.map((leg) => {
            const strike = leg.strike ?? scenario.spot
            const isCe = leg.optionType === 'CE'
            const isBuy = leg.side === 'BUY'
            return (
              <label key={leg.id} className="flex items-center gap-3 text-xs">
                <span
                  className={cn(
                    'inline-flex w-[4.5rem] shrink-0 items-center gap-1 font-bold tabular-nums',
                    isBuy ? 'text-emerald-700 dark:text-emerald-400' : 'text-rose-700 dark:text-rose-400'
                  )}
                  title={`${leg.side} ${leg.optionType}`}
                >
                  <span
                    className={cn(
                      'inline-flex h-4 w-4 items-center justify-center rounded text-[9px]',
                      isBuy ? 'bg-emerald-500/15' : 'bg-rose-500/15'
                    )}
                  >
                    {isBuy ? 'B' : 'S'}
                  </span>
                  <span style={{ color: isCe ? colors.ceStrike : colors.peStrike }}>{leg.optionType}</span>
                </span>
                <input
                  type="range"
                  className="h-2 flex-1 cursor-pointer accent-current"
                  style={{ accentColor: isCe ? colors.ceStrike : colors.peStrike }}
                  min={xBounds.min}
                  max={xBounds.max}
                  step={strikeStep > 0 ? strikeStep : 1}
                  value={strike}
                  onChange={(e) => {
                    const next = snapStrike(Number(e.target.value), strikeStep)
                    if (next !== strike) onStrikeChange(leg.id, next)
                  }}
                />
                <span className="w-16 shrink-0 text-right font-semibold tabular-nums">{strike}</span>
                <span className="hidden w-24 shrink-0 truncate text-[10px] text-muted-foreground sm:inline">
                  ₹{leg.price.toFixed(1)}
                </span>
              </label>
            )
          })}
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
