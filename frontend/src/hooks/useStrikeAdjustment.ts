import { type Dispatch, type SetStateAction, useCallback, useMemo, useState } from 'react'
import type { ListedOptionChainResponse } from '@/lib/strategyContracts'
import type { StrategyLeg } from '@/lib/strategyMath'
import { normalizeExpiryCode } from '@/lib/templateResolution'
import { showToast } from '@/utils/toast'

export function cloneLegs(legs: StrategyLeg[]): StrategyLeg[] {
  return legs.map((l) => ({ ...l }))
}

/**
 * Nearest listed strike to a dragged/snapped price, not an exact match.
 *
 * This used to require an exact hit (`Math.abs(s.strike - strike) < 0.01`),
 * which silently dropped the update whenever it didn't land on a real row —
 * and it very often didn't: `strikeStep` (below) is a single global value
 * derived from the *tightest* gap anywhere in the chain, but many index
 * option chains widen their strike interval away from spot (e.g. 50pt near
 * ATM, 100-200pt further out). Snapping a chart-drag to that global step
 * then lands on a price no row actually has for anything but the tightest
 * region, and the leg just doesn't move — which reads as "dragging is
 * broken" even though the line itself moved fine. Finding the closest real
 * row instead means every drag lands on a tradable strike; `maxDistance`
 * only guards against snapping across a genuine gap in the loaded chain
 * (e.g. dragging past its edge) to some arbitrarily distant strike.
 */
export function findChainRow(
  chain: ListedOptionChainResponse['chain'] | null | undefined,
  strike: number,
  maxDistance = Infinity
) {
  if (!chain || chain.length === 0) return undefined
  let closest: ListedOptionChainResponse['chain'][number] | undefined
  let closestDistance = Infinity
  for (const row of chain) {
    const distance = Math.abs(row.strike - strike)
    if (distance < closestDistance) {
      closestDistance = distance
      closest = row
    }
  }
  return closest && closestDistance <= maxDistance ? closest : undefined
}

/**
 * Strike-drag-to-chart-row snapping, plus the "reset to loaded plan" baseline
 * comparison, for the Strategy Builder payoff chart. Kept separate from
 * StrategyBuilder.tsx's own state (plan state, tab/mode selection) since this
 * concern only touches `legs` and the active option chain.
 */
export function useStrikeAdjustment({
  legs,
  setLegs,
  chainData,
  strikeStep,
}: {
  legs: StrategyLeg[]
  setLegs: Dispatch<SetStateAction<StrategyLeg[]>>
  chainData: ListedOptionChainResponse | null
  strikeStep: number
}) {
  const [strikeAdjustBaseline, setStrikeAdjustBaseline] = useState<StrategyLeg[] | null>(null)

  const handleStrikeFromChart = useCallback(
    (legId: string, strike: number) => {
      setLegs((prev) =>
        prev.map((l) => {
          if (l.id !== legId || l.segment !== 'OPTION' || !l.optionType) return l
          // A drag can land a few rupees off whatever real strikes the chain
          // actually lists (see findChainRow's docstring) — snap to the
          // nearest one within a few steps rather than requiring an exact
          // hit, or requiring a hit at all along a widened part of the chain.
          const row = findChainRow(chainData?.chain, strike, strikeStep * 3)
          const side = l.optionType === 'CE' ? row?.ce : row?.pe
          // No listed contract near this strike (chain not loaded / dragged
          // past its edge) — skip rather than fabricate a symbol the backend
          // can't fill.
          if (!row || !side?.symbol) return l
          return {
            ...l,
            // Use the resolved row's own strike, not the raw dragged value —
            // `symbol` below is *for* that strike, and letting them diverge
            // silently mismatches the leg's payoff math against what it
            // would actually trade.
            strike: row.strike,
            expiry: normalizeExpiryCode(l.expiry),
            symbol: side.symbol,
            price: side.ltp && side.ltp > 0 ? side.ltp : l.price,
            lotSize: side.lotsize ?? l.lotSize,
          }
        })
      )
    },
    [chainData, strikeStep, setLegs]
  )

  const resetStrikesToBaseline = useCallback(() => {
    if (!strikeAdjustBaseline?.length) return
    setLegs(cloneLegs(strikeAdjustBaseline))
    showToast.success('Strikes reset to loaded plan')
  }, [strikeAdjustBaseline, setLegs])

  const canResetStrikes = useMemo(() => {
    if (!strikeAdjustBaseline?.length) return false
    return strikeAdjustBaseline.some((b) => {
      const cur = legs.find((l) => l.id === b.id)
      return (
        !cur ||
        cur.strike !== b.strike ||
        cur.price !== b.price ||
        cur.symbol !== b.symbol ||
        cur.optionType !== b.optionType
      )
    })
  }, [legs, strikeAdjustBaseline])

  return {
    strikeAdjustBaseline,
    setStrikeAdjustBaseline,
    handleStrikeFromChart,
    resetStrikesToBaseline,
    canResetStrikes,
  }
}
