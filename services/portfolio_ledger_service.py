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


def get_strategy_performance(user_id: str, strategy: str | None = None) -> dict[str, Any]:
    """Win-rate/expectancy stats over realized-trade-closure events.

    `strategy=None` aggregates every closed trade across the whole account;
    pass a specific strategy tag to scope to just that one. Each row in
    `strategy_book_db.StrategyClosedTrade` is one realization event (a fill
    that closed some or all of an open leg) — a partial close counts as its
    own event, not a fraction of a "round trip", same as this codebase's
    fill-level P&L accounting elsewhere.

    Expectancy is `win_rate * average_win + loss_rate * average_loss`
    (`average_loss` is negative) — the standard "expected P&L per trade"
    figure, distinct from `get_portfolio_rollup`'s point-in-time risk
    snapshot: this answers "is the system making money over time", that
    answers "how much is at risk right now".
    """
    from database.strategy_book_db import get_closed_trades

    trades = get_closed_trades(user_id=user_id, strategy=strategy)
    if not trades:
        return {
            "status": "success",
            "trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "breakeven_count": 0,
            "win_rate": None,
            "expectancy": None,
            "average_win": None,
            "average_loss": None,
            "gross_profit": 0.0,
            "gross_loss": 0.0,
            "net_pnl": 0.0,
        }

    pnls = [t["realized_pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    trade_count = len(pnls)
    win_rate = len(wins) / trade_count
    loss_rate = len(losses) / trade_count
    average_win = (sum(wins) / len(wins)) if wins else 0.0
    average_loss = (sum(losses) / len(losses)) if losses else 0.0
    expectancy = (win_rate * average_win) + (loss_rate * average_loss)

    return {
        "status": "success",
        "trade_count": trade_count,
        "win_count": len(wins),
        "loss_count": len(losses),
        "breakeven_count": trade_count - len(wins) - len(losses),
        "win_rate": round(win_rate, 4),
        "expectancy": round(expectancy, 2),
        "average_win": round(average_win, 2),
        "average_loss": round(average_loss, 2),
        "gross_profit": round(sum(wins), 2),
        "gross_loss": round(sum(losses), 2),
        "net_pnl": round(sum(pnls), 2),
    }
