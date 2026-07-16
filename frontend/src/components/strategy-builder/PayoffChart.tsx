import type * as PlotlyTypes from 'plotly.js'
import { useMemo } from 'react'
import Plot from '@/lib/Plot2D'
import type { PayoffResult, StrategyLeg } from '@/lib/strategyMath'
import { useThemeStore } from '@/stores/themeStore'

export interface PayoffChartProps {
  title: string
  spot: number
  atmIv: number
  tYears: number
  payoff: PayoffResult
  showTplus0?: boolean
  height?: number
  legs?: StrategyLeg[]
  strikeStep?: number
  onStrikeChange?: (legId: string, strike: number) => void
}

function snapStrike(value: number, step: number): number {
  if (!step || step <= 0) return Math.round(value)
  return Math.round(value / step) * step
}

export function PayoffChart({
  title,
  spot,
  atmIv,
  tYears,
  payoff,
  showTplus0 = true,
  height = 440,
  legs = [],
  strikeStep = 50,
  onStrikeChange,
}: PayoffChartProps) {
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
    if (!xs.length) return { min: spot * 0.9, max: spot * 1.1 }
    return { min: Math.min(...xs), max: Math.max(...xs) }
  }, [payoff.samples, spot])

  const { data, layout, config } = useMemo(() => {
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

    const sigmaT = (atmIv / 100) * Math.sqrt(Math.max(tYears, 1e-6))
    const sigmaMove = spot * sigmaT
    const band = (n: number) => ({ lo: spot - n * sigmaMove, hi: spot + n * sigmaMove })
    const b1 = band(1)
    const b2 = band(2)

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
        name: 'At Expiry',
        line: { color: colors.expiryLine, width: 2.2 },
        customdata: pctFromSpot as unknown as PlotlyTypes.Datum[],
        hovertemplate:
          '<b>At Expiry P&L</b> ₹%{y:,.0f}' +
          '<br>Chg. from Spot: %{customdata}' +
          '<extra></extra>',
      },
    ]

    if (showTplus0) {
      traces.push({
        x: xs,
        y: ysT0,
        type: 'scatter',
        mode: 'lines',
        name: 'T+0',
        line: { color: colors.tplus0Line, width: 2, dash: 'dash' },
        hovertemplate: '<b>T+0 P&L</b> ₹%{y:,.0f}<extra></extra>',
      })
    }

    // Bold vertical strike lines (always visible on chart)
    for (const leg of strikeLegs) {
      const strike = leg.strike ?? spot
      const isCe = leg.optionType === 'CE'
      traces.push({
        x: [strike, strike],
        y: [yLo, yHi],
        type: 'scatter',
        mode: 'lines',
        name: `${leg.optionType} ${strike}`,
        line: {
          color: isCe ? colors.ceStrike : colors.peStrike,
          width: 3,
          dash: 'solid',
        },
        hovertemplate: `<b>${leg.optionType}</b> strike %{x:,.0f}<extra></extra>`,
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

    if (sigmaMove > 0) {
      shapes.push({
        type: 'rect',
        xref: 'x',
        x0: b2.lo,
        x1: b1.lo,
        yref: 'paper',
        y0: 0,
        y1: 1,
        fillcolor: colors.sigma2Band,
        line: { width: 0 },
        layer: 'below',
      })
      shapes.push({
        type: 'rect',
        xref: 'x',
        x0: b1.hi,
        x1: b2.hi,
        yref: 'paper',
        y0: 0,
        y1: 1,
        fillcolor: colors.sigma2Band,
        line: { width: 0 },
        layer: 'below',
      })
      shapes.push({
        type: 'rect',
        xref: 'x',
        x0: b1.lo,
        x1: b1.hi,
        yref: 'paper',
        y0: 0,
        y1: 1,
        fillcolor: colors.sigma1Band,
        line: { width: 0 },
        layer: 'below',
      })
      for (const x of [b2.lo, b1.lo, b1.hi, b2.hi]) {
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

    if (sigmaMove > 0) {
      for (const s of [
        { x: b2.lo, text: '-2σ' },
        { x: b1.lo, text: '-1σ' },
        { x: b1.hi, text: '+1σ' },
        { x: b2.hi, text: '+2σ' },
      ]) {
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
      },
      yaxis: {
        title: { text: 'Profit / Loss (₹)', font: { color: colors.text, size: 12 } },
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
  }, [payoff, spot, atmIv, tYears, showTplus0, title, colors, isDark, strikeLegs])

  return (
    <div className="space-y-3">
      <Plot
        data={data}
        layout={layout}
        config={config}
        useResizeHandler
        style={{ width: '100%', height }}
      />
      {onStrikeChange && strikeLegs.length > 0 && (
        <div className="space-y-2 rounded-lg border bg-muted/20 px-3 py-2.5">
          <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
            Adjust strikes (drag sliders — updates legs &amp; charges)
          </div>
          {strikeLegs.map((leg) => {
            const strike = leg.strike ?? spot
            const isCe = leg.optionType === 'CE'
            return (
              <label key={leg.id} className="flex items-center gap-3 text-xs">
                <span
                  className="w-14 shrink-0 font-bold tabular-nums"
                  style={{ color: isCe ? colors.ceStrike : colors.peStrike }}
                >
                  {leg.optionType}
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
              </label>
            )
          })}
        </div>
      )}
    </div>
  )
}
