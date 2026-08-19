import { Loader2, RotateCcw, Sparkles, Trash2 } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { SynthesisResult } from '@/api/strategy-synthesis'
import { strategySynthesisApi } from '@/api/strategy-synthesis'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { showToast } from '@/utils/toast'
import type { LegDraft, ResolveLegContract } from './ManualLegBuilder'

export interface DrawTargetPayoffProps {
  underlying: string
  /** Derivative exchange (e.g. NFO) — matches what the option chain / synthesis API expect. */
  exchange: string
  expiry: string
  /** Real listed strikes for this underlying/expiry — the canvas snaps drawn points to these. */
  strikes: number[]
  resolveContract: ResolveLegContract
  onAdd: (draft: LegDraft) => void
}

interface DrawPoint {
  /** A real listed strike — not an arbitrary price. See the module comment below for why. */
  strike: number
  /** A row index into the P&L grid (0 = bottom/loss, ROW_COUNT-1 = top/profit) — not a
   * continuous height. See ROW_COUNT below for why the Y axis is discretized the same
   * way the X axis (strikes) already is. */
  row: number
}

const CANVAS_HEIGHT = 220
const MAX_POINTS = 12
const DEFAULT_MAX_LEGS = 3
// How many discrete P&L levels the canvas offers. The X axis was already
// restricted to real strikes (you can only place a point where an option
// actually exists) — the Y axis used to stay a free continuous height,
// which meant half the canvas still looked like "click anywhere" even
// though the other half didn't. Discretizing Y into a fixed row count and
// drawing every (strike x row) intersection as a small dot makes the whole
// canvas honestly show its full set of selectable points up front, rather
// than only revealing the X constraint after the first click. 15 rows is a
// middle ground: fine enough to shape a real curve, coarse enough that the
// dot matrix reads as texture rather than clutter next to 20-40 strike
// columns.
const ROW_COUNT = 15
// On-screen diameter of a placed point. Drawn in real measured pixels (see
// `canvasWidth` below) rather than viewBox units scaled by `preserveAspectRatio`
// — a square viewBox stretched to fill a non-square container (100x100 into
// a wide-and-short canvas) distorts circles into ellipses, the same bug this
// canvas used to have as the payoff chart's strike handles before that got a
// pixel-based fix. Matching the viewBox to the container's actual pixel size
// makes 1 viewBox unit = 1 real pixel in both axes, so a fixed-radius circle
// is a circle regardless of how wide the container ends up.
const POINT_DIAMETER_PX = 10
const GRID_DOT_DIAMETER_PX = 3

// Points snap to real listed strikes, not an arbitrary continuous price —
// two reasons, one cosmetic and one a correctness fix:
//
// - Every strike the search can actually choose from is already fixed by
//   the option chain, so letting the user "draw" at a price no option
//   exists near was always going to be approximated away regardless.
//   Showing the real grid up front (vertical guide lines, snap-to-nearest)
//   makes the interaction honest about what's actually selectable.
// - The backend's price grid for shape-matching is built directly from
//   `target_points`'s own x-range (`search.py`: `np.linspace(xs[0], xs[-1],
//   ...)`). This component used to send a fake 0-100 price span regardless
//   of the real underlying's price — for something like NIFTY (strikes
//   ~24,000+), every candidate's intrinsic value over a 0-100 "price" range
//   is degenerate (constantly zero or constantly max), so shape-matching
//   was silently comparing candidates outside their real domain entirely.
//   Sending real strikes as x fixes that: the price grid now actually
//   spans the region where the candidates' payoffs vary.
// Row index -> the -1..1 P&L value sent to the backend (row 0 = -1, top row = +1).
function rowToValue(row: number): number {
  return (row / (ROW_COUNT - 1)) * 2 - 1
}

function pointsToTargetPairs(points: DrawPoint[]): [number, number][] {
  return [...points].sort((a, b) => a.strike - b.strike).map((p) => [p.strike, rowToValue(p.row)])
}

