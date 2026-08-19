import { Loader2, RotateCcw, Sparkles, Trash2 } from 'lucide-react'
import { useMemo, useRef, useState } from 'react'
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
  resolveContract: ResolveLegContract
  onAdd: (draft: LegDraft) => void
}

interface DrawPoint {
  /** 0..1 fraction across the canvas width — converted to a price only when submitting. */
  x: number
  /** 0..1 fraction up the canvas height (0 = bottom/loss, 1 = top/profit). */
  y: number
}

const CANVAS_HEIGHT = 220
const MAX_POINTS = 12
const DEFAULT_MAX_LEGS = 3

// The drawn curve only needs to carry *shape* — the synthesis backend fits
// an optimal rescaling before comparing it to any candidate combo's real
// P&L (see services/strategy_synthesis/objective.py), so the absolute
// price/P&L numbers behind these fractions are never shown to or chosen by
// the user. A fixed placeholder price span is enough to turn "click here"
// into a monotonic x ordering for the target curve.
const PRICE_SPAN = 100

function pointsToTargetPairs(points: DrawPoint[]): [number, number][] {
  return [...points].sort((a, b) => a.x - b.x).map((p) => [p.x * PRICE_SPAN, p.y * 2 - 1])
}

export default function DrawTargetPayoff({
  underlying,
  exchange,
  expiry,
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

  const canSearch = points.length >= 2 && Boolean(underlying) && Boolean(expiry) && !loading

  const sortedPoints = useMemo(() => [...points].sort((a, b) => a.x - b.x), [points])
  const pathD = useMemo(() => {
    if (sortedPoints.length < 2) return ''
    return sortedPoints
      .map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x * 100} ${(1 - p.y) * 100}`)
      .join(' ')
  }, [sortedPoints])

  const fractionFromEvent = (e: { clientX: number; clientY: number }) => {
    const rect = svgRef.current?.getBoundingClientRect()
    if (!rect) return null
    const x = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width))
    const y = Math.min(1, Math.max(0, 1 - (e.clientY - rect.top) / rect.height))
    return { x, y }
  }

  const handleCanvasClick = (e: React.MouseEvent<SVGSVGElement>) => {
    if (draggingIndexRef.current !== null) return
    if (points.length >= MAX_POINTS) {
      showToast.warning(`You can place up to ${MAX_POINTS} points`)
      return
    }
    const point = fractionFromEvent(e)
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
    const point = fractionFromEvent(e)
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
          Click to place points (drag to adjust, click a point to remove it). Only the shape matters
          — up, down, flat — not the exact numbers.
        </p>
      </div>

      <div className="overflow-hidden rounded-lg border bg-muted/20">
        <svg
          ref={svgRef}
          viewBox="0 0 100 100"
          preserveAspectRatio="none"
          className="w-full cursor-crosshair touch-none"
          style={{ height: CANVAS_HEIGHT }}
          onClick={handleCanvasClick}
          onPointerMove={handleSvgPointerMove}
          onPointerUp={endDrag}
          onPointerLeave={endDrag}
        >
          <line
            x1="0"
            y1="50"
            x2="100"
            y2="50"
            stroke="currentColor"
            strokeOpacity="0.15"
            vectorEffect="non-scaling-stroke"
          />
          {pathD && (
            <path
              d={pathD}
              fill="none"
              stroke="#10b981"
              strokeWidth="2"
              vectorEffect="non-scaling-stroke"
            />
          )}
          {sortedPoints.map((p) => {
            const originalIndex = points.indexOf(p)
            return (
              <circle
                key={originalIndex}
                cx={p.x * 100}
                cy={(1 - p.y) * 100}
                r="4"
                fill="#10b981"
                stroke="white"
                strokeWidth="1.5"
                vectorEffect="non-scaling-stroke"
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
            {results.length} match{results.length > 1 ? 'es' : ''}, best shape fit first
          </p>
          {results.map((result, index) => (
            <div
              key={index}
              className="flex items-center justify-between gap-3 rounded-md border bg-card px-3 py-2 text-xs"
            >
              <div className="min-w-0 space-y-0.5">
                <p className="truncate font-medium">
                  {result.legs
                    .map((l) => `${l.side === 'BUY' ? 'B' : 'S'} ${l.strike} ${l.option_type}`)
                    .join(' + ')}
                </p>
                <p className="text-muted-foreground">
                  Shape fit {Math.round(result.shape_score * 100)}% · Max profit{' '}
                  {result.max_profit === null ? 'Unlimited' : result.max_profit.toFixed(2)} · Max
                  loss {result.max_loss === null ? 'Unlimited' : result.max_loss.toFixed(2)}
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
          ))}
        </div>
      )}
    </div>
  )
}
