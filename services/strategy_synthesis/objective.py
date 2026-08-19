"""
Scoring: how well a candidate leg combination matches a user-drawn target
payoff shape, blended with how favorable its risk/reward profile is.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .payoff import RiskProfile, SynthesizedLeg, combo_payoff, evaluate_risk


@dataclass(frozen=True)
class ScoredCombo:
    legs: list[SynthesizedLeg]
    risk: RiskProfile
    shape_score: float  # 0..1, higher = closer shape match
    risk_score: float  # 0..1, higher = better risk/reward
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


def _risk_score(risk: RiskProfile) -> float:
    """Profit/loss ratio, clamped and normalized to 0..1 for blending with shape_score."""
    return max(0.0, min(risk.profit_loss_ratio, 5.0)) / 5.0


def score_combo(
    prices: np.ndarray,
    target: np.ndarray,
    legs: list[SynthesizedLeg],
    lot_size: int,
    shape_weight: float,
    risk_weight: float,
) -> ScoredCombo:
    payoff = combo_payoff(prices, legs, lot_size)
    risk = evaluate_risk(prices, payoff)
    shape = _shape_score(target, payoff)
    risk_s = _risk_score(risk)
    total = shape_weight * shape + risk_weight * risk_s
    return ScoredCombo(legs=legs, risk=risk, shape_score=shape, risk_score=risk_s, score=total)
