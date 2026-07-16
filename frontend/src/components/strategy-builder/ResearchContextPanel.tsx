import { ChevronDown, Sparkles } from 'lucide-react'
import { useState } from 'react'
import { cn } from '@/lib/utils'

export interface PlanPrediction {
  view?: string | null
  iv_regime?: string | null
  confidence?: number | null
  expected_move_pct?: number | null
  pcr?: number | null
  source?: string | null
}

export interface RankedStrategy {
  name?: string
  tier?: string | null
  score?: number | null
  pop?: number | null
  rationale?: string | null
}

export interface PlanScenario {
  name?: string
  probability?: number | null
  trigger?: string | null
  strategy_hint?: string | null
}

export interface ResearchContextPanelProps {
  underlying: string
  prediction?: PlanPrediction | null
  recommendedName?: string | null
  recommendedRationale?: string | null
  recommendedTier?: string | null
  recommendedScore?: number | null
  rankedStrategies?: RankedStrategy[]
  scenarios?: PlanScenario[]
}

function fmtPct(v: number | null | undefined, digits = 0): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return '—'
  return `${(v * (v <= 1 ? 100 : 1)).toFixed(digits)}%`
}

function tierTone(tier: string | null | undefined): string {
  const t = (tier || '').toLowerCase()
  if (t.includes('strong') || t.includes('best')) {
    return 'border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400'
  }
  if (t.includes('consider')) {
    return 'border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400'
  }
  return 'border-muted bg-muted/40 text-muted-foreground'
}

export function ResearchContextPanel({
  underlying,
  prediction,
  recommendedName,
  recommendedRationale,
  recommendedTier,
  recommendedScore,
  rankedStrategies = [],
  scenarios = [],
}: ResearchContextPanelProps) {
  const [open, setOpen] = useState(true)
  const hasPrediction =
    prediction &&
    (prediction.view ||
      prediction.iv_regime ||
      prediction.confidence !== undefined ||
      prediction.expected_move_pct !== undefined)
  const hasRanked = rankedStrategies.length > 0
  const hasScenarios = scenarios.length > 0

  if (!hasPrediction && !recommendedRationale && !hasRanked && !hasScenarios) {
    return null
  }

  return (
    <div className="overflow-hidden rounded-xl border border-emerald-500/20 bg-gradient-to-br from-emerald-500/5 via-card to-card shadow-sm">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left transition hover:bg-muted/20"
      >
        <div className="flex min-w-0 items-center gap-2">
          <div className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-emerald-500/15 text-emerald-600 dark:text-emerald-400">
            <Sparkles className="h-3.5 w-3.5" />
          </div>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-semibold">Research context · {underlying}</h3>
            <p className="truncate text-[11px] text-muted-foreground">
              {recommendedName
                ? `Recommended: ${recommendedName.replace(/_/g, ' ')}`
                : 'From trade-stack hub'}
            </p>
          </div>
        </div>
        <ChevronDown
          className={cn('h-4 w-4 shrink-0 text-muted-foreground transition', open && 'rotate-180')}
        />
      </button>

      {open && (
        <div className="space-y-4 border-t px-4 py-4 text-[12px]">
          {(recommendedRationale || recommendedTier || recommendedScore !== undefined) && (
            <div className="space-y-1.5">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                  Recommendation
                </span>
                {recommendedTier && (
                  <span
                    className={cn(
                      'rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider',
                      tierTone(recommendedTier)
                    )}
                  >
                    {recommendedTier}
                  </span>
                )}
                {recommendedScore !== undefined && recommendedScore !== null && (
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[10px] font-semibold tabular-nums text-muted-foreground">
                    Score {(recommendedScore * (recommendedScore <= 1 ? 100 : 1)).toFixed(0)}
                  </span>
                )}
              </div>
              {recommendedRationale && (
                <p className="leading-relaxed text-foreground">{recommendedRationale}</p>
              )}
            </div>
          )}

          {hasPrediction && (
            <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
              {prediction?.view && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    View
                  </dt>
                  <dd className="mt-0.5 font-medium capitalize">
                    {String(prediction.view).replace(/_/g, ' ')}
                  </dd>
                </div>
              )}
              {prediction?.iv_regime && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    IV regime
                  </dt>
                  <dd className="mt-0.5 font-medium capitalize">{prediction.iv_regime}</dd>
                </div>
              )}
              {prediction?.confidence !== undefined && prediction.confidence !== null && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Confidence
                  </dt>
                  <dd className="mt-0.5 font-medium tabular-nums">
                    {fmtPct(prediction.confidence, 0)}
                  </dd>
                </div>
              )}
              {prediction?.expected_move_pct !== undefined &&
                prediction.expected_move_pct !== null && (
                  <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                    <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Exp. move
                    </dt>
                    <dd className="mt-0.5 font-medium tabular-nums">
                      ±{Number(prediction.expected_move_pct).toFixed(1)}%
                    </dd>
                  </div>
                )}
              {prediction?.pcr !== undefined && prediction.pcr !== null && (
                <div className="rounded-lg border bg-background/60 px-2.5 py-2">
                  <dt className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                    PCR
                  </dt>
                  <dd className="mt-0.5 font-medium tabular-nums">
                    {Number(prediction.pcr).toFixed(2)}
                  </dd>
                </div>
              )}
            </div>
          )}

          {hasRanked && (
            <div className="space-y-2">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Ranked alternatives
              </div>
              <ul className="space-y-1.5">
                {rankedStrategies.slice(0, 4).map((s, i) => (
                  <li
                    key={`${s.name}-${i}`}
                    className={cn(
                      'rounded-lg border px-3 py-2',
                      s.name === recommendedName && 'border-emerald-500/30 bg-emerald-500/5'
                    )}
                  >
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-semibold capitalize">
                        {(s.name || 'strategy').replace(/_/g, ' ')}
                      </span>
                      {s.tier && (
                        <span
                          className={cn(
                            'rounded-full border px-1.5 py-px text-[9px] font-semibold uppercase',
                            tierTone(s.tier)
                          )}
                        >
                          {s.tier}
                        </span>
                      )}
                      {s.score !== undefined && s.score !== null && (
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          {(s.score * (s.score <= 1 ? 100 : 1)).toFixed(0)} pts
                        </span>
                      )}
                      {s.pop !== undefined && s.pop !== null && (
                        <span className="text-[10px] tabular-nums text-muted-foreground">
                          PoP {fmtPct(s.pop, 0)}
                        </span>
                      )}
                    </div>
                    {s.rationale && (
                      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
                        {s.rationale}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {hasScenarios && (
            <div className="space-y-2">
              <div className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                Scenarios
              </div>
              <ul className="space-y-1.5">
                {scenarios.slice(0, 4).map((sc, i) => (
                  <li key={`${sc.name}-${i}`} className="rounded-lg border bg-background/40 px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-medium capitalize">
                        {(sc.name || `scenario ${i + 1}`).replace(/_/g, ' ')}
                      </span>
                      {sc.probability !== undefined && sc.probability !== null && (
                        <span className="rounded bg-muted px-1.5 py-px text-[10px] font-semibold tabular-nums text-muted-foreground">
                          {fmtPct(sc.probability, 0)} likely
                        </span>
                      )}
                      {sc.strategy_hint && (
                        <span className="text-[10px] text-muted-foreground">
                          → {sc.strategy_hint.replace(/_/g, ' ')}
                        </span>
                      )}
                    </div>
                    {sc.trigger && (
                      <p className="mt-1 text-[11px] leading-snug text-muted-foreground">
                        {sc.trigger}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
