import { RotateCcw } from 'lucide-react'
import { useMemo } from 'react'
import { Button } from '@/components/ui/button'
import type { StrategyLeg } from '@/lib/strategyMath'
import { useThemeStore } from '@/stores/themeStore'
import type { OptionStrike } from '@/types/option-chain'

export interface StrikeSliderRailProps {
  legs: StrategyLeg[]
  chain: OptionStrike[] | null | undefined
  onStrikeChange: (legId: string, strike: number) => void
  onResetStrikes?: () => void
  canResetStrikes?: boolean
}

/**
 * The strike-adjustment control for the payoff chart above it — one slider
 * per active option leg, each stepping only through strikes that leg's side
 * (CE/PE) actually has a listed, quoted contract for. That's the whole
 * fix for "dragging a strike line sometimes silently did nothing": a plain
 * `<input type="range">` indexed over the real strike list *cannot* select
 * a price that doesn't exist, so there's no snapping-to-a-computed-step
 * math left to get wrong (see PayoffChart.tsx's module comment for why
 * on-chart dragging was retired in favor of this).
 */
export default function StrikeSliderRail({
  legs,
  chain,
  onStrikeChange,
  onResetStrikes,
  canResetStrikes = false,
}: StrikeSliderRailProps) {
  const { mode, appMode } = useThemeStore()
  const isDark = mode === 'dark' || appMode === 'analyzer'
  const ceColor = isDark ? '#4ade80' : '#16a34a'
  const peColor = isDark ? '#f87171' : '#dc2626'
  const trackColor = isDark ? '#334155' : '#e2e8f0'

  const strikeLegs = useMemo(
    () =>
      legs.filter(
        (l) =>
          l.active &&
          l.segment === 'OPTION' &&
          l.strike !== undefined &&
          l.optionType &&
          !(l.exitPrice && l.exitPrice > 0)
      ),
    [legs]
  )

  if (strikeLegs.length === 0) return null

  return (
    <div className="space-y-2 rounded-md border bg-muted/10 px-3 py-2.5">
      <div className="flex items-center justify-between">
        <p className="text-xs font-medium text-muted-foreground">Adjust strikes</p>
        {onResetStrikes && canResetStrikes && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-6 gap-1 text-[10px]"
            onClick={onResetStrikes}
          >
            <RotateCcw className="h-3 w-3" /> Reset strikes
          </Button>
        )}
      </div>
      {strikeLegs.map((leg) => {
        const availableStrikes = (chain ?? [])
          .filter((row) => (leg.optionType === 'CE' ? row.ce : row.pe))
          .map((row) => row.strike)
          .sort((a, b) => a - b)
        if (availableStrikes.length === 0) return null

        const currentStrike = leg.strike ?? availableStrikes[0]
        const currentIndex = availableStrikes.reduce(
          (closest, s, i) =>
            Math.abs(s - currentStrike) < Math.abs(availableStrikes[closest] - currentStrike)
              ? i
              : closest,
          0
        )
        const isSell = leg.side === 'SELL'
        const color = leg.optionType === 'CE' ? ceColor : peColor
        const fillPct =
          availableStrikes.length > 1 ? (currentIndex / (availableStrikes.length - 1)) * 100 : 100

        return (
          <div key={leg.id} className="flex items-center gap-2.5">
            <span className="w-24 shrink-0 truncate text-[11px] font-semibold" style={{ color }}>
              {isSell ? 'S' : 'B'} {leg.strike} {leg.optionType}
            </span>
            <input
              type="range"
              min={0}
              max={availableStrikes.length - 1}
              step={1}
              value={currentIndex}
              onChange={(e) => onStrikeChange(leg.id, availableStrikes[Number(e.target.value)])}
              className="h-1.5 flex-1 cursor-pointer appearance-none rounded-full
                [&::-webkit-slider-thumb]:h-3.5 [&::-webkit-slider-thumb]:w-3.5
                [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full
                [&::-webkit-slider-thumb]:border-2 [&::-webkit-slider-thumb]:border-background
                [&::-webkit-slider-thumb]:shadow-md [&::-webkit-slider-thumb]:transition-transform
                [&::-webkit-slider-thumb]:hover:scale-110
                [&::-moz-range-thumb]:h-3.5 [&::-moz-range-thumb]:w-3.5
                [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border-2
                [&::-moz-range-thumb]:border-background [&::-moz-range-thumb]:shadow-md"
              style={{
                background: `linear-gradient(to right, ${color} ${fillPct}%, ${trackColor} ${fillPct}%)`,
                accentColor: color,
              }}
              aria-label={`${isSell ? 'Sell' : 'Buy'} ${leg.optionType} strike`}
            />
            <span
              className="w-14 shrink-0 rounded px-1.5 py-0.5 text-right text-[11px] font-semibold tabular-nums"
              style={{ color, backgroundColor: `${color}1a` }}
            >
              {currentStrike}
            </span>
          </div>
        )
      })}
    </div>
  )
}
