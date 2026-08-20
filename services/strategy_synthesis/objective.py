"""
Scoring: how well a candidate leg combination matches a user-drawn target
payoff shape, blended with how favorable its absolute risk profile is and
how likely it is to finish in profit, minus a fee-driven leg-count
penalty.

Ranking priority (defaults) — expressed in the order the user asked for:

    1. Shape fit (largest weight) — the user drew a curve on the canvas
       to express *what kind* of payoff they want. Within the universe
       of combos whose other factors are competitive, the one that
       matches the drawn shape most closely is the right answer.

    2. Win probability — "high chance of happening".
    3. Absolute max profit — "want more profit, not legs that give
       very little".
    4. Absolute max loss — "minimizing risk".
    5. Leg-count penalty — same shape, same win/profit/risk → fewer
       legs wins, because each leg adds brokerage + STT + GST +
       exchange fees. India options are charged per leg, so a 4-leg
       iron condor pays roughly 4x the per-trade fees of a 1-leg call.

The leg penalty is applied as an additive term per extra leg beyond
the first (a 1-leg combo has zero penalty; a 2-leg combo gets one unit
of `leg_count_penalty`; etc.). It's small enough that a multi-leg
combo with clearly better shape/win/profit/risk still wins over a
1-leg alternative, but large enough that two near-tied combos get
broken by leg count.

The previous version of this module had a near-perfect shape match on
a tiny-payoff structure outrank a clearly more profitable,
higher-win-probability combo. The fix moved shape from dominant to
*one of four* axes. The user then asked for shape to be *re-emphasized*
as the tie-breaker among holy-grail candidates — which is what this
ordering does: shape is now the largest single weight, but profit
(0.25), loss (0.20), and win prob (0.30) collectively outweigh shape
(0.25), so a small-profit spread that happens to fit the drawn shape
perfectly still loses to a long call with materially more profit and
a comparable win rate. The leg-count penalty further nudges ties
toward fewer legs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .payoff import RiskProfile, SynthesizedLeg, combo_payoff, evaluate_risk
from .probability import win_probability


@dataclass(frozen=True)
class ScoredCombo:
    legs: list[SynthesizedLeg]
    risk: RiskProfile
    shape_score: float  # 0..1, higher = closer shape match
    profit_score: float  # 0..1, higher = larger absolute max profit
    loss_score: float  # 0..1, higher = smaller absolute max loss
    win_probability: float  # 0..1, P(profit at expiry); 0.5 (neutral) if spot/iv/years unknown
    leg_count_penalty: float  # additive subtractor applied to the base score (>= 0)
    score: float  # final ranking score (base blend minus penalty)


def _shape_score(target: np.ndarray, candidate: np.ndarray) -> float:
    """
    Shape match invariant to the target's absolute scale — a user drawing a
    curve on screen has no way to know what P&L numbers are realistic for a
    given underlying, so what matters is direction and relative proportions,
    not literal rupee values.

    Finds the best-fit scale `alpha` (least squares) mapping the candidate
    curve onto the target, then scores by the residual after that
    rescaling. A negative or ~zero `alpha` means the best "fit" is a flat
    or mirror-image curve (e.g. matching a drawn profit hump with a loss
    hump) — that's not a shape match, so it scores 0 rather than being
    rewarded for accidentally correlating.
    """
    denom = float(np.dot(candidate, candidate))
    if denom < 1e-9:
        return 0.0
    alpha = float(np.dot(candidate, target) / denom)
    if alpha <= 0:
        return 0.0
    residual = target - alpha * candidate
    target_spread = float(np.std(target)) + 1e-9
    normalized_rmse = float(np.sqrt(np.mean(residual**2))) / target_spread
    return 1.0 / (1.0 + normalized_rmse)


def _profit_score(risk: RiskProfile, normalization: float) -> float:
    """
    Absolute max profit, normalized to [0, 1] against the chain's
    `normalization` benchmark (computed by the caller from the candidate
    premiums — see `search.synthesize`).

    - Unlimited upside (naked long call past the highest strike) -> 1.0
    - Zero or negative -> 0.0
    - Anything in between saturates at 1.0 once it crosses the benchmark

    This is the score that gives "want more profit, not legs that give
    very little" teeth: two combos with identical shape and identical
    win probability get ranked by absolute reward, not by their shape's
    P/L ratio.
    """
    if normalization <= 0:
        return 0.0
    if risk.max_profit == math.inf:
        return 1.0
    if risk.max_profit <= 0 or math.isnan(risk.max_profit):
        return 0.0
    return min(risk.max_profit / normalization, 1.0)


def _loss_score(risk: RiskProfile, normalization: float) -> float:
    """
    Absolute max loss, inverted and normalized to [0, 1] (higher = better,
    i.e. smaller loss). `normalization` is the same benchmark used by
    `_profit_score`.

    - Unlimited downside (naked short call past the highest strike) -> 0.0
    - Zero or positive max loss (can't lose, e.g. fully hedged or the
      payoff stays non-negative across the grid) -> 1.0
    - Anything in between linearly interpolates

    Note: `risk.max_loss` is a non-positive number in practice (it's the
    min of the payoff curve), so `risk.max_loss / normalization` is
    negative or zero; we add it to 1.0 to flip the sign.
    """
    if normalization <= 0:
        return 0.0
    if risk.max_loss == -math.inf:
        return 0.0
    if risk.max_loss >= 0:
        return 1.0
    # risk.max_loss is negative here. |loss| / normalization in [0, 1] is
    # 1 + (risk.max_loss / normalization).
    return max(0.0, min(1.0, 1.0 + risk.max_loss / normalization))


def score_combo(
    prices: np.ndarray,
    target: np.ndarray,
    legs: list[SynthesizedLeg],
    lot_size: int,
    shape_weight: float,
    profit_weight: float,
    loss_weight: float,
    win_prob_weight: float,
    leg_count_penalty: float = 0.0,
    profit_normalization: float = 50_000.0,
    spot: float | None = None,
    iv: float | None = None,
    years: float | None = None,
) -> ScoredCombo:
    """
    Blend four axes into a base score, then subtract a leg-count penalty:

        base   = shape_weight * shape
               + profit_weight * profit_score
               + loss_weight * loss_score
               + win_prob_weight * win_probability
        score  = max(0.0, base - leg_count_penalty * (len(legs) - 1))

    The penalty is per extra leg beyond the first (a 1-leg combo has no
    penalty; a 2-leg combo gets one unit; a 4-leg gets three units). The
    multiplier is supplied by the caller (`search.synthesize` defaults
    to 0.03, i.e. a 3% per-leg fee haircut). The penalty is small enough
    to be a tie-breaker, not a veto — a 4-leg combo with clearly better
    shape / win prob / profit / loss still wins over a 1-leg alternative
    with marginal benefits.

    The final score is clamped at 0 so a long penalty against an
    otherwise-mediocre combo doesn't produce a negative ranking score
    (which would interact oddly with sort orders downstream).
    """
    payoff = combo_payoff(prices, legs, lot_size)
    risk = evaluate_risk(prices, payoff)
    shape = _shape_score(target, payoff)
    profit_s = _profit_score(risk, profit_normalization)
    loss_s = _loss_score(risk, profit_normalization)
    win_p = win_probability(prices, payoff, spot, iv, years)
    base = (
        shape_weight * shape
        + profit_weight * profit_s
        + loss_weight * loss_s
        + win_prob_weight * win_p
    )
    penalty = max(0.0, leg_count_penalty) * max(0, len(legs) - 1)
    total = max(0.0, base - penalty)
    return ScoredCombo(
        legs=legs,
        risk=risk,
        shape_score=shape,
        profit_score=profit_s,
        loss_score=loss_s,
        win_probability=win_p,
        leg_count_penalty=penalty,
        score=total,
    )
