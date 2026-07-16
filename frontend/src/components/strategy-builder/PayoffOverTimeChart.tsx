import type * as PlotlyTypes from 'plotly.js'
import { useMemo } from 'react'
import Plot from '@/lib/Plot2D'
import { useThemeStore } from '@/stores/themeStore'

export interface PayoffOverTimeSample {
  days_to_expiry?: number | null
  pnl?: number | null
  net_pnl?: number | null
}

export interface PayoffOverTimeChartProps {
  title?: string
  samples: PayoffOverTimeSample[]
  height?: number
}

export function PayoffOverTimeChart({
  title = 'P&L vs days to expiry (from hub)',
  samples,
  height = 280,
}: PayoffOverTimeChartProps) {
  const { mode, appMode } = useThemeStore()
  const isDark = mode === 'dark' || appMode === 'analyzer'

  const { xDays, gross, net, hasNet } = useMemo(() => {
    const sorted = [...samples]
      .filter((s) => s.days_to_expiry !== undefined && s.days_to_expiry !== null)
      .sort((a, b) => (b.days_to_expiry ?? 0) - (a.days_to_expiry ?? 0))
    return {
      xDays: sorted.map((s) => s.days_to_expiry as number),
      gross: sorted.map((s) => s.pnl ?? 0),
      net: sorted.map((s) => s.net_pnl ?? s.pnl ?? 0),
      hasNet: sorted.some((s) => s.net_pnl !== undefined && s.net_pnl !== null),
    }
  }, [samples])

  const colors = useMemo(
    () => ({
      paper: isDark ? '#0f172a' : '#ffffff',
      bg: isDark ? '#1e293b' : '#f8fafc',
      text: isDark ? '#e2e8f0' : '#1e293b',
      grid: isDark ? 'rgba(148,163,184,0.18)' : 'rgba(15,23,42,0.08)',
      gross: isDark ? '#60a5fa' : '#2563eb',
      net: isDark ? '#34d399' : '#059669',
    }),
    [isDark]
  )

  if (xDays.length === 0) {
    return null
  }

  const traces: PlotlyTypes.Data[] = [
    {
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Gross P&L',
      x: xDays,
      y: gross,
      line: { color: colors.gross, width: 2 },
      marker: { size: 6 },
      hovertemplate: 'DTE %{x}<br>Gross ₹%{y:,.0f}<extra></extra>',
    },
  ]

  if (hasNet) {
    traces.push({
      type: 'scatter',
      mode: 'lines+markers',
      name: 'Net P&L (after charges)',
      x: xDays,
      y: net,
      line: { color: colors.net, width: 2, dash: 'dot' },
      marker: { size: 6 },
      hovertemplate: 'DTE %{x}<br>Net ₹%{y:,.0f}<extra></extra>',
    })
  }

  return (
    <div className="overflow-hidden rounded-xl border bg-card p-2 shadow-sm">
      <Plot
        data={traces}
        layout={{
          title: { text: title, font: { size: 13, color: colors.text } },
          paper_bgcolor: colors.paper,
          plot_bgcolor: colors.bg,
          font: { color: colors.text, size: 11 },
          height,
          margin: { l: 56, r: 16, t: 40, b: 44 },
          xaxis: {
            title: 'Days to expiry',
            gridcolor: colors.grid,
            zeroline: false,
            dtick: 1,
          },
          yaxis: {
            title: 'P&L (₹)',
            gridcolor: colors.grid,
            zeroline: true,
            zerolinecolor: colors.grid,
            tickformat: ',.0f',
          },
          legend: { orientation: 'h', y: 1.12, x: 0 },
          hovermode: 'x unified',
        }}
        config={{ displayModeBar: false, responsive: true }}
        style={{ width: '100%' }}
      />
    </div>
  )
}
