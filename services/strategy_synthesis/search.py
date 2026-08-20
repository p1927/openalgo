"""
Search over candidate strikes for the leg combination that best matches a
user-drawn payoff shape (see `objective.score_combo`), subject to a leg
count cap.

Exhaustive combinatorics are only tractable for a handful of legs — an
option chain easily has 40-80 usable (strike, type, side) templates, and
choosing e.g. 4 of those is already millions of combinations. Below
`_MAX_EXHAUSTIVE_COMBINATIONS` this searches exactly; above it, it falls
back to a greedy construction (add whichever leg most improves the score
at each step) plus a bounded local-search polish (try swapping each chosen
leg for every unused template, keep improvements) — a standard approach
that keeps runtime bounded regardless of how large the input chain is.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass

import numpy as np

from .objective import ScoredCombo, score_combo
from .payoff import LegCandidate, SynthesizedLeg

_MAX_EXHAUSTIVE_COMBINATIONS = 200_000
_LOCAL_SEARCH_PASSES = 2


@dataclass(frozen=True)
class _ScoringContext:
    """
    Bundles the growing list of `score_combo` parameters that every
    candidate combo in a search needs scored the same way, so `_exhaustive`/
    `_greedy` pass one object around instead of an ever-lengthening
    positional list.
    """

    lot_size: int
    shape_weight: float
    profit_weight: float
    loss_weight: float
    win_prob_weight: float
    profit_normalization: float
    spot: float | None
    iv: float | None
    years: float | None


def _score(
    prices: np.ndarray, target: np.ndarray, legs: list[SynthesizedLeg], ctx: _ScoringContext
) -> ScoredCombo:
    return score_combo(
        prices,
        target,
        legs,
        ctx.lot_size,
        ctx.shape_weight,
        ctx.profit_weight,
        ctx.loss_weight,
        ctx.win_prob_weight,
        ctx.profit_normalization,
        ctx.spot,
        ctx.iv,
        ctx.years,
    )


def _templates(candidates: list[LegCandidate], sides: tuple[str, ...]) -> list[SynthesizedLeg]:
    return [
        SynthesizedLeg(strike=c.strike, option_type=c.option_type, side=side, premium=c.premium)
        for c in candidates
        for side in sides
    ]


def _dedupe_key(legs: list[SynthesizedLeg]) -> tuple:
    return tuple(sorted((leg.strike, leg.option_type, leg.side) for leg in legs))


def _exhaustive(
    templates: list[SynthesizedLeg],
    leg_count: int,
    prices: np.ndarray,
    target: np.ndarray,
    ctx: _ScoringContext,
) -> list[ScoredCombo]:
    return [
        _score(prices, target, list(combo), ctx)
        for combo in itertools.combinations(templates, leg_count)
    ]


def _greedy(
    templates: list[SynthesizedLeg],
    leg_count: int,
    prices: np.ndarray,
    target: np.ndarray,
    ctx: _ScoringContext,
) -> list[ScoredCombo]:
    chosen: list[SynthesizedLeg] = []
    remaining = list(templates)

    for _ in range(leg_count):
        if not remaining:
            break
        best_leg = None
        best_score = -math.inf
        for leg in remaining:
            trial = _score(prices, target, chosen + [leg], ctx)
            if trial.score > best_score:
                best_score = trial.score
                best_leg = leg
        chosen.append(best_leg)
        remaining.remove(best_leg)

    for _ in range(_LOCAL_SEARCH_PASSES):
        improved = False
        for i in range(len(chosen)):
            current_score = _score(prices, target, chosen, ctx).score
            for candidate_leg in list(remaining):
                trial_legs = chosen[:i] + [candidate_leg] + chosen[i + 1 :]
                trial = _score(prices, target, trial_legs, ctx)
                if trial.score > current_score:
                    remaining.remove(candidate_leg)
                    remaining.append(chosen[i])
                    chosen[i] = candidate_leg
                    current_score = trial.score
                    improved = True
        if not improved:
            break

    if not chosen:
        return []
    return [_score(prices, target, chosen, ctx)]


def _default_profit_normalization(candidates: list[LegCandidate], lot_size: int) -> float:
    """
    Build a sensible "this is a good absolute profit" benchmark from the
    candidate pool, so a combo delivering ~5x the most expensive single
    leg's premium as max profit scores 1.0 on the profit axis, and a
    combo delivering the same as one leg's premium scores 0.2.

    Why 5x: a fully-loaded 4-leg bullish structure (long low strike +
    long high strike + short mid-strike puts) can roughly deliver up to
    a few multiples of a single premium in max profit; beyond that, a
    naked long call's unlimited upside will saturate at 1.0 anyway, so
    the exact constant doesn't matter for those — it just needs to be
    a value where "ordinary" multi-leg combos land in the 0.2..0.8
    range rather than 0.0..0.1 or 0.99..1.0.
    """
    if not candidates:
        return 1.0
    max_premium = max(c.premium for c in candidates)
    return max(max_premium * 5.0 * lot_size, 1.0)


def synthesize(
    target_points: list[tuple[float, float]],
    candidates: list[LegCandidate],
    max_legs: int,
    lot_size: int = 1,
    min_legs: int = 1,
    top_n: int = 5,
    allow_sides: tuple[str, ...] = ("BUY", "SELL"),
    shape_weight: float = 0.10,
    profit_weight: float = 0.30,
    loss_weight: float = 0.20,
    win_prob_weight: float = 0.40,
    profit_normalization: float | None = None,
    spot: float | None = None,
    iv: float | None = None,
    years: float | None = None,
    grid_points: int = 120,
) -> list[ScoredCombo]:
    """
    Finds the top `top_n` leg combinations (from `min_legs` to `max_legs`
    legs) that best match `target_points` — a user-drawn (price, P&L) curve
    — ranked by a blend of shape fit, absolute max profit, absolute max
    loss, and win probability (see `objective.score_combo`).

    `candidates` is the pool of (strike, option_type, premium) the search
    may choose from — callers fetch this from the live option chain (see
    `service.py`). This function itself has no I/O and is deterministic
    given its inputs, which is what makes it unit-testable without a
    broker connection.

    `spot`/`iv`/`years` feed `objective.win_probability` — pass them
    (`service.py` does, from the live chain) to get a real P(profit)
    estimate; omit them and win_probability degrades to a neutral 0.5 for
    every candidate, which doesn't affect relative ranking (a constant
    shifts every combo's score equally) so callers without live market data
    can still call this safely.

    The default weight split (0.10 shape / 0.30 profit / 0.20 loss /
    0.40 win probability) reflects the user's stated priority for the
    Draw Target recommendations:

      1. High chance of happening (win probability, biggest weight).
      2. More profit, not legs that give very little (absolute max
         profit, second).
      3. Less risk (absolute max loss, third).
      4. Match the drawn shape (still a real factor, but no longer
         dominant — the shape is a *gate* to keep the top results
         from being a wildly different shape than the user drew,
         not the primary axis).

    The previous default (0.7 / 0.15 / 0.15 shape/risk/win_prob) used
    the scale-invariant shape score as the primary axis, which meant
    a near-perfect shape match on a tiny-payoff structure would outrank
    a clearly more profitable, higher-win-probability combo just because
    the user's drawn target happened to fit a low-payoff body. The user
    explicitly does not want that — they want the holy grail (high
    win rate, more profit, less risk), and accept that the drawn shape
    is a hint, not a contract.

    `profit_normalization` is the rupee value that maps to a profit
    score of 1.0; if omitted, it defaults to 5x the highest premium in
    the candidate pool, which puts ordinary multi-leg combos in the
    0.2..0.8 range and saturates naked long calls at 1.0.
    """
    if not target_points or not candidates or max_legs < 1:
        return []
    if not (
        0 <= shape_weight <= 1
        and 0 <= profit_weight <= 1
        and 0 <= loss_weight <= 1
        and 0 <= win_prob_weight <= 1
    ):
        raise ValueError(
            "shape_weight, profit_weight, loss_weight, win_prob_weight must all be in [0, 1]"
        )

    xs = np.array([p[0] for p in target_points], dtype=float)
    ys = np.array([p[1] for p in target_points], dtype=float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]

    prices = np.linspace(xs[0], xs[-1], grid_points)
    target = np.interp(prices, xs, ys)

    templates = _templates(candidates, allow_sides)
    norm = (
        profit_normalization
        if profit_normalization is not None
        else _default_profit_normalization(candidates, lot_size)
    )
    ctx = _ScoringContext(
        lot_size=lot_size,
        shape_weight=shape_weight,
        profit_weight=profit_weight,
        loss_weight=loss_weight,
        win_prob_weight=win_prob_weight,
        profit_normalization=norm,
        spot=spot,
        iv=iv,
        years=years,
    )

    all_results: list[ScoredCombo] = []
    for leg_count in range(min_legs, max_legs + 1):
        combos_possible = math.comb(len(templates), leg_count) if len(templates) >= leg_count else 0
        if combos_possible == 0:
            continue
        if combos_possible <= _MAX_EXHAUSTIVE_COMBINATIONS:
            all_results.extend(_exhaustive(templates, leg_count, prices, target, ctx))
        else:
            all_results.extend(_greedy(templates, leg_count, prices, target, ctx))

    all_results.sort(key=lambda r: r.score, reverse=True)

    deduped: list[ScoredCombo] = []
    seen: set[tuple] = set()
    for result in all_results:
        key = _dedupe_key(result.legs)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(result)
        if len(deduped) >= top_n:
            break

    return deduped
