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
from services.option_greeks_service import calculate_time_to_expiry, get_expiry_datetime
from services.option_symbol_service import get_option_exchange
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
        with_greeks=True,
    )
    if not success:
        return False, chain_response, status_code

    spot = chain_response.get("underlying_ltp")
    years = _years_to_expiry(exchange, expiry_date)
    iv = _atm_iv(chain_response)

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
        spot=spot,
        iv=iv,
        years=years,
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


def _years_to_expiry(exchange: str, expiry_date: str) -> float | None:
    """Time to expiry in years, for `win_probability`'s lognormal model — the
    same helper `option_chain_service` itself uses for chain-wide Greeks."""
    options_exchange = get_option_exchange(exchange)
    expiry_dt = get_expiry_datetime(expiry_date, options_exchange)
    if expiry_dt is None:
        return None
    years, _ = calculate_time_to_expiry(expiry_dt)
    return years if years > 0 else None


def _atm_iv(chain_response: dict[str, Any]) -> float | None:
    """
    A single representative IV for `win_probability`'s distribution —
    averaged CE/PE implied vol at the chain's ATM strike. `win_probability`
    is already a rough heuristic (a real vol surface has skew this ignores),
    so one ATM figure is a reasonable simplification rather than needing a
    per-leg vol lookup threaded through the whole search.
    """
    chain = chain_response.get("chain") or []
    atm_strike = chain_response.get("atm_strike")
    if not chain or atm_strike is None:
        return None
    row = min(chain, key=lambda r: abs((r.get("strike") or 0) - atm_strike))
    ivs = [
        iv
        for leg_key in ("ce", "pe")
        if (leg := row.get(leg_key))
        and (iv := leg.get("implied_volatility")) is not None
        and iv > 0
    ]
    if not ivs:
        return None
    avg_iv_pct = sum(ivs) / len(ivs)
    # implied_volatility comes back as a percentage (e.g. 18.5) — the
    # lognormal model in probability.py expects a decimal (0.185).
    return avg_iv_pct / 100


def _serialize(result: ScoredCombo, symbol_lookup: dict[tuple[float, str], str]) -> dict[str, Any]:
    return {
        "score": round(result.score, 4),
        "shape_score": round(result.shape_score, 4),
        "profit_score": round(result.profit_score, 4),
        "loss_score": round(result.loss_score, 4),
        "win_probability": round(result.win_probability, 4),
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
