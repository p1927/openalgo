import { useState } from 'react'
import type {
  PlanImplementationStep,
  StockPlanOrder,
} from '@/components/strategy-builder/ExecutePlanWizard'
import type { PayoffOverTimeSample } from '@/components/strategy-builder/PayoffOverTimeChart'
import type { PositionsPanelProps } from '@/components/strategy-builder/PositionsPanel'
import type {
  PlanPrediction,
  PlanScenario,
  RankedStrategy,
} from '@/components/strategy-builder/ResearchContextPanel'

/** State for the currently loaded/executed trade plan on the Strategy
 * Builder page — everything populated by loading a saved plan or running
 * a prediction, consumed by the research panel, positions panel, and the
 * execute-plan wizard. Kept separate from StrategyBuilder.tsx's own
 * page-local UI state (active tab, strike-adjust baseline, etc). */
export function usePlanState() {
  const [loadedPlanName, setLoadedPlanName] = useState<string | null>(null)
  const [planCharges, setPlanCharges] = useState<PositionsPanelProps['planCharges']>(null)
  const [planNetPnl, setPlanNetPnl] = useState<PositionsPanelProps['planNetPnl']>(null)
  const [isChargesLoading, setIsChargesLoading] = useState(false)
  const [planImplementationSteps, setPlanImplementationSteps] = useState<PlanImplementationStep[]>(
    []
  )
  const [planPrediction, setPlanPrediction] = useState<PlanPrediction | null>(null)
  const [planRecommendedRationale, setPlanRecommendedRationale] = useState<string | null>(null)
  const [planRecommendedTier, setPlanRecommendedTier] = useState<string | null>(null)
  const [planRecommendedScore, setPlanRecommendedScore] = useState<number | null>(null)
  const [planRankedStrategies, setPlanRankedStrategies] = useState<RankedStrategy[]>([])
  const [planScenarios, setPlanScenarios] = useState<PlanScenario[]>([])
  const [planPayoffOverTime, setPlanPayoffOverTime] = useState<PayoffOverTimeSample[]>([])
  const [planKind, setPlanKind] = useState<'options' | 'stock'>('options')
  const [stockOrder, setStockOrder] = useState<StockPlanOrder | null>(null)
  const [executePlanOpen, setExecutePlanOpen] = useState(false)

  return {
    loadedPlanName,
    setLoadedPlanName,
    planCharges,
    setPlanCharges,
    planNetPnl,
    setPlanNetPnl,
    isChargesLoading,
    setIsChargesLoading,
    planImplementationSteps,
    setPlanImplementationSteps,
    planPrediction,
    setPlanPrediction,
    planRecommendedRationale,
    setPlanRecommendedRationale,
    planRecommendedTier,
    setPlanRecommendedTier,
    planRecommendedScore,
    setPlanRecommendedScore,
    planRankedStrategies,
    setPlanRankedStrategies,
    planScenarios,
    setPlanScenarios,
    planPayoffOverTime,
    setPlanPayoffOverTime,
    planKind,
    setPlanKind,
    stockOrder,
    setStockOrder,
    executePlanOpen,
    setExecutePlanOpen,
  }
}
