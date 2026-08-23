"""
Capital/risk-reward ledger — module 9 of the options-profitability prediction
platform (see .claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md).

Answers two questions the sandbox's existing per-position/per-strategy P&L
views do not: "how much capital is currently at risk across every open
position" and "how much of my banked profit could I safely take out right
now" (banked profit minus capital currently committed as risk).

Builds entirely on existing sandbox infrastructure rather than a new capital
model: `SandboxFunds` (database/sandbox_db.py) is already the capital
account, and `strategy_book_db.py`'s per-strategy book (plus the new
`StrategyRiskProfile` table it carries) already tracks which strategies are
open and what their max-risk-at-entry was. This module only combines them.
"""

from __future__ import annotations

from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


def get_capital_account(user_id: str) -> dict[str, Any] | None:
    """Capital account snapshot: starting capital, available balance, margin
    currently committed, and running realized/unrealized P&L.

    A thin read over the existing `SandboxFunds` row — no new table. Returns
    `None` if the user has no sandbox funds row yet (mirrors
    `FundManager.get_funds()`'s own not-found behavior rather than
    initializing one as a side effect of a read)."""
    from database.sandbox_db import SandboxFunds

    funds = SandboxFunds.query.filter_by(user_id=user_id).first()
    if funds is None:
        return None
    return {
        "total_capital": float(funds.total_capital or 0),
        "available_balance": float(funds.available_balance or 0),
        "used_margin": float(funds.used_margin or 0),
        "realized_pnl": float(funds.realized_pnl or 0),
        "today_realized_pnl": float(funds.today_realized_pnl or 0),
        "unrealized_pnl": float(funds.unrealized_pnl or 0),
        "total_pnl": float(funds.total_pnl or 0),
    }


def _is_strategy_open(user_id: str, strategy: str) -> bool:
    """A strategy is open if any of its legs still carries nonzero quantity.

    Reads the strategy book directly rather than going through
    `strategy_pnl_service.get_strategy_pnl` because that function also marks
    every open leg to market via a live broker `positionbook()` call, which
    this check does not need — capital-at-risk uses the fixed max-risk figure
    recorded at entry, not a live P&L mark."""
    from database.strategy_book_db import StrategyBookUnavailable, get_strategy_legs

    legs = get_strategy_legs(user_id=user_id, strategy=strategy)
    return any(abs(leg.get("quantity") or 0) > 1e-9 for leg in legs)


def get_portfolio_rollup(user_id: str) -> dict[str, Any]:
    """Portfolio-level rollup: total capital currently at risk (sum of
    max-risk across every currently-open, risk-profiled strategy), total
    profit already banked (realized P&L, all-time), and the "safe to
    withdraw" figure the user asked for directly — banked profit minus
    capital currently committed as risk.

    A strategy with open legs but no recorded risk profile (module 5 has not
    run for it, or it predates this module) is surfaced under
    `unprofiled_open_strategies` rather than silently excluded from
    `capital_at_risk` — treating an unknown risk as zero risk would make the
    "safe to withdraw" figure overstate what is actually safe.
    """
    from database.strategy_book_db import (
        StrategyBookUnavailable,
        get_strategy_risk_profile,
        list_strategies,
    )

    capital = get_capital_account(user_id)
    if capital is None:
        return {
            "status": "error",
            "message": f"No sandbox funds found for user {user_id}",
        }

    try:
        strategies = list_strategies(user_id=user_id)
    except StrategyBookUnavailable as exc:
        return {"status": "error", "message": f"Strategy book unavailable: {exc}"}

    capital_at_risk = 0.0
    open_strategies: list[dict[str, Any]] = []
    unprofiled_open_strategies: list[str] = []

    for strategy in strategies:
        try:
            if not _is_strategy_open(user_id, strategy):
                continue
        except StrategyBookUnavailable as exc:
            return {"status": "error", "message": f"Strategy book unavailable: {exc}"}

        profile = get_strategy_risk_profile(user_id, strategy)
        if profile is None:
            unprofiled_open_strategies.append(strategy)
            continue

        capital_at_risk += profile["max_risk"]
        open_strategies.append(profile)

    banked_pnl = capital["realized_pnl"]
    safe_to_withdraw = banked_pnl - capital_at_risk

    return {
        "status": "success",
        "capital": capital,
        "capital_at_risk": round(capital_at_risk, 2),
        "banked_pnl": round(banked_pnl, 2),
        "safe_to_withdraw": round(safe_to_withdraw, 2),
        "open_strategies": open_strategies,
        "unprofiled_open_strategies": unprofiled_open_strategies,
    }