function nearestStrike(strikes: number[], price: number): number {
  let closest = strikes[0]
  let closestDist = Math.abs(strikes[0] - price)
  for (const s of strikes) {
    const dist = Math.abs(s - price)
    if (dist < closestDist) {
      closestDist = dist
      closest = s
    }
  }
  return closest
}

function nearestRow(yFraction: number): number {
  // yFraction is 0 (bottom) .. 1 (top); row 0 is the bottom, ROW_COUNT-1 the top.
  return Math.min(ROW_COUNT - 1, Math.max(0, Math.round(yFraction * (ROW_COUNT - 1))))
}

export default function DrawTargetPayoff({
  underlying,
  exchange,
  expiry,
  strikes,
  resolveContract,
  onAdd,
}: DrawTargetPayoffProps) {
  const [points, setPoints] = useState<DrawPoint[]>([])
  const [maxLegs, setMaxLegs] = useState(DEFAULT_MAX_LEGS)
  const [loading, setLoading] = useState(false)
  const [results, setResults] = useState<SynthesisResult[] | null>(null)
  const [applyingIndex, setApplyingIndex] = useState<number | null>(null)
  const svgRef = useRef<SVGSVGElement>(null)
  const draggingIndexRef = useRef<number | null>(null)
  // Measures the actual rendered width so points/lines can be drawn in real
  // pixels (see POINT_DIAMETER_PX above) instead of a viewBox that gets
  // non-uniformly stretched to fill the container.
  const [canvasWidth, setCanvasWidth] = useState(600)
  useEffect(() => {
    const el = svgRef.current
    if (!el) return
    const observer = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width
      if (width && width > 0) setCanvasWidth(width)
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  const sortedStrikes = useMemo(() => [...strikes].sort((a, b) => a - b), [strikes])
  const strikeRange = useMemo<[number, number]>(() => {
    if (sortedStrikes.length === 0) return [0, 1]
    return [sortedStrikes[0], sortedStrikes[sortedStrikes.length - 1]]
  }, [sortedStrikes])
  const hasStrikes = sortedStrikes.length >= 2

  const xForStrike = (strike: number) => {
    const [lo, hi] = strikeRange
    const span = hi - lo || 1
    return ((strike - lo) / span) * canvasWidth
  }
  // Row 0 is the bottom of the canvas, ROW_COUNT-1 the top — inverted from
  // pixel-Y, which grows downward.
  const yForRow = (row: number) => (1 - row / (ROW_COUNT - 1)) * CANVAS_HEIGHT

  const canSearch = points.length >= 2 && Boolean(underlying) && Boolean(expiry) && !loading

  const sortedPoints = useMemo(() => [...points].sort((a, b) => a.strike - b.strike), [points])
  const pathD = useMemo(() => {
    if (sortedPoints.length < 2) return ''
    const [lo, hi] = strikeRange
    const span = hi - lo || 1
    const xFor = (strike: number) => ((strike - lo) / span) * canvasWidth
    const yFor = (row: number) => (1 - row / (ROW_COUNT - 1)) * CANVAS_HEIGHT
    return sortedPoints
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(p.strike)} ${yFor(p.row)}`)
      .join(' ')
  }, [sortedPoints, canvasWidth, strikeRange])

  const pointFromEvent = (e: { clientX: number; clientY: number }): DrawPoint | null => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect || !hasStrikes) return null
    const xFraction = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    const yFraction = Math.min(1, Math.max(0, 1 - (e.clientY - rect.top) / rect.height))
    const [lo, hi] = strikeRange
    const rawPrice = lo + xFraction * (hi - lo)
    return { strike: nearestStrike(sortedStrikes, rawPrice), row: nearestRow(yFraction) }
  }

  const handleCanvasClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (draggingIndexRef.current !== null) return
    if (!hasStrikes) return
    if (points.length >= MAX_POINTS) {
      showToast.warning(`You can place up to ${MAX_POINTS} points`)
      return
    }
    const point = pointFromEvent(e)
    if (!point) return
    setPoints((prev) => [...prev, point])
    setResults(null)
  }

  const handlePointPointerDown = (index: number) => (e: React.PointerEvent<SVGCircleElement>) => {
    e.stopPropagation()
    e.currentTarget.setPointerCapture(e.pointerId)
    draggingIndexRef.current = index
  }

  const handleSvgPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const index = draggingIndexRef.current
    if (index === null) return
    const point = pointFromEvent(e)
    if (!point) return
    setPoints((prev) => prev.map((p, i) => (i === index ? point : p)))
  }

  const endDrag = () => {
    if (draggingIndexRef.current !== null) {
      draggingIndexRef.current = null
      setResults(null)
    }
  }

  const removePoint = (index: number) => {
    setPoints((prev) => prev.filter((_, i) => i !== index))
    setResults(null)
  }

  const handleClear = () => {
    setPoints([])
    setResults(null)
  }

  const handleSearch = async () => {
    setLoading(true)
    setResults(null)
    try {
      const response = await strategySynthesisApi.synthesize({
        underlying,
        exchange,
        expiry_date: expiry,
        target_points: pointsToTargetPairs(points),
        max_legs: maxLegs,
      })
      if (response.status !== 'success' || !response.data) {
        showToast.error(response.message || 'Could not find matching legs for that shape')
        return
      }
      if (response.data.results.length === 0) {
        showToast.info('No leg combination matched that shape well — try a simpler curve')
        return
      }
      setResults(response.data.results)
    } catch {
      showToast.error('Failed to reach the strategy synthesis service')
    } finally {
      setLoading(false)
    }
  }

  const handleApply = async (result: SynthesisResult, index: number) => {
    setApplyingIndex(index)
    try {
      let appliedCount = 0
      for (const leg of result.legs) {
        const resolved = await resolveContract(expiry, 'OPTION', leg.strike, leg.option_type)
        if (!resolved) {
          showToast.warning(`Could not resolve ${leg.strike} ${leg.option_type} — skipped`)
          continue
        }
        onAdd({
          segment: 'OPTION',
          side: leg.side,
          expiry: resolved.expiry,
          strike: leg.strike,
          optionType: leg.option_type,
          lots: 1,
          price: resolved.marketPrice,
          iv: resolved.iv,
          symbol: resolved.symbol,
          exchange: resolved.exchange,
          expiryTs: resolved.expiryTs,
          lotSize: resolved.lotSize,
          tickSize: resolved.tickSize,
          contractValid: true,
          marketPrice: resolved.marketPrice,
          referenceUnderlying: resolved.referenceUnderlying,
          forwardPrice: resolved.forwardPrice,
          greeks: resolved.greeks,
        })
        appliedCount += 1
      }
      if (appliedCount > 0) {
        showToast.success(`Added ${appliedCount} leg${appliedCount > 1 ? 's' : ''} to the strategy`)
      }
    } finally {
      setApplyingIndex(null)
    }
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <p className="text-sm font-medium text-foreground">Draw the payoff shape you want</p>
        <p className="text-xs text-muted-foreground">
          {hasStrikes
            ? 'Click a strike column to place a point (drag to adjust, click a point to remove it). Only the shape matters — up, down, flat — not the exact height.'
            : 'Load an option chain to draw against its real strikes.'}
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border bg-muted/20">
        <svg
          ref={svgRef}
          viewBox={`0 0 ${canvasWidth} ${CANVAS_HEIGHT}`}
          className={hasStrikes ? 'w-full cursor-crosshair touch-none' : 'w-full touch-none'}
          style={{ height: CANVAS_HEIGHT }}
          onClick={handleCanvasClick}
          onPointerMove={handleSvgPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
        >
          {/* Strike grid — every column is a real, tradable strike; drawn
              points can only ever land on one of these. */}
          {sortedStrikes.map((s) => (
            <line
              key={s}
              x1={xForStrike(s)}
              y1={0}
              x2={xForStrike(s)}
              y2={CANVAS_HEIGHT}
              stroke="currentColor"
              strokeOpacity="0.07"
            />
          ))}
          <line
            x1={0}
            y1={CANVAS_HEIGHT / 2}
            x2={canvasWidth}
            y2={CANVAS_HEIGHT / 2}
            stroke="currentColor"
            strokeOpacity="0.15"
          />
          {hasStrikes && (
            <>
              <text x={4} y={CANVAS_HEIGHT - 6} fontSize="10" fill="currentColor" opacity="0.5">
                {strikeRange[0]}
              </text>
              <text
                x={canvasWidth - 4}
                y={CANVAS_HEIGHT - 6}
                fontSize="10"
                fill="currentColor"
                opacity="0.5"
                textAnchor="end"
              >
                {strikeRange[1]}
              </text>
            </>
          )}
          {pathD && <path d={pathD} fill="none" stroke="#10b981" strokeWidth="2" />}
          {sortedPoints.map((p) => {
            const originalIndex = points.indexOf(p)
            return (
              <circle
                key={originalIndex}
                cx={xForStrike(p.strike)}
                cy={(1 - p.y) * CANVAS_HEIGHT}
                r={POINT_DIAMETER_PX / 2}
                fill="#10b981"
                stroke="white"
                strokeWidth="1.5"
                className="cursor-grab active:cursor-grabbing"
                onPointerDown={handlePointPointerDown(originalIndex)}
                onDoubleClick={(e) => {
                  e.stopPropagation()
                  removePoint(originalIndex)
                }}
              />
            )
          })}
        </svg>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <div className="space-y-1">
          <Label htmlFor="synthesis-max-legs" className="text-xs">
            Max legs
          </Label>
          <Input
            id="synthesis-max-legs"
            type="number"
            min={1}
            max={6}
            value={maxLegs}
            onChange={(e) =>
              setMaxLegs(Math.min(6, Math.max(1, Number(e.target.value) || DEFAULT_MAX_LEGS)))
            }
            className="h-8 w-20"
          />
        </div>
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={handleClear}
          disabled={points.length === 0}
        >
          <Trash2 className="mr-1.5 h-3.5 w-3.5" /> Clear
        </Button>
        <Button type="button" size="sm" onClick={handleSearch} disabled={!canSearch}>
          {loading ? (
            <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
          ) : (
            <Sparkles className="mr-1.5 h-3.5 w-3.5" />
          )}
          Find matching legs
        </Button>
      </div>

      {results && (
        <div className="space-y-2">
          <p className="text-xs font-medium text-muted-foreground">
            {results.length} possibilit{results.length > 1 ? 'ies' : 'y'}, best match first
          </p>
          {results.map((result, index) => (
            <div key={index} className="rounded-lg border bg-card p-3">
              <div className="flex items-start justify-between gap-3">
                <div className="min-w-0 space-y-1">
                  {index === 0 && (
                    <span className="inline-block rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-semibold text-primary">
                      Best match
                    </span>
                  )}
                  <p className="truncate text-xs font-medium">
                    {result.legs
                      .map((l) => `${l.side === 'BUY' ? 'B' : 'S'} ${l.strike} ${l.option_type}`)
                      .join(' + ')}
                  </p>
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  className="h-7 shrink-0 gap-1 text-[11px]"
                  onClick={() => handleApply(result, index)}
                  disabled={applyingIndex !== null}
                >
                  {applyingIndex === index ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <RotateCcw className="h-3 w-3" />
                  )}
                  Apply
                </Button>
              </div>
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] sm:grid-cols-4">
                <div>
                  <dt className="text-muted-foreground">Shape fit</dt>
                  <dd className="font-semibold tabular-nums">
                    {Math.round(result.shape_score * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Win chance</dt>
                  <dd className="font-semibold tabular-nums">
                    {Math.round(result.win_probability * 100)}%
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Max profit</dt>
                  <dd className="font-semibold tabular-nums text-emerald-600 dark:text-emerald-400">
                    {result.max_profit === null ? 'Unlimited' : result.max_profit.toFixed(2)}
                  </dd>
                </div>
                <div>
                  <dt className="text-muted-foreground">Max loss</dt>
                  <dd className="font-semibold tabular-nums text-rose-600 dark:text-rose-400">
                    {result.max_loss === null ? 'Unlimited' : result.max_loss.toFixed(2)}
                  </dd>
                </div>
              </dl>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
