import { Clock, RotateCcw, Sliders, TrendingUp, Waves } from 'lucide-react'
import type { ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface SliderRowProps {
  icon: ReactNode
  label: string
  accessibleLabel: string
  sublabel: string
  value: number
  min: number
  max: number
  step: number
  formatter: (v: number) => string
  onChange: (v: number) => void
  disabled?: boolean
  accent?: 'pink' | 'violet' | 'blue'
  centered?: boolean
}

function SliderRow({
  icon,
  label,
  accessibleLabel,
  sublabel,
  value,
  min,
  max,
  step,
  formatter,
  onChange,
  disabled = false,
  accent = 'violet',
  centered = false,
}: SliderRowProps) {
  // Note: keep the native range appearance so the browser paints the thumb
  // — we tint only the track (accent-*) and rely on the user agent for thumb
  // rendering, which gives us a visible, high-contrast circle in every
  // theme (light, dark, and our analyzer theme). A fully custom thumb via
  // ::webkit-slider-thumb { appearance:none } was invisible on light mode
  // because Tailwind utilities inside pseudo-selectors aren't composed the
  // way the `accent` utility is.
  const accentTrack = {
    pink: 'accent-pink-500',
    violet: 'accent-violet-500',
    blue: 'accent-blue-500',
  }[accent]

  const accentBg = {
    pink: 'from-pink-500/15 to-pink-500/0 text-pink-600 dark:text-pink-400',
    violet: 'from-violet-500/15 to-violet-500/0 text-violet-600 dark:text-violet-400',
    blue: 'from-blue-500/15 to-blue-500/0 text-blue-600 dark:text-blue-400',
  }[accent]

  const accentValue = {
    pink: 'text-pink-600 dark:text-pink-400',
    violet: 'text-violet-600 dark:text-violet-400',
    blue: 'text-blue-600 dark:text-blue-400',
  }[accent]

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2.5">
        <div
          className={cn(
            'inline-flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br',
            accentBg
          )}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="text-xs font-semibold leading-none">{label}</div>
          <div className="mt-0.5 text-[10px] text-muted-foreground">{sublabel}</div>
        </div>
        <span className={cn('text-sm font-semibold tabular-nums', accentValue)}>
          {formatter(value)}
        </span>
      </div>
      <div className="relative px-1">
        <input
          type="range"
          aria-label={accessibleLabel}
          aria-valuetext={formatter(value)}
          min={min}
          max={max}
          step={step}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(Number(e.target.value))}
          className={cn(
            'h-2 w-full cursor-pointer rounded-full bg-muted outline-none',
            accentTrack,
            disabled && 'cursor-not-allowed opacity-50'
          )}
        />
        {/* Center tick for bipolar sliders */}
        {centered && (
          <span
            className="pointer-events-none absolute top-1/2 h-2 w-[2px] -translate-y-1/2 rounded-full bg-border"
            style={{ left: `${((0 - min) / (max - min)) * 100}%` }}
          />
        )}
        <div className="mt-1 flex justify-between text-[9px] font-medium tabular-nums text-muted-foreground/70">
          <span>{formatter(min)}</span>
          {centered && <span className="opacity-60">0</span>}
          <span>{formatter(max)}</span>
        </div>
      </div>
    </div>
  )
}

export interface SimulatorsProps {
  spotShiftPct: number
  ivShiftPct: number
  daysElapsed: number
  maxDays: number
  onSpotShiftChange: (v: number) => void
  onIvShiftChange: (v: number) => void
  onDaysElapsedChange: (v: number) => void
  onReset: () => void
  /**
   * `'card'` (default) keeps the original stacked layout with header +
   * bordered card chrome — used when Simulators is the only thing in a
   * right-hand slot.
   *
   * `'compact'` strips the card chrome and lays the three sliders out in a
   * single horizontal strip — used when Simulators sits inline beneath the
   * strike-rail inside the payoff chart card and must read as one
   * continuation of the chart, not a separate panel.
   */
  variant?: 'card' | 'compact'
}

interface SimulatorsInternals {
  spotShiftPct: number
  ivShiftPct: number
  daysElapsed: number
  maxShiftedDays: number
  hasTimeRemaining: boolean
  isSubDay: boolean
  timeSliderValue: number
  timeSliderMax: number
  timeStep: number
  isDirty: boolean
  sliderValueToDays: (value: number) => number
  onSpotShiftChange: (v: number) => void
  onIvShiftChange: (v: number) => void
  onDaysElapsedChange: (v: number) => void
}

