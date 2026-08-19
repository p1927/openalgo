"""
Adapter wiring `search.synthesize` to the live option chain, so a caller
(a future API route) can go straight from "this underlying, this many
legs, this drawn curve" to ranked leg combinations without assembling the
candidate pool itself.

Kept separate from `search.py` deliberately — `search.py` and the modules
it depends on (`payoff.py`, `objective.py`) have no I/O and are unit-tested
directly; this module is the only part that touches the network/broker and
is exercised via the option-chain service's own integration tests instead.
"""

from __future__ import annotations

from typing import Any

from services.option_chain_service import get_option_chain
from utils.logging import get_logger

from .objective import ScoredCombo
from .payoff import LegCandidate
from .search import synthesize

logger = get_logger(__name__)


def synthesize_from_option_chain(
    api_key: str,
    underlying: str,
    exchange: str,
    expiry_date: str,
    target_points: list[tuple[float, float]],
    max_legs: int,
    strike_count: int = 20,
    lot_size: int = 1,
    **synthesize_kwargs: Any,
) -> tuple[bool, dict[str, Any], int]:
    """
    Fetches the live option chain for `underlying`/`expiry_date` and runs
    `synthesize` against it. Returns `(success, response, status_code)`,
    mirroring the convention `option_chain_service.get_option_chain` uses.
    """
    success, chain_response, status_code = get_option_chain(
        underlying=underlying,
        exchange=exchange,
        expiry_date=expiry_date,
        strike_count=strike_count,
        api_key=api_key,
        with_quotes=True,
        with_greeks=False,
    )
    if not success:
        return False, chain_response, status_code

    candidates: list[LegCandidate] = []
    # The synthesis core only deals in (strike, option_type, premium) — it
    # has no notion of a broker symbol. Keep a side lookup here so the
    # response can still hand the frontend a real, orderable symbol per
    # chosen leg without threading broker concerns into the pure package.
    symbol_lookup: dict[tuple[float, str], str] = {}
    for row in chain_response.get("chain", []):
        strike = row.get("strike")
        if strike is None:
            continue
        for option_type, leg_key in (("CE", "ce"), ("PE", "pe")):
            leg_data = row.get(leg_key) or {}
            ltp = leg_data.get("ltp")
            if ltp is None or ltp <= 0:
                continue
            candidates.append(
                LegCandidate(strike=float(strike), option_type=option_type, premium=float(ltp))
            )
            symbol = leg_data.get("symbol")
            if symbol:
                symbol_lookup[(float(strike), option_type)] = symbol

    if not candidates:
        logger.warning(
            "strategy_synthesis: no tradable strikes with quotes for %s %s", underlying, expiry_date
        )
        return False, {"status": "error", "message": "No tradable strikes with quotes found"}, 404

    results = synthesize(
        target_points=target_points,
        candidates=candidates,
        max_legs=max_legs,
        lot_size=lot_size,
        **synthesize_kwargs,
    )
    return (
        True,
        {
            "status": "success",
            "data": {
                "underlying_ltp": chain_response.get("underlying_ltp"),
                "results": [_serialize(r, symbol_lookup) for r in results],
            },
        },
        200,
    )


def _serialize(result: ScoredCombo, symbol_lookup: dict[tuple[float, str], str]) -> dict[str, Any]:
    return {
        "score": round(result.score, 4),
        "shape_score": round(result.shape_score, 4),
        "risk_score": round(result.risk_score, 4),
        "max_profit": None
        if result.risk.max_profit == float("inf")
        else round(result.risk.max_profit, 2),
        "max_loss": None
        if result.risk.max_loss == float("-inf")
        else round(result.risk.max_loss, 2),
        "breakevens": [round(b, 2) for b in result.risk.breakevens],
        "legs": [
            {
                "strike": leg.strike,
                "option_type": leg.option_type,
                "side": leg.side,
                "premium": leg.premium,
                "qty": leg.qty,
                "symbol": symbol_lookup.get((leg.strike, leg.option_type)),
            }
            for leg in result.legs
        ],
    }
