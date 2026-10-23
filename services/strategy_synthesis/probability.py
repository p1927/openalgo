"""
Probability of finishing in profit at expiry, estimated from a payoff curve
and the underlying's risk-neutral lognormal price distribution — the same
model the frontend uses for its σ-band overlays
(`frontend/src/lib/strategyMath.ts`'s `lognormalPriceBand`), just integrated
against a payoff curve here instead of used to draw bands.

Kept side-effect-free (numpy + scipy.special.ndtr, both already declared dependencies),
matching the rest of this package.
"""

from __future__ import annotations

import math

import numpy as np
from scipy.special import ndtr


# Profiled (segment O2): the per-segment Python loop that called a scalar erf ~600x per scored combo was ~75% of a
# synthesis request (316 us/score, 43 s for C(100,3) combos). One vectorised ndtr over the grid gives the same CDF.
def _lognormal_cdf(prices: np.ndarray, mu: float, sigma: float) -> np.ndarray:
    """P(S_T <= price) for each price (0 for price <= 0), vectorised."""
    out = np.zeros(len(prices), dtype=float)
    pos = prices > 0
    out[pos] = ndtr((np.log(prices[pos]) - mu) / sigma)
    return out


def win_probability(
    prices: np.ndarray,
    payoff: np.ndarray,
    spot: float | None,
    iv: float | None,
    years: float | None,
) -> float:
    """
    P(payoff(S_T) > 0) under S_T ~ LogNormal(ln(spot) - 0.5*sigma_T**2, sigma_T),
    sigma_T = iv * sqrt(years) — the standard driftless risk-neutral forward
    price model (median forward = spot).

    Returns 0.5 (neutral — doesn't discriminate between candidates) when
    spot/iv/years aren't available, so callers that don't have live market
    data can still call this without it corrupting relative rankings.

    Integrates the lognormal PDF over the payoff's sampled price grid, mass
    by mass, using each segment's midpoint sign to decide profit/loss.
    Beyond the grid's edges, the payoff's sign is constant (intrinsic value
    never reverses slope again past the outermost strike involved), so the
    remaining tail probability mass is attributed to whichever side the
    nearest edge is on rather than requiring the grid to span to price
    extremes.
    """
    if not spot or spot <= 0 or not iv or iv <= 0 or not years or years <= 0 or len(prices) < 2:
        return 0.5

    sigma_t = iv * math.sqrt(years)
    if sigma_t <= 0:
        mid = payoff[len(payoff) // 2]
        return 1.0 if mid > 0 else 0.0
    mu = math.log(spot) - 0.5 * sigma_t**2

    cdf = _lognormal_cdf(np.asarray(prices, dtype=float), mu, sigma_t)
    segment_is_profit = (payoff[:-1] + payoff[1:]) / 2 > 0
    profit_mass = float(np.sum((cdf[1:] - cdf[:-1])[segment_is_profit]))

    if payoff[0] > 0:
        profit_mass += float(cdf[0])
    if payoff[-1] > 0:
        profit_mass += 1 - float(cdf[-1])

    return max(0.0, min(1.0, profit_mass))