function useSimulatorsInternals({
  spotShiftPct,
  ivShiftPct,
  daysElapsed,
  maxDays,
  onSpotShiftChange,
  onIvShiftChange,
  onDaysElapsedChange,
}: Pick<
  SimulatorsProps,
  | 'spotShiftPct'
  | 'ivShiftPct'
  | 'daysElapsed'
  | 'maxDays'
  | 'onSpotShiftChange'
  | 'onIvShiftChange'
  | 'onDaysElapsedChange'
>): SimulatorsInternals {
  const maxShiftedDays = Math.max(0, maxDays)
  const hasTimeRemaining = maxShiftedDays > 0
  const isSubDay = maxShiftedDays < 1
  const maxHourlyStep = 1 / 24
  const timePartitions =
    isSubDay && maxShiftedDays > 0 ? Math.ceil(maxShiftedDays / maxHourlyStep) : 0
  const timeSliderValue =
    isSubDay && timePartitions > 0
      ? Math.round((Math.min(daysElapsed, maxShiftedDays) / maxShiftedDays) * timePartitions)
      : daysElapsed
  const timeSliderMax = isSubDay ? timePartitions : maxShiftedDays
  const timeStep = isSubDay ? 1 : 0.25
  const sliderValueToDays = (value: number) => {
    if (!isSubDay || timePartitions === 0) return isSubDay ? 0 : value
    if (value >= timePartitions) return maxShiftedDays
    return (value * maxShiftedDays) / timePartitions
  }
  const isDirty = spotShiftPct !== 0 || ivShiftPct !== 0 || daysElapsed !== 0
  return {
    spotShiftPct,
    ivShiftPct,
    daysElapsed,
    maxShiftedDays,
    hasTimeRemaining,
    isSubDay,
    timeSliderValue,
    timeSliderMax,
    timeStep,
    isDirty,
    sliderValueToDays,
    onSpotShiftChange,
    onIvShiftChange,
    onDaysElapsedChange,
  }
}

function formatTimeLabel(internals: SimulatorsInternals, value: number): string {
  const days = internals.sliderValueToDays(value)
  const totalSeconds = Math.max(0, Math.round(days * 24 * 60 * 60))
  const totalHours = Math.round((totalSeconds / (60 * 60)) * 10) / 10
  if (internals.isSubDay) {
    if (totalSeconds < 60) return `+${totalSeconds}s`
    if (totalSeconds < 60 * 60) {
      const minutes = Math.floor(totalSeconds / 60)
      const seconds = totalSeconds % 60
      return seconds === 0 ? `+${minutes}m` : `+${minutes}m ${seconds}s`
    }
    return `+${totalHours.toLocaleString()}h`
  }
  const wholeDays = Math.floor(totalHours / 24)
  const hours = Math.round((totalHours - wholeDays * 24) * 10) / 10
  if (hours === 0) return `+${wholeDays}d`
  if (wholeDays === 0) return `+${hours.toLocaleString()}h`
  return `+${wholeDays}d ${hours.toLocaleString()}h`
}

