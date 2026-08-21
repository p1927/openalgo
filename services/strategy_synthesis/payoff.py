"""
Pure option-payoff math shared by the strategy-synthesis search. Kept
dependency-free (numpy only) and side-effect-free so it's trivially
unit-testable without a broker connection or the live option chain.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import numpy as np

OptionType = Literal["CE", "PE"]
Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class LegCandidate:
    """One tradeable strike/type the search may choose from, with its premium."""

    strike: float
    option_type: OptionType
    premium: float


@dataclass(frozen=True)
class SynthesizedLeg:
    """A leg chosen by the search — a `LegCandidate` plus the side picked."""

    strike: float
    option_type: OptionType
    side: Side
    premium: float
    qty: int = 1


@dataclass(frozen=True)
class RiskProfile:
    max_profit: float  # math.inf if unbounded upside (net long calls at the top strike)
    max_loss: float  # -math.inf if unbounded loss (net short calls at the top strike)
    breakevens: list[float]
    profit_loss_ratio: float  # see `risk_reward_ratio` for the sentinel convention


def intrinsic_value(prices: np.ndarray, strike: float, option_type: OptionType) -> np.ndarray:
    """At-expiry intrinsic value of one option across a price grid."""
    if option_type == "CE":
        return np.maximum(prices - strike, 0.0)
    return np.maximum(strike - prices, 0.0)


def leg_payoff(prices: np.ndarray, leg: SynthesizedLeg, lot_size: int = 1) -> np.ndarray:
    """At-expiry P&L of one leg across a price grid."""
    intrinsic = intrinsic_value(prices, leg.strike, leg.option_type)
    per_unit = (intrinsic - leg.premium) if leg.side == "BUY" else (leg.premium - intrinsic)
    return per_unit * leg.qty * lot_size


def combo_payoff(prices: np.ndarray, legs: list[SynthesizedLeg], lot_size: int = 1) -> np.ndarray:
    """At-expiry P&L of a full multi-leg combo across a price grid."""
    if not legs:
        return np.zeros_like(prices, dtype=float)
    return np.sum([leg_payoff(prices, leg, lot_size) for leg in legs], axis=0)


def risk_grid(prices: np.ndarray, legs: list[SynthesizedLeg]) -> np.ndarray:
    """
    Builds the price grid `evaluate_risk` should sample a combo's payoff on
    for max profit/loss, breakevens, and unbounded-detection — corrected
    against whatever grid the caller happened to pass in (typically built
    from a user-drawn price range, which has no reason to line up with the
    combo's own strikes).

    Three corrections over the raw `prices` grid:

    - Inserts every leg's own strike exactly. Intrinsic value is piecewise
      linear and only ever kinks *at* a leg's strike, so a combo's true max
      profit/loss always occurs exactly at one of its own strikes (or at
      the grid's edges) — never strictly between two sampled points. Only
      sampling the caller's grid can miss that peak by up to half the grid
      spacing — concretely, an ATM straddle's true max profit sits exactly
      at its shared strike, which a coarse or misaligned grid can straddle
      (sample on both sides of) without ever landing on it.
    - Appends two points beyond the combo's highest strike, so the
      right-edge unbounded check (see `evaluate_risk`) never samples its
      slope mid-ramp. Intrinsic value is perfectly linear beyond the
      highest strike involved in a combo; a spread whose short leg sits
      past the caller's grid edge would otherwise still look unbounded
      (the long leg alone is still climbing at that edge).
    - Inserts price 0.0. A put's intrinsic value (`max(strike - S, 0)`)
      keeps rising linearly all the way down to S=0 — unlike a call, it
      never kinks flat below its own strike. So a long put's true max
      profit (or a short put's true max loss) is realized exactly at S=0,
      not at whatever the caller's grid happened to sample lowest. Unlike
      the right edge, no "beyond" points are needed here — the underlying
      floor at 0 means there's nothing past it to slope-check.
    """
    if not legs:
        return prices
    points = {float(p) for p in prices}
    for leg in legs:
        points.add(float(leg.strike))
    points.add(0.0)
    max_strike = max(leg.strike for leg in legs)
    if len(prices) >= 2:
        step = float(prices[-1] - prices[-2])
    else:
        step = 0.0
    if step <= 0:
        step = max(max_strike * 0.01, 1.0)
    points.add(max_strike + step)
    points.add(max_strike + 2 * step)
    return np.array(sorted(points))


def _edge_slope(prices: np.ndarray, payoff: np.ndarray) -> float:
    """
    Slope of the payoff's last two grid points. Intrinsic value only ever
    kinks — never turns back down — beyond the highest strike in a combo,
    so the sign of this slope is enough to tell whether the combo's
    profit/loss keeps growing past the sampled range (unbounded) or has
    already flattened (bounded).
    """
    if len(prices) < 2:
        return 0.0
    dx = prices[-1] - prices[-2]
    if dx == 0:
        return 0.0
    return float((payoff[-1] - payoff[-2]) / dx)


def _find_breakevens(prices: np.ndarray, payoff: np.ndarray) -> list[float]:
    """
    Sign-crossing detection. Deliberately asymmetric (`y0 < 0 <= y1` /
    `y0 >= 0 > y1`, not a plain sign mismatch) so a crossing that lands
    exactly on a grid point — common with round-number strikes and a
    round-number grid — is attributed to exactly one adjacent pair instead
    of two: without the asymmetry, both the pair ending at that zero point
    and the pair starting from it would independently detect "a sign
    change" and the same breakeven would be emitted twice.
    """
    breakevens: list[float] = []
    for i in range(len(prices) - 1):
        y0, y1 = payoff[i], payoff[i + 1]
        crosses_up = y0 < 0 <= y1
        crosses_down = y0 >= 0 > y1
        if not (crosses_up or crosses_down):
            continue
        x0, x1 = prices[i], prices[i + 1]
        if y1 == y0:
            breakevens.append(float(x0))
            continue
        t = -y0 / (y1 - y0)
        breakevens.append(float(x0 + t * (x1 - x0)))
    return breakevens


def risk_reward_ratio(max_profit: float, max_loss: float) -> float:
    """
    Profit-to-risk ratio for ranking, with sentinel handling for the
    unbounded cases `max_profit`/`max_loss` can carry:

    - unlimited profit, bounded loss -> best possible; returns a large
      finite constant rather than actual infinity so it stays orderable
      and summable alongside finite scores.
    - unlimited loss -> worst possible; returns 0.
    - both bounded -> the plain ratio, floored at 0 (a max_profit of 0 or
      negative is not "rewarding" regardless of loss size).
    """
    if max_loss == -math.inf:
        return 0.0
    if max_profit == math.inf:
        return 50.0
    if max_loss == 0:
        return 50.0 if max_profit > 0 else 0.0
    return max(0.0, max_profit / abs(max_loss))


def evaluate_risk(prices: np.ndarray, payoff: np.ndarray) -> RiskProfile:
    """
    Max profit/loss, breakevens, and profit/loss ratio for a payoff curve.

    Only the *right* edge is checked for unboundedness: standard equity/
    index underlyings can't trade below 0, so a combo's downside loss is
    always capped by that floor — real unbounded loss only comes from net
    short calls, which show up as a negative slope at the high-price edge.
    """
    right_slope = _edge_slope(prices, payoff)
    max_profit = math.inf if right_slope > 1e-9 else float(np.max(payoff))
    max_loss = -math.inf if right_slope < -1e-9 else float(np.min(payoff))
    breakevens = _find_breakevens(prices, payoff)
    return RiskProfile(
        max_profit=max_profit,
        max_loss=max_loss,
        breakevens=breakevens,
        profit_loss_ratio=risk_reward_ratio(max_profit, max_loss),
    )
