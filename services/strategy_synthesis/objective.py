"""
Scoring: how well a candidate leg combination matches a user-drawn target
payoff shape, blended with how favorable its absolute risk profile is and
how likely it is to finish in profit.

Ranking priority (defaults) — expressed in the order the user asked for:

    1. Win probability (highest weight) — "high chance of happening".
    2. Absolute max profit (second) — "want more profit, not legs that
       give very little".
    3. Absolute max loss (third) — "minimizing risk".
    4. Shape fit (smallest) — a *gate* (combo's shape must vaguely
       match what the user drew) rather than the primary axis; we no
       longer want a near-perfect shape match to outrank a clearly more
       profitable, higher-win-probability combo just because the user's
       drawn target happened to fit a tiny-payoff structure.

The shape is still in the formula (and `alpha <= 0` mirror-image combos
are still scored 0 outright) — the change is that it lost the dominant
weight. See the docstring in `search.synthesize` for the full rationale.
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
    score: float  # weighted combination, used for ranking


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
    profit_normalization: float = 50_000.0,
    spot: float | None = None,
    iv: float | None = None,
    years: float | None = None,
) -> ScoredCombo:
    """
    Blend four axes into a final ranking score:

        score = shape_weight * shape
              + profit_weight * profit_score
              + loss_weight * loss_score
              + win_prob_weight * win_probability

    The four weights are caller-supplied (defaults from `search.synthesize`
    are 0.10 / 0.25 / 0.15 / 0.50 — see the module docstring for the
    priority order). They should sum to 1.0; if they don't, the formula
    still works (the residual is implicitly given to `profit_score` only
    when weights don't add up, but the caller is expected to pass 1.0).
    """
    payoff = combo_payoff(prices, legs, lot_size)
    risk = evaluate_risk(prices, payoff)
    shape = _shape_score(target, payoff)
    profit_s = _profit_score(risk, profit_normalization)
    loss_s = _loss_score(risk, profit_normalization)
    win_p = win_probability(prices, payoff, spot, iv, years)
    total = (
        shape_weight * shape
        + profit_weight * profit_s
        + loss_weight * loss_s
        + win_prob_weight * win_p
    )
    return ScoredCombo(
        legs=legs,
        risk=risk,
        shape_score=shape,
        profit_score=profit_s,
        loss_score=loss_s,
        win_probability=win_p,
        score=total,
    )
