"""
Strategy-synthesis math verified against a REAL recorded NIFTY option chain
(see `test/fixtures/real_option_chain_nifty.json` for provenance: real 1-minute
close LTPs from the repo's own recorded replay dataset, spot from the same
snapshot, IV solved from those real LTPs via the same Black-76 solver the live
Draw Target feature uses).

Two things this file is specifically here to catch that the synthetic-data
tests in `test_strategy_synthesis.py` cannot:

1. Real strike spacing/premium curves are irregular (skew, one stale/thin
   print at 24650 CE) — a formula that only works on evenly-spaced synthetic
   premiums could still be subtly wrong here.
2. The `risk_grid` fix (see `payoff.py`) only matters when a combo's own
   strikes extend past the user-drawn price range — real strikes at real
   spacing are what actually exercises that path end to end.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from services.strategy_synthesis import (
    LegCandidate,
    SynthesizedLeg,
    combo_payoff,
    evaluate_risk,
    risk_grid,
    synthesize,
    win_probability,
)
from services.strategy_synthesis.objective import score_combo

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "real_option_chain_nifty.json"
LOT_SIZE = 75  # real NIFTY lot size, used wherever rupee magnitude matters

# The 24650 CE row is a real but stale/thin print (open_interest=0) flagged
# during extraction — wildly out of line with its neighbors (340.0 vs ~84/59
# on either side). Excluded when building "known combo" fixtures so a test
# isn't accidentally asserting on illiquid noise; kept in the general
# candidate pool for the end-to-end test since the search must not choke on
# a real chain's real imperfections.
STALE_STRIKE_TYPE = (24650.0, "CE")


def _load_chain() -> dict:
    with open(FIXTURE_PATH) as f:
        return json.load(f)


CHAIN = _load_chain()
SPOT = CHAIN["underlying_ltp"]
YEARS = CHAIN["years_to_expiry"]


def _premium(strike: float, option_type: str) -> float:
    row = next(r for r in CHAIN["chain"] if r["strike"] == strike)
    key = "ce" if option_type == "CE" else "pe"
    return row[key]["ltp"]


def _candidates(exclude_stale: bool = False) -> list[LegCandidate]:
    out = []
    for row in CHAIN["chain"]:
        for option_type, key in (("CE", "ce"), ("PE", "pe")):
            if exclude_stale and (row["strike"], option_type) == STALE_STRIKE_TYPE:
                continue
            ltp = row[key]["ltp"]
            if ltp > 0:
                out.append(LegCandidate(strike=row["strike"], option_type=option_type, premium=ltp))
    return out


def _atm_iv() -> float:
    atm_row = min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))
    ivs = [atm_row["ce"]["implied_volatility"], atm_row["pe"]["implied_volatility"]]
    ivs = [iv for iv in ivs if iv]
    return (sum(ivs) / len(ivs)) / 100


ATM_IV = _atm_iv()


# --------------------------------------------------------------------------
# 1. Hand-verified payoff math at real strikes/premiums
# --------------------------------------------------------------------------


def test_leg_payoff_matches_hand_calculation_at_real_strike():
    strike, premium = 24200.0, _premium(24200.0, "CE")
    assert premium == pytest.approx(267.2)
    leg = SynthesizedLeg(strike=strike, option_type="CE", side="BUY", premium=premium, qty=1)
    prices = np.array([23800.0, 24200.0, 24500.0])
    payoff = combo_payoff(prices, [leg], LOT_SIZE)

    # Below strike: pure loss of premium * lot_size.
    assert payoff[0] == pytest.approx(-premium * LOT_SIZE)
    # At strike: intrinsic is 0, same as below.
    assert payoff[1] == pytest.approx(-premium * LOT_SIZE)
    # Above strike: intrinsic (300) minus premium, times lot size.
    assert payoff[2] == pytest.approx((300.0 - premium) * LOT_SIZE)


def test_short_leg_is_mirror_image_of_long_leg_at_real_strike():
    strike, premium = 24300.0, _premium(24300.0, "PE")
    prices = np.linspace(23800.0, 24750.0, 50)
    long_leg = SynthesizedLeg(strike=strike, option_type="PE", side="BUY", premium=premium)
    short_leg = SynthesizedLeg(strike=strike, option_type="PE", side="SELL", premium=premium)
    long_payoff = combo_payoff(prices, [long_leg], LOT_SIZE)
    short_payoff = combo_payoff(prices, [short_leg], LOT_SIZE)
    assert np.allclose(long_payoff, -short_payoff)


# --------------------------------------------------------------------------
# 2. Known combos built from real strikes/premiums must be recoverable
# --------------------------------------------------------------------------


def _target_from_legs(legs: list[SynthesizedLeg], lo: float, hi: float, n: int = 150):
    prices = np.linspace(lo, hi, n)
    payoff = combo_payoff(prices, legs, LOT_SIZE)
    return list(zip(prices.tolist(), payoff.tolist(), strict=True)), prices, payoff


def test_bull_call_spread_recovered_from_real_chain():
    buy_k, sell_k = 24100.0, 24400.0
    true_legs = [
        SynthesizedLeg(strike=buy_k, option_type="CE", side="BUY", premium=_premium(buy_k, "CE")),
        SynthesizedLeg(
            strike=sell_k, option_type="CE", side="SELL", premium=_premium(sell_k, "CE")
        ),
    ]
    target_points, prices, target_payoff = _target_from_legs(true_legs, 23800.0, 24750.0)

    results = synthesize(
        target_points=target_points,
        candidates=_candidates(exclude_stale=True),
        max_legs=2,
        min_legs=2,
        lot_size=LOT_SIZE,
        top_n=3,
        shape_weight=0.85,
        profit_weight=0.05,
        loss_weight=0.05,
        win_prob_weight=0.05,
    )

    assert results
    top = results[0]
    found = {(leg.strike, leg.option_type, leg.side) for leg in top.legs}
    assert found == {(buy_k, "CE", "BUY"), (sell_k, "CE", "SELL")}
    assert top.shape_score > 0.99

    net_premium = _premium(buy_k, "CE") - _premium(sell_k, "CE")
    expected_max_profit = ((sell_k - buy_k) - net_premium) * LOT_SIZE
    expected_max_loss = -net_premium * LOT_SIZE
    assert top.risk.max_profit == pytest.approx(expected_max_profit, rel=1e-6)
    assert top.risk.max_loss == pytest.approx(expected_max_loss, rel=1e-6)


def test_bear_put_spread_recovered_from_real_chain():
    buy_k, sell_k = 24400.0, 24100.0
    true_legs = [
        SynthesizedLeg(strike=buy_k, option_type="PE", side="BUY", premium=_premium(buy_k, "PE")),
        SynthesizedLeg(
            strike=sell_k, option_type="PE", side="SELL", premium=_premium(sell_k, "PE")
        ),
    ]
    target_points, _, _ = _target_from_legs(true_legs, 23800.0, 24750.0)

    results = synthesize(
        target_points=target_points,
        candidates=_candidates(exclude_stale=True),
        max_legs=2,
        min_legs=2,
        lot_size=LOT_SIZE,
        top_n=3,
        shape_weight=0.85,
        profit_weight=0.05,
        loss_weight=0.05,
        win_prob_weight=0.05,
    )

    assert results
    top = results[0]
    found = {(leg.strike, leg.option_type, leg.side) for leg in top.legs}
    assert found == {(buy_k, "PE", "BUY"), (sell_k, "PE", "SELL")}
    assert top.shape_score > 0.99

    net_premium = _premium(buy_k, "PE") - _premium(sell_k, "PE")
    expected_max_profit = ((buy_k - sell_k) - net_premium) * LOT_SIZE
    expected_max_loss = -net_premium * LOT_SIZE
    assert top.risk.max_profit == pytest.approx(expected_max_profit, rel=1e-6)
    assert top.risk.max_loss == pytest.approx(expected_max_loss, rel=1e-6)


def test_short_straddle_recovered_at_real_atm_strike():
    atm_row = min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))
    k = atm_row["strike"]
    true_legs = [
        SynthesizedLeg(strike=k, option_type="CE", side="SELL", premium=_premium(k, "CE")),
        SynthesizedLeg(strike=k, option_type="PE", side="SELL", premium=_premium(k, "PE")),
    ]
    target_points, _, _ = _target_from_legs(true_legs, 23800.0, 24750.0)

    results = synthesize(
        target_points=target_points,
        candidates=_candidates(exclude_stale=True),
        max_legs=2,
        min_legs=2,
        lot_size=LOT_SIZE,
        top_n=3,
        shape_weight=0.85,
        profit_weight=0.05,
        loss_weight=0.05,
        win_prob_weight=0.05,
    )

    assert results
    top = results[0]
    found = {(leg.strike, leg.option_type, leg.side) for leg in top.legs}
    assert found == {(k, "CE", "SELL"), (k, "PE", "SELL")}
    # A short straddle has unlimited loss on the call side (right edge keeps
    # falling past the highest sampled strike).
    assert top.risk.max_loss == -math.inf
    total_premium = _premium(k, "CE") + _premium(k, "PE")
    assert top.risk.max_profit == pytest.approx(total_premium * LOT_SIZE, rel=1e-6)


def test_iron_condor_shape_recovered_from_real_chain():
    # Sell a near OTM call + near OTM put, buy a farther OTM call + farther
    # OTM put to cap both sides — the classic 4-leg range-bound structure.
    #
    # The full real chain has ~19 strikes (~76 (strike, type, side)
    # templates), and C(76, 4) ~= 1.28M combinations — well past
    # `_MAX_EXHAUSTIVE_COMBINATIONS` (200k), so `synthesize` would silently
    # fall back to greedy + local-search there, which isn't guaranteed to
    # find the true best 4-leg combo (see
    # `test_synthesize_falls_back_to_greedy_for_large_candidate_pools` in
    # test_strategy_synthesis.py for that fallback's own coverage). To
    # actually verify shape *recovery* here, restrict the candidate pool to
    # a window of real strikes that keeps C(4n, 4) under the exhaustive
    # threshold so this test exercises the exact-search path.
    window_strikes = {
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
    candidates = [c for c in _candidates(exclude_stale=True) if c.strike in window_strikes]
    assert (
        math.comb(len(candidates), 4) <= 200_000
    )  # sanity: this test must hit the exhaustive path

    legs = [
        SynthesizedLeg(
            strike=23900.0, option_type="PE", side="BUY", premium=_premium(23900.0, "PE")
        ),
        SynthesizedLeg(
            strike=24100.0, option_type="PE", side="SELL", premium=_premium(24100.0, "PE")
        ),
        SynthesizedLeg(
            strike=24400.0, option_type="CE", side="SELL", premium=_premium(24400.0, "CE")
        ),
        SynthesizedLeg(
            strike=24600.0, option_type="CE", side="BUY", premium=_premium(24600.0, "CE")
        ),
    ]
    target_points, _, _ = _target_from_legs(legs, 23800.0, 24750.0)

    results = synthesize(
        target_points=target_points,
        candidates=candidates,
        max_legs=4,
        min_legs=4,
        lot_size=LOT_SIZE,
        top_n=5,
        shape_weight=0.9,
        profit_weight=0.03,
        loss_weight=0.04,
        win_prob_weight=0.03,
    )

    assert results
    top = results[0]
    found = {(leg.strike, leg.option_type, leg.side) for leg in top.legs}
    assert found == {
        (23900.0, "PE", "BUY"),
        (24100.0, "PE", "SELL"),
        (24400.0, "CE", "SELL"),
        (24600.0, "CE", "BUY"),
    }
    assert top.shape_score > 0.99
    assert math.isfinite(top.risk.max_profit)
    assert math.isfinite(top.risk.max_loss)


def test_iron_condor_on_full_real_chain_uses_greedy_fallback_and_still_bounded():
    # Documents the known limitation the test above works around: with the
    # full real chain (past the exhaustive threshold), the greedy fallback
    # is not guaranteed to recover the exact combo used to draw the target
    # — but it must still return *some* valid, fully-bounded 4-leg iron-
    # condor-shaped result, not garbage or a crash.
    legs = [
        SynthesizedLeg(
            strike=23900.0, option_type="PE", side="BUY", premium=_premium(23900.0, "PE")
        ),
        SynthesizedLeg(
            strike=24100.0, option_type="PE", side="SELL", premium=_premium(24100.0, "PE")
        ),
        SynthesizedLeg(
            strike=24400.0, option_type="CE", side="SELL", premium=_premium(24400.0, "CE")
        ),
        SynthesizedLeg(
            strike=24600.0, option_type="CE", side="BUY", premium=_premium(24600.0, "CE")
        ),
    ]
    target_points, _, _ = _target_from_legs(legs, 23800.0, 24750.0)
    candidates = _candidates(exclude_stale=True)
    templates = len(candidates) * 2  # BUY and SELL side of each candidate
    assert math.comb(templates, 4) > 200_000  # sanity: this test must hit greedy

    results = synthesize(
        target_points=target_points,
        candidates=candidates,
        max_legs=4,
        min_legs=4,
        lot_size=LOT_SIZE,
        top_n=5,
        shape_weight=0.9,
        profit_weight=0.03,
        loss_weight=0.04,
        win_prob_weight=0.03,
    )

    assert results
    top = results[0]
    assert len(top.legs) == 4
    assert math.isfinite(top.risk.max_profit)
    assert math.isfinite(top.risk.max_loss)


# --------------------------------------------------------------------------
# 3. Regression test for the risk_grid unbounded-detection bug
# --------------------------------------------------------------------------


def test_risk_grid_fixes_unbounded_misdetection_past_drawn_range():
    # A real 24100/24400 bull call spread: bounded max profit once price
    # clears the short strike at 24400. If the user only draws points up to
    # 24250 (never reaching the short strike), the *unfixed* logic — edge
    # slope measured at the last two points of that narrow grid, which sits
    # squarely in the still-rising 1:1 region below 24400 — would wrongly
    # conclude the combo has unlimited upside.
    buy_k, sell_k = 24100.0, 24400.0
    legs = [
        SynthesizedLeg(strike=buy_k, option_type="CE", side="BUY", premium=_premium(buy_k, "CE")),
        SynthesizedLeg(
            strike=sell_k, option_type="CE", side="SELL", premium=_premium(sell_k, "CE")
        ),
    ]
    narrow_prices = np.linspace(24100.0, 24250.0, 30)  # ends well before the short strike
    narrow_payoff = combo_payoff(narrow_prices, legs, LOT_SIZE)

    # Reproduce the old (unfixed) behavior directly: evaluate_risk on the
    # narrow grid with no extension is genuinely unbounded-looking, because
    # 24250 < 24400 means the combo is still climbing 1:1 at that edge.
    naive_risk = evaluate_risk(narrow_prices, narrow_payoff)
    assert naive_risk.max_profit == math.inf, (
        "sanity check: the narrow grid should reproduce the pre-fix "
        "mid-ramp misdetection this test guards against"
    )

    # The fix: score_combo (used by the real search path) extends the risk
    # grid to the combo's own strikes before evaluating boundedness.
    extended_prices = risk_grid(narrow_prices, legs)
    assert extended_prices[-1] > sell_k
    extended_payoff = combo_payoff(extended_prices, legs, LOT_SIZE)
    fixed_risk = evaluate_risk(extended_prices, extended_payoff)

    net_premium = _premium(buy_k, "CE") - _premium(sell_k, "CE")
    expected_max_profit = ((sell_k - buy_k) - net_premium) * LOT_SIZE
    assert math.isfinite(fixed_risk.max_profit)
    assert fixed_risk.max_profit == pytest.approx(expected_max_profit, rel=1e-6)

    # And score_combo itself (the actual call site used by search.synthesize)
    # must report the same corrected, bounded max profit — not the naive one.
    target = np.interp(narrow_prices, narrow_prices, narrow_payoff)
    scored = score_combo(
        narrow_prices,
        target,
        legs,
        LOT_SIZE,
        shape_weight=0.25,
        profit_weight=0.25,
        loss_weight=0.20,
        win_prob_weight=0.30,
    )
    assert math.isfinite(scored.risk.max_profit)
    assert scored.risk.max_profit == pytest.approx(expected_max_profit, rel=1e-6)


def test_risk_grid_inserts_exact_leg_strike_even_when_range_already_covers_it():
    # 24200 sits between two grid points at this spacing — risk_grid must
    # insert it exactly rather than relying on the coarse grid to land on
    # it, since the combo's true peak/kink is exactly at the strike.
    legs = [SynthesizedLeg(strike=24200.0, option_type="CE", side="BUY", premium=267.2)]
    prices = np.linspace(23800.0, 24750.0, 60)
    assert 24200.0 not in prices.tolist()
    extended = risk_grid(prices, legs)
    assert 24200.0 in extended.tolist()
    assert extended[-1] > 24200.0  # still extended past the strike for the unbounded check


def test_short_straddle_max_profit_is_exact_at_the_real_shared_strike():
    # Regression for the precision half of the risk_grid fix: an ATM
    # straddle's true max profit is exactly `total premium * lot_size`,
    # realized exactly at the shared strike — a grid that doesn't happen to
    # sample that exact strike undersamples the peak.
    atm_row = min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))
    k = atm_row["strike"]
    legs = [
        SynthesizedLeg(strike=k, option_type="CE", side="SELL", premium=_premium(k, "CE")),
        SynthesizedLeg(strike=k, option_type="PE", side="SELL", premium=_premium(k, "PE")),
    ]
    coarse_prices = np.linspace(
        23800.0, 24750.0, 41
    )  # deliberately coarse, unlikely to hit k exactly
    assert k not in coarse_prices.tolist()
    extended = risk_grid(coarse_prices, legs)
    payoff = combo_payoff(extended, legs, LOT_SIZE)
    risk = evaluate_risk(extended, payoff)
    expected = (_premium(k, "CE") + _premium(k, "PE")) * LOT_SIZE
    assert risk.max_profit == pytest.approx(expected, rel=1e-9)


# --------------------------------------------------------------------------
# 4. Rupee-target scoring axis
# --------------------------------------------------------------------------


def test_rupee_profit_target_is_a_floor_met_combo_beats_short_combo():
    # Rupee profit targets are a floor — "I want to make at least this
    # much" — so a combo that clears the target scores the same 1.0
    # regardless of how far past it it lands; the axis's job is to filter
    # out combos that fall *short*, not to penalize extra profit. Two real
    # bull call spreads at very different absolute payoff scale: with the
    # target set between their two actual max profits, the narrower spread
    # (below target) must lose to the wider one (at/above target).
    narrow = [
        SynthesizedLeg(
            strike=24200.0, option_type="CE", side="BUY", premium=_premium(24200.0, "CE")
        ),
        SynthesizedLeg(
            strike=24300.0, option_type="CE", side="SELL", premium=_premium(24300.0, "CE")
        ),
    ]
    wide = [
        SynthesizedLeg(
            strike=24000.0, option_type="CE", side="BUY", premium=_premium(24000.0, "CE")
        ),
        SynthesizedLeg(
            strike=24700.0, option_type="CE", side="SELL", premium=_premium(24700.0, "CE")
        ),
    ]
    prices = np.linspace(23800.0, 24750.0, 150)
    target = np.zeros_like(prices)  # shape irrelevant here — isolate the rupee axis

    narrow_risk = evaluate_risk(prices, combo_payoff(prices, narrow, LOT_SIZE))
    wide_risk = evaluate_risk(prices, combo_payoff(prices, wide, LOT_SIZE))
    assert narrow_risk.max_profit < wide_risk.max_profit  # sanity: they really do differ in scale

    target_max_profit = (narrow_risk.max_profit + wide_risk.max_profit) / 2

    narrow_scored = score_combo(
        prices,
        target,
        narrow,
        LOT_SIZE,
        shape_weight=0.0,
        profit_weight=0.0,
        loss_weight=0.0,
        win_prob_weight=0.0,
        rupee_weight=1.0,
        target_max_profit=target_max_profit,
    )
    wide_scored = score_combo(
        prices,
        target,
        wide,
        LOT_SIZE,
        shape_weight=0.0,
        profit_weight=0.0,
        loss_weight=0.0,
        win_prob_weight=0.0,
        rupee_weight=1.0,
        target_max_profit=target_max_profit,
    )

    assert narrow_scored.rupee_score < 1.0  # falls short of the floor
    assert wide_scored.rupee_score == pytest.approx(1.0, abs=1e-6)  # clears it
    assert wide_scored.score > narrow_scored.score


def test_rupee_loss_target_is_a_ceiling_smaller_loss_never_penalized():
    # Symmetric check on the loss side: a target_max_loss is a ceiling
    # ("don't lose more than this"), so a combo whose actual loss is
    # smaller than the cap scores the max 1.0, same as one that lands
    # exactly on it — only exceeding the cap is penalized.
    narrow = [
        SynthesizedLeg(
            strike=24200.0, option_type="CE", side="BUY", premium=_premium(24200.0, "CE")
        ),
        SynthesizedLeg(
            strike=24300.0, option_type="CE", side="SELL", premium=_premium(24300.0, "CE")
        ),
    ]
    wide = [
        SynthesizedLeg(
            strike=24000.0, option_type="CE", side="BUY", premium=_premium(24000.0, "CE")
        ),
        SynthesizedLeg(
            strike=24700.0, option_type="CE", side="SELL", premium=_premium(24700.0, "CE")
        ),
    ]
    prices = np.linspace(23800.0, 24750.0, 150)
    target = np.zeros_like(prices)

    narrow_risk = evaluate_risk(prices, combo_payoff(prices, narrow, LOT_SIZE))
    wide_risk = evaluate_risk(prices, combo_payoff(prices, wide, LOT_SIZE))
    assert abs(narrow_risk.max_loss) < abs(wide_risk.max_loss)  # narrow risks less

    target_max_loss = abs(narrow_risk.max_loss)  # exactly narrow's own risk

    narrow_scored = score_combo(
        prices,
        target,
        narrow,
        LOT_SIZE,
        shape_weight=0.0,
        profit_weight=0.0,
        loss_weight=0.0,
        win_prob_weight=0.0,
        rupee_weight=1.0,
        target_max_loss=target_max_loss,
    )
    wide_scored = score_combo(
        prices,
        target,
        wide,
        LOT_SIZE,
        shape_weight=0.0,
        profit_weight=0.0,
        loss_weight=0.0,
        win_prob_weight=0.0,
        rupee_weight=1.0,
        target_max_loss=target_max_loss,
    )

    assert narrow_scored.rupee_score == pytest.approx(1.0, abs=1e-6)  # exactly at the cap
    assert wide_scored.rupee_score < 1.0  # blows past the cap
    assert narrow_scored.score > wide_scored.score


def test_rupee_score_is_none_when_no_target_given():
    legs = [SynthesizedLeg(strike=24200.0, option_type="CE", side="BUY", premium=267.2)]
    prices = np.linspace(23800.0, 24750.0, 40)
    target = np.zeros_like(prices)
    scored = score_combo(
        prices,
        target,
        legs,
        LOT_SIZE,
        shape_weight=0.25,
        profit_weight=0.25,
        loss_weight=0.2,
        win_prob_weight=0.3,
    )
    assert scored.rupee_score is None


def test_rupee_target_axis_is_inert_by_default_in_synthesize():
    # rupee_weight defaults to 0.0 and no target is passed — synthesize()
    # results must be identical to a call that never mentions rupee targets
    # at all (backward compatibility for existing callers).
    candidates = _candidates(exclude_stale=True)
    target_points = [(23800.0, -2.0), (24200.0, 0.0), (24500.0, 4.0)]
    kwargs = {
        "target_points": target_points,
        "candidates": candidates,
        "max_legs": 2,
        "lot_size": LOT_SIZE,
        "top_n": 3,
        "spot": SPOT,
        "iv": ATM_IV,
        "years": YEARS,
    }
    baseline = synthesize(**kwargs)
    with_inert_rupee = synthesize(**kwargs, rupee_weight=0.0, target_max_profit=None)
    assert [r.score for r in baseline] == [r.score for r in with_inert_rupee]


def test_invalid_rupee_targets_raise():
    candidates = [LegCandidate(24200.0, "CE", 267.2)]
    target_points = [(24000.0, 0.0), (24400.0, 0.0)]
    with pytest.raises(ValueError):
        synthesize(
            target_points=target_points, candidates=candidates, max_legs=1, target_max_profit=0
        )
    with pytest.raises(ValueError):
        synthesize(
            target_points=target_points, candidates=candidates, max_legs=1, target_max_loss=-5
        )
    with pytest.raises(ValueError):
        synthesize(target_points=target_points, candidates=candidates, max_legs=1, rupee_weight=1.5)


# --------------------------------------------------------------------------
# 5. Win probability sanity checks against real IV/years
# --------------------------------------------------------------------------


def test_win_probability_bounded_with_real_market_inputs():
    atm_row = min(CHAIN["chain"], key=lambda r: abs(r["strike"] - SPOT))
    k = atm_row["strike"]
    legs = [SynthesizedLeg(strike=k, option_type="CE", side="SELL", premium=_premium(k, "CE"))]
    prices = np.linspace(20000.0, 29000.0, 400)
    payoff = combo_payoff(prices, legs, LOT_SIZE)
    p = win_probability(prices, payoff, spot=SPOT, iv=ATM_IV, years=YEARS)
    assert 0.0 <= p <= 1.0
    # Selling a near-ATM call with ~18 days to expiry should win noticeably
    # more than half the time (collects premium unless price rallies hard).
    assert p > 0.5


def test_win_probability_deep_otm_sold_put_is_high_with_real_iv():
    deep_otm_put_strike = 23800.0  # ~1.8% below spot, ~18 days to expiry
    legs = [
        SynthesizedLeg(
            strike=deep_otm_put_strike,
            option_type="PE",
            side="SELL",
            premium=_premium(deep_otm_put_strike, "PE"),
        )
    ]
    prices = np.linspace(15000.0, 29000.0, 400)
    payoff = combo_payoff(prices, legs, LOT_SIZE)
    p = win_probability(prices, payoff, spot=SPOT, iv=ATM_IV, years=YEARS)
    assert p > 0.6


# --------------------------------------------------------------------------
# 6. End-to-end: full real chain, including its one stale/thin print
# --------------------------------------------------------------------------


def test_synthesize_end_to_end_on_full_real_chain_including_stale_row():
    # Uses the full candidate pool (stale 24650 CE row included) to prove
    # the search doesn't get derailed by one illiquid, out-of-line premium
    # in an otherwise-real chain.
    candidates = _candidates(exclude_stale=False)
    # A modestly bullish target a user might actually draw: flat-ish below
    # spot, rising above it.
    target_points = [(23800.0, -1.0), (24200.0, -0.5), (24500.0, 1.0), (24750.0, 1.0)]

    results = synthesize(
        target_points=target_points,
        candidates=candidates,
        max_legs=3,
        lot_size=LOT_SIZE,
        top_n=5,
        spot=SPOT,
        iv=ATM_IV,
        years=YEARS,
    )

    assert results
    for r in results:
        assert 1 <= len(r.legs) <= 3
        assert 0.0 <= r.win_probability <= 1.0
        assert r.risk.max_loss == -math.inf or math.isfinite(r.risk.max_loss)
