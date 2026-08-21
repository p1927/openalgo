"""
Shared shape definitions for strategy-synthesis verification against the real
recorded NIFTY chain (`test/fixtures/real_option_chain_nifty.json`,
snapshot 2026-08-21 13:38 IST — spot 24233.15, expiry 08-Sep-2026).

Used by both `test_strategy_synthesis_shapes_real_chain.py` (numeric
assertions) and the visual verification script that plots target vs.
recovered payoff curves for a human to eyeball. Kept as a single source of
truth so the shapes tested and the shapes plotted never drift apart.

Not a test file itself (no `test_` prefix) — pytest won't collect it.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from services.strategy_synthesis import LegCandidate, SynthesizedLeg, combo_payoff

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_option_chain_nifty.json"
LOT_SIZE = 75  # real NIFTY lot size

with open(FIXTURE_PATH) as _f:
    CHAIN = json.load(_f)

SPOT = CHAIN["underlying_ltp"]
YEARS = CHAIN["years_to_expiry"]
PRICE_LO, PRICE_HI = 23800.0, 24750.0

# The 24650 CE row is a real but stale/thin print (open_interest=0) —
# wildly out of line with its neighbors (340.0 vs ~84/59). Excluded from
# the candidate pool used to build/recover known shapes so a test isn't
# accidentally asserting on illiquid noise.
STALE_STRIKE_TYPE = (24650.0, "CE")


def premium(strike: float, option_type: str) -> float:
    row = next(r for r in CHAIN["chain"] if r["strike"] == strike)
    return row["ce" if option_type == "CE" else "pe"]["ltp"]


def candidates(exclude_stale: bool = True) -> list[LegCandidate]:
    out = []
    for row in CHAIN["chain"]:
        for option_type, key in (("CE", "ce"), ("PE", "pe")):
            if exclude_stale and (row["strike"], option_type) == STALE_STRIKE_TYPE:
                continue
            ltp = row[key]["ltp"]
            if ltp > 0:
                out.append(LegCandidate(strike=row["strike"], option_type=option_type, premium=ltp))
    return out


def candidates_in_window(strikes: set[float], exclude_stale: bool = True) -> list[LegCandidate]:
    return [c for c in candidates(exclude_stale) if c.strike in strikes]


def atm_strike() -> float:
    return min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))["strike"]


def atm_iv() -> float:
    row = min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))
    ivs = [iv for iv in (row["ce"]["implied_volatility"], row["pe"]["implied_volatility"]) if iv]
    return (sum(ivs) / len(ivs)) / 100


def leg(strike: float, option_type: str, side: str) -> SynthesizedLeg:
    return SynthesizedLeg(
        strike=strike, option_type=option_type, side=side, premium=premium(strike, option_type)
    )


def target_from_legs(
    legs: list[SynthesizedLeg], lo: float = PRICE_LO, hi: float = PRICE_HI, n: int = 150
):
    prices = np.linspace(lo, hi, n)
    payoff = combo_payoff(prices, legs, LOT_SIZE)
    return list(zip(prices.tolist(), payoff.tolist(), strict=True)), prices, payoff


@dataclass(frozen=True)
class Shape:
    name: str
    legs: list[SynthesizedLeg]
    max_legs: int
    expected_max_profit: float  # math.inf allowed
    expected_max_loss: float  # -math.inf allowed
    candidate_window: set[float] | None = None  # None = full chain
    search_weights: dict = field(
        default_factory=lambda: {
            "shape_weight": 0.85,
            "profit_weight": 0.05,
            "loss_weight": 0.05,
            "win_prob_weight": 0.05,
        }
    )


def _net_premium(long_leg: SynthesizedLeg, short_leg: SynthesizedLeg) -> float:
    return long_leg.premium - short_leg.premium


def build_shapes() -> list[Shape]:
    atm = atm_strike()
    shapes: list[Shape] = []

    # -- Naked singles --------------------------------------------------
    k = 24200.0
    p = premium(k, "CE")
    shapes.append(Shape("Long Call 24200", [leg(k, "CE", "BUY")], 1, math.inf, -p * LOT_SIZE))

    k = 24300.0
    p = premium(k, "PE")
    shapes.append(
        Shape("Long Put 24300", [leg(k, "PE", "BUY")], 1, (k - p) * LOT_SIZE, -p * LOT_SIZE)
    )

    k = 24400.0
    p = premium(k, "CE")
    shapes.append(Shape("Short Call 24400", [leg(k, "CE", "SELL")], 1, p * LOT_SIZE, -math.inf))

    k = 24100.0
    p = premium(k, "PE")
    shapes.append(
        Shape("Short Put 24100", [leg(k, "PE", "SELL")], 1, p * LOT_SIZE, (p - k) * LOT_SIZE)
    )

    # -- Vertical spreads (varying width => varying P&L scale) ----------
    buy_k, sell_k = 24200.0, 24300.0  # narrow debit call spread
    buy_leg, sell_leg = leg(buy_k, "CE", "BUY"), leg(sell_k, "CE", "SELL")
    net = _net_premium(buy_leg, sell_leg)
    shapes.append(
        Shape(
            "Bull Call Spread (narrow, 24200/24300)",
            [buy_leg, sell_leg],
            2,
            ((sell_k - buy_k) - net) * LOT_SIZE,
            -net * LOT_SIZE,
        )
    )

    buy_k, sell_k = 24000.0, 24700.0  # wide debit call spread
    buy_leg, sell_leg = leg(buy_k, "CE", "BUY"), leg(sell_k, "CE", "SELL")
    net = _net_premium(buy_leg, sell_leg)
    shapes.append(
        Shape(
            "Bull Call Spread (wide, 24000/24700)",
            [buy_leg, sell_leg],
            2,
            ((sell_k - buy_k) - net) * LOT_SIZE,
            -net * LOT_SIZE,
            # A wide spread's shape is close enough to a naked long call
            # (unlimited profit) that the default profit-axis weight (5%)
            # is enough to tip a shape-adjacent alternative ahead of the
            # true spread — correct behavior of the objective function
            # (see test_shape_does_not_dominate_when_other_factors_differ_materially
            # in test_strategy_synthesis.py), but it means recovering the
            # *exact* wide spread here needs a more shape-dominant weight
            # than the default narrow-spread cases use.
            search_weights={
                "shape_weight": 0.97,
                "profit_weight": 0.01,
                "loss_weight": 0.01,
                "win_prob_weight": 0.01,
            },
        )
    )

    sell_k, buy_k = 24200.0, 24400.0  # bear call spread (credit)
    sell_leg, buy_leg = leg(sell_k, "CE", "SELL"), leg(buy_k, "CE", "BUY")
    net_credit = sell_leg.premium - buy_leg.premium
    shapes.append(
        Shape(
            "Bear Call Spread (credit, 24200/24400)",
            [sell_leg, buy_leg],
            2,
            net_credit * LOT_SIZE,
            -((buy_k - sell_k) - net_credit) * LOT_SIZE,
        )
    )

    sell_k, buy_k = 24200.0, 24000.0  # bull put spread (credit)
    sell_leg, buy_leg = leg(sell_k, "PE", "SELL"), leg(buy_k, "PE", "BUY")
    net_credit = sell_leg.premium - buy_leg.premium
    shapes.append(
        Shape(
            "Bull Put Spread (credit, 24000/24200)",
            [sell_leg, buy_leg],
            2,
            net_credit * LOT_SIZE,
            -((sell_k - buy_k) - net_credit) * LOT_SIZE,
        )
    )

    buy_k, sell_k = 24300.0, 24000.0  # bear put spread (debit)
    buy_leg, sell_leg = leg(buy_k, "PE", "BUY"), leg(sell_k, "PE", "SELL")
    net = _net_premium(buy_leg, sell_leg)
    shapes.append(
        Shape(
            "Bear Put Spread (24000/24300)",
            [buy_leg, sell_leg],
            2,
            ((buy_k - sell_k) - net) * LOT_SIZE,
            -net * LOT_SIZE,
        )
    )

    # -- Straddle / strangle ---------------------------------------------
    ce_leg, pe_leg = leg(atm, "CE", "BUY"), leg(atm, "PE", "BUY")
    total_premium = ce_leg.premium + pe_leg.premium
    shapes.append(
        Shape(
            f"Long Straddle (ATM {atm:.0f})",
            [ce_leg, pe_leg],
            2,
            math.inf,
            -total_premium * LOT_SIZE,
        )
    )

    put_k, call_k = 23900.0, 24600.0
    pe_leg, ce_leg = leg(put_k, "PE", "SELL"), leg(call_k, "CE", "SELL")
    total_premium = pe_leg.premium + ce_leg.premium
    shapes.append(
        Shape(
            f"Short Strangle ({put_k:.0f}P/{call_k:.0f}C)",
            [pe_leg, ce_leg],
            2,
            total_premium * LOT_SIZE,
            -math.inf,
        )
    )

    put_k, call_k = 24000.0, 24500.0
    pe_leg, ce_leg = leg(put_k, "PE", "BUY"), leg(call_k, "CE", "BUY")
    total_premium = pe_leg.premium + ce_leg.premium
    shapes.append(
        Shape(
            f"Long Strangle ({put_k:.0f}P/{call_k:.0f}C)",
            [pe_leg, ce_leg],
            2,
            math.inf,
            -total_premium * LOT_SIZE,
        )
    )

    # -- Iron condors (4 legs, two widths => two P&L scales) -------------
    def iron_condor(put_buy, put_sell, call_sell, call_buy, name):
        legs = [
            leg(put_buy, "PE", "BUY"),
            leg(put_sell, "PE", "SELL"),
            leg(call_sell, "CE", "SELL"),
            leg(call_buy, "CE", "BUY"),
        ]
        net_credit = (legs[1].premium - legs[0].premium) + (legs[2].premium - legs[3].premium)
        put_wing = put_sell - put_buy
        call_wing = call_buy - call_sell
        max_loss = -(max(put_wing, call_wing) - net_credit) * LOT_SIZE
        window = {put_buy, put_sell, call_sell, call_buy} | {
            23900.0,
            24000.0,
            24100.0,
            24200.0,
            24300.0,
            24400.0,
            24500.0,
            24550.0,
            24600.0,
            24700.0,
        }
        return Shape(
            name,
            legs,
            4,
            net_credit * LOT_SIZE,
            max_loss,
            candidate_window=window,
            search_weights={
                "shape_weight": 0.9,
                "profit_weight": 0.03,
                "loss_weight": 0.04,
                "win_prob_weight": 0.03,
            },
        )

    shapes.append(
        iron_condor(23900.0, 24100.0, 24400.0, 24600.0, "Iron Condor (narrow, 200-wide wings)")
    )
    shapes.append(
        iron_condor(23800.0, 24000.0, 24500.0, 24700.0, "Iron Condor (wide, 200/200 further out)")
    )

    return shapes
