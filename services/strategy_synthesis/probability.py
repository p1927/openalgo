"""
Probability of finishing in profit at expiry, estimated from a payoff curve
and the underlying's risk-neutral lognormal price distribution — the same
model the frontend uses for its σ-band overlays
(`frontend/src/lib/strategyMath.ts`'s `lognormalPriceBand`), just integrated
against a payoff curve here instead of used to draw bands.

Kept dependency-free (stdlib `math` only, no scipy) and side-effect-free,
matching the rest of this package.
"""

from __future__ import annotations

import math

import numpy as np


def _lognormal_cdf(price: float, mu: float, sigma: float) -> float:
    if price <= 0:
        return 0.0
    z = (math.log(price) - mu) / sigma
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


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

    profit_mass = 0.0
    for i in range(len(prices) - 1):
        segment_is_profit = (payoff[i] + payoff[i + 1]) / 2 > 0
        if not segment_is_profit:
            continue
        profit_mass += _lognormal_cdf(float(prices[i + 1]), mu, sigma_t) - _lognormal_cdf(
            float(prices[i]), mu, sigma_t
        )

    if payoff[0] > 0:
        profit_mass += _lognormal_cdf(float(prices[0]), mu, sigma_t)
    if payoff[-1] > 0:
        profit_mass += 1 - _lognormal_cdf(float(prices[-1]), mu, sigma_t)

    return max(0.0, min(1.0, profit_mass))
