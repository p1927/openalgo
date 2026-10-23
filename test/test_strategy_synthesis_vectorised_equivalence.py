"""The vectorised synthesis math (segment O2 profile: scoring was ~316 us/score, 75% in a Python erf loop)
must equal the original scalar implementations, kept below as the reference."""

import math

import numpy as np
import pytest

from services.strategy_synthesis import payoff as pf
from services.strategy_synthesis import probability as pr
from services.strategy_synthesis.payoff import SynthesizedLeg


def _ref_cdf(price, mu, sigma):
    if price <= 0:
        return 0.0
    return 0.5 * (1 + math.erf(((math.log(price) - mu) / sigma) / math.sqrt(2)))


def _ref_win(prices, payoff, spot, iv, years):
    sigma_t = iv * math.sqrt(years)
    mu = math.log(spot) - 0.5 * sigma_t**2
    mass = 0.0
    for i in range(len(prices) - 1):
        if (payoff[i] + payoff[i + 1]) / 2 > 0:
            mass += _ref_cdf(float(prices[i + 1]), mu, sigma_t) - _ref_cdf(float(prices[i]), mu, sigma_t)
    if payoff[0] > 0:
        mass += _ref_cdf(float(prices[0]), mu, sigma_t)
    if payoff[-1] > 0:
        mass += 1 - _ref_cdf(float(prices[-1]), mu, sigma_t)
    return max(0.0, min(1.0, mass))


def _ref_breakevens(prices, payoff):
    out = []
    for i in range(len(prices) - 1):
        y0, y1 = payoff[i], payoff[i + 1]
        if not ((y0 < 0 <= y1) or (y0 >= 0 > y1)):
            continue
        x0, x1 = prices[i], prices[i + 1]
        if y1 == y0:
            out.append(float(x0))
            continue
        out.append(float(x0 + (-y0 / (y1 - y0)) * (x1 - x0)))
    return out


def _ref_risk_grid(prices, legs):
    points = {float(p) for p in prices}
    for leg in legs:
        points.add(float(leg.strike))
    points.add(0.0)
    ms = max(leg.strike for leg in legs)
    step = float(prices[-1] - prices[-2]) if len(prices) >= 2 else 0.0
    if step <= 0:
        step = max(ms * 0.01, 1.0)
    points.add(ms + step)
    points.add(ms + 2 * step)
    return np.array(sorted(points))


def _random_legs(rng, n):
    return [
        SynthesizedLeg(
            strike=float(rng.choice(np.arange(23000, 26000, 50))),
            option_type=str(rng.choice(["CE", "PE"])),
            side=str(rng.choice(["BUY", "SELL"])),
            premium=float(rng.uniform(5, 300)),
        )
        for _ in range(n)
    ]


@pytest.mark.parametrize("seed", range(40))
def test_vectorised_math_matches_scalar_reference(seed):
    rng = np.random.default_rng(seed)
    legs = _random_legs(rng, int(rng.integers(1, 5)))
    prices = np.linspace(23500, 25000, 120)
    grid = pf.risk_grid(prices, legs)
    assert np.array_equal(grid, _ref_risk_grid(prices, legs))

    payoff = pf.combo_payoff(grid, legs, 75)
    ref_payoff = np.sum([pf.leg_payoff(grid, leg, 75) for leg in legs], axis=0)
    assert np.array_equal(payoff, ref_payoff)

    assert pf._find_breakevens(grid, payoff) == pytest.approx(_ref_breakevens(grid, payoff), abs=1e-9)

    spot, iv, years = 24300.0, float(rng.uniform(0.08, 0.4)), float(rng.uniform(1, 60)) / 365
    span = 6 * iv * math.sqrt(years)
    wp = np.unique(np.concatenate([grid, np.linspace(spot * math.exp(-span), spot * math.exp(span), 241)]))
    wpay = pf.combo_payoff(wp, legs, 75)
    assert pr.win_probability(wp, wpay, spot, iv, years) == pytest.approx(_ref_win(wp, wpay, spot, iv, years), abs=1e-12)


def test_breakeven_exact_on_grid_point_and_flat_segment():
    prices = np.array([0.0, 1.0, 2.0, 3.0])
    assert pf._find_breakevens(prices, np.array([-1.0, 0.0, 1.0, 1.0])) == [0.5 + 0.5]
    assert pf._find_breakevens(prices, np.array([1.0, 1.0, 1.0, 1.0])) == []
    assert pf._find_breakevens(prices, np.array([1.0, -1.0, 1.0, -1.0])) == [0.5, 1.5, 2.5]


def test_nonpositive_price_gets_zero_cdf():
    out = pr._lognormal_cdf(np.array([-5.0, 0.0, 100.0]), math.log(100.0), 0.2)
    assert out[0] == 0.0 and out[1] == 0.0 and 0.0 < out[2] < 1.0
