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
    """Bundles the growing list of `score_combo` parameters that every
    candidate combo in a search needs scored the same way, so `_exhaustive`/
    `_greedy` pass one object around instead of an ever-lengthening
    positional list."""

    lot_size: int
    shape_weight: float
    risk_weight: float
    win_prob_weight: float
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
        ctx.risk_weight,
        ctx.win_prob_weight,
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


def synthesize(
    target_points: list[tuple[float, float]],
    candidates: list[LegCandidate],
    max_legs: int,
    lot_size: int = 1,
    min_legs: int = 1,
    top_n: int = 5,
    allow_sides: tuple[str, ...] = ("BUY", "SELL"),
    shape_weight: float = 0.7,
    risk_weight: float = 0.15,
    win_prob_weight: float = 0.15,
    spot: float | None = None,
    iv: float | None = None,
    years: float | None = None,
    grid_points: int = 120,
) -> list[ScoredCombo]:
    """
    Finds the top `top_n` leg combinations (from `min_legs` to `max_legs`
    legs) that best match `target_points` — a user-drawn (price, P&L) curve
    — ranked by a blend of shape fit, risk/reward, and win probability (see
    `objective.py`).

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

    The default weight split (0.7 shape / 0.15 risk / 0.15 win-probability)
    is deliberately shape-dominant: `objective._risk_score` gives an
    unbounded-profit combo close to its maximum score regardless of how it
    actually looks, and a shape-agnostic combo can likewise have a high raw
    win probability by sheer luck of where its breakevens land — either one
    at a near-even blend could outscore a combo that matches the user's
    drawn shape almost exactly but happens to be capped, which is backwards:
    a flat top in the drawing means the user *wants* a capped payoff there.
    Risk/reward and win-probability should only decide between candidates
    whose shape fit is already comparable, not override a clearly better
    shape match.
    """
    if not target_points or not candidates or max_legs < 1:
        return []
    if not (0 < shape_weight <= 1 and 0 <= risk_weight <= 1 and 0 <= win_prob_weight <= 1):
        raise ValueError(
            "shape_weight must be in (0,1]; risk_weight and win_prob_weight must be in [0,1]"
        )

    xs = np.array([p[0] for p in target_points], dtype=float)
    ys = np.array([p[1] for p in target_points], dtype=float)
    order = np.argsort(xs)
    xs, ys = xs[order], ys[order]

    prices = np.linspace(xs[0], xs[-1], grid_points)
    target = np.interp(prices, xs, ys)

    templates = _templates(candidates, allow_sides)
    ctx = _ScoringContext(
        lot_size=lot_size,
        shape_weight=shape_weight,
        risk_weight=risk_weight,
        win_prob_weight=win_prob_weight,
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