function SimulatorsCard({
  internals,
  onReset,
}: {
  internals: SimulatorsInternals
  onReset: () => void
}) {
  const formatTime = (value: number) => formatTimeLabel(internals, value)
  return (
    <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
      <div className="flex items-center justify-between border-b bg-gradient-to-r from-muted/30 to-transparent px-4 py-3">
        <div className="flex items-center gap-2">
          <div className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-gradient-to-br from-amber-500/15 to-pink-500/15 text-amber-600 dark:text-amber-400">
            <Sliders className="h-3.5 w-3.5" />
          </div>
          <div>
            <h3 className="text-sm font-semibold leading-none">What-If Simulator</h3>
            <p className="mt-1 text-[10px] text-muted-foreground">
              Stress-test the strategy across spot, IV and time
            </p>
          </div>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={onReset}
          disabled={!internals.isDirty}
          className="h-7 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-40"
        >
          <RotateCcw className="mr-1 h-3 w-3" /> Reset
        </Button>
      </div>
      <div className="space-y-5 px-4 py-4">
        <SliderRow
          icon={<TrendingUp className="h-3.5 w-3.5" />}
          label="Spot Price"
          accessibleLabel="Spot price shift"
          sublabel="Move underlying up or down"
          value={internals.spotShiftPct}
          min={-10}
          max={10}
          step={0.1}
          accent="pink"
          centered
          formatter={(v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
          onChange={internals.onSpotShiftChange}
        />
        <SliderRow
          icon={<Waves className="h-3.5 w-3.5" />}
          label="Implied Volatility"
          accessibleLabel="Implied volatility shift"
          sublabel="Vol expansion or crush"
          value={internals.ivShiftPct}
          min={-50}
          max={50}
          step={1}
          accent="violet"
          centered
          formatter={(v) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
          onChange={internals.onIvShiftChange}
        />
        <SliderRow
          icon={<Clock className="h-3.5 w-3.5" />}
          label={internals.isSubDay ? 'Hours Forward' : 'Days Forward'}
          accessibleLabel="Time forward"
          sublabel="Advance time toward expiry"
          value={internals.timeSliderValue}
          min={0}
          max={internals.timeSliderMax}
          step={internals.timeStep}
          disabled={!internals.hasTimeRemaining}
          accent="blue"
          formatter={(value) => formatTime(value)}
          onChange={(value) => internals.onDaysElapsedChange(internals.sliderValueToDays(value))}
        />
      </div>
    </div>
  )
}

interface CompactSlotProps {
  icon: ReactNode
  label: string
  accessibleLabel: string
  value: number
  min: number
  max: number
  step: number
  formatter: (v: number) => string
  onChange: (v: number) => void
  disabled?: boolean
  accent: 'pink' | 'violet' | 'blue'
  centered?: boolean
}

/**
 * Single-line control used by `SimulatorsCompact`: `[icon] [label] [slider…] [value]`.
 * Kept narrow so three of them line up in the compact strip without forcing
 * the chart card to scroll; wraps to a new row on narrow widths.
 */
function CompactSlot({
  icon,
  label,
  accessibleLabel,
  value,
  min,
  max,
  step,
  formatter,
  onChange,
  disabled = false,
  accent,
  centered = false,
}: CompactSlotProps) {
  const accentTrack = {
    pink: 'accent-pink-500',
    violet: 'accent-violet-500',
    blue: 'accent-blue-500',
  }[accent]
  const accentValue = {
    pink: 'text-pink-600 dark:text-pink-400',
    violet: 'text-violet-600 dark:text-violet-400',
    blue: 'text-blue-600 dark:text-blue-400',
  }[accent]
  return (
    <div className="flex min-w-0 flex-1 basis-40 items-center gap-1.5">
      <span
        className={cn('inline-flex h-5 w-5 shrink-0 items-center justify-center', accentValue)}
      >
        {icon}
      </span>
      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </span>
      <input
        type="range"
        aria-label={accessibleLabel}
        aria-valuetext={formatter(value)}
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(Number(e.target.value))}
        className={cn(
          'h-1.5 min-w-0 flex-1 cursor-pointer rounded-full bg-muted/70 outline-none',
          accentTrack,
          disabled && 'cursor-not-allowed opacity-40'
        )}
      />
      <span
        className={cn(
          'shrink-0 tabular-nums text-[10px] font-semibold',
          accentValue,
          centered && 'min-w-[3.2rem] text-right'
        )}
      >
        {formatter(value)}
      </span>
    </div>
  )
}

/**
 * Compact, inline variant — no card chrome, no header. Three sliders share a
 * single horizontal strip so the block reads as a continuation of the chart
 * card above it, not a separate panel that has to be re-scanned for.
 */
function SimulatorsCompact({
  internals,
  onReset,
}: {
  internals: SimulatorsInternals
  onReset: () => void
}) {
  const formatTime = (value: number) => formatTimeLabel(internals, value)
  return (
    <div
      data-testid="simulators-compact"
      className="flex flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-dashed bg-muted/20 px-3 py-2"
    >
      <div className="flex shrink-0 items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        <Sliders className="h-3 w-3" />
        What-if
      </div>
      <CompactSlot
        icon={<TrendingUp className="h-3 w-3" />}
        label="Spot"
        value={internals.spotShiftPct}
        min={-10}
        max={10}
        step={0.1}
        accent="pink"
        centered
        formatter={(v) => `${v > 0 ? '+' : ''}${v.toFixed(1)}%`}
        onChange={internals.onSpotShiftChange}
        accessibleLabel="Spot price shift"
      />
      <CompactSlot
        icon={<Waves className="h-3 w-3" />}
        label="IV"
        value={internals.ivShiftPct}
        min={-50}
        max={50}
        step={1}
        accent="violet"
        centered
        formatter={(v) => `${v > 0 ? '+' : ''}${v.toFixed(0)}%`}
        onChange={internals.onIvShiftChange}
        accessibleLabel="Implied volatility shift"
      />
      <CompactSlot
        icon={<Clock className="h-3 w-3" />}
        label={internals.isSubDay ? 'Hours' : 'Days'}
        value={internals.timeSliderValue}
        min={0}
        max={internals.timeSliderMax}
        step={internals.timeStep}
        disabled={!internals.hasTimeRemaining}
        accent="blue"
        formatter={(value) => formatTime(value)}
        onChange={(value) => internals.onDaysElapsedChange(internals.sliderValueToDays(value))}
        accessibleLabel="Time forward"
      />
      <Button
        type="button"
        variant="ghost"
        size="sm"
        onClick={onReset}
        disabled={!internals.isDirty}
        aria-label="Reset what-if simulators"
        className="ml-auto h-6 shrink-0 px-2 text-[10px] text-muted-foreground hover:text-foreground disabled:opacity-30"
      >
        <RotateCcw className="mr-1 h-2.5 w-2.5" /> Reset
      </Button>
    </div>
  )
}

export function Simulators({
  spotShiftPct,
  ivShiftPct,
  daysElapsed,
  maxDays,
  onSpotShiftChange,
  onIvShiftChange,
  onDaysElapsedChange,
  onReset,
  variant = 'card',
}: SimulatorsProps) {
  const internals = useSimulatorsInternals({
    spotShiftPct,
    ivShiftPct,
    daysElapsed,
    maxDays,
    onSpotShiftChange,
    onIvShiftChange,
    onDaysElapsedChange,
  })
  if (variant === 'compact') {
    return <SimulatorsCompact internals={internals} onReset={onReset} />
  }
  return <SimulatorsCard internals={internals} onReset={onReset} />
}
