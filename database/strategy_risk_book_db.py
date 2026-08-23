# database/strategy_risk_book_db.py
"""
Per-strategy risk-at-entry snapshots and realized-trade log — the module 9
(portfolio-ledger) extension of `strategy_book_db`'s per-strategy position
book, split into its own file per `docs/FORK_CONVENTIONS.md`'s sidecar
pattern rather than living inline in that upstream-owned module.

Shares `strategy_book_db`'s `Base`/`db_session`/`logger` rather than creating
its own — these models register on the same declarative `Base`, so
`init_strategy_book_db()`'s `Base.metadata.create_all(bind=engine)` creates
these tables too, and every read/write shares the one connection pool and
transaction scope the position book itself uses.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, Float, Integer, String, UniqueConstraint

from database.strategy_book_db import Base, db_session, logger


class StrategyRiskProfile(Base):
    """Max-risk / max-profit snapshot for one strategy group, recorded once at
    entry (typically by the risk-adjusted-selector's ranking output) and read
    back by the portfolio-ledger rollup to answer "how much capital is
    currently committed as risk".

    Deliberately separate from `StrategyPosition`: that table is fed
    continuously from the event bus and reflects live quantity/cost basis,
    while this one is a point-in-time figure fixed at open time (a spread's
    max loss does not change as the market moves, only its live P&L within
    that fixed band does). One row per (user, strategy) — a strategy is
    re-profiled by upserting, not by inserting a new row.
    """

    __tablename__ = "strategy_risk_profiles"
    __table_args__ = (UniqueConstraint("user_id", "strategy", name="uq_strategy_risk_profile"),)

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    strategy = Column(String(120), nullable=False, index=True)
    max_risk = Column(Float, nullable=False)
    # None for an undefined-risk position (e.g. a long single option) whose
    # profit is uncapped — never a stand-in for "unknown".
    max_profit = Column(Float, nullable=True)
    recorded_at = Column(DateTime, nullable=False, default=datetime.now)
    updated_at = Column(DateTime, nullable=False, default=datetime.now, onupdate=datetime.now)


class StrategyClosedTrade(Base):
    """One row per realized-P&L event, written at the exact point
    `strategy_book_db._apply_fill_locked` computes `realized` for a closing
    fill (via `record_closed_trade` below).

    `StrategyPosition.realized_pnl` only accumulates a running total — this
    table persists each discrete realization so win-rate/expectancy can be
    computed over individual trade-closure events, not just the sum.

    A partial close is its own row here, same as how a partial fill is its
    own row in `SandboxTrades` — this counts "how many realization events
    were profitable", not "how many round-trips to fully flat", since a leg
    reopening the same day makes lifecycle boundaries ambiguous to infer
    reliably from fills alone. That matches how win-rate is conventionally
    reported in trade journals.
    """

    __tablename__ = "strategy_closed_trades"

    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), nullable=False, index=True)
    strategy = Column(String(120), nullable=False, index=True)
    symbol = Column(String(64), nullable=False)
    exchange = Column(String(20), nullable=False)
    product = Column(String(20), nullable=False)
    closed_quantity = Column(Float, nullable=False)
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=False)
    realized_pnl = Column(Float, nullable=False)
    trade_date = Column(String(10), nullable=True)
    closed_at = Column(DateTime, nullable=False, default=datetime.now)


def record_closed_trade(
    *,
    user_id: str,
    strategy: str,
    symbol: str,
    exchange: str,
    product: str,
    closed_quantity: float,
    entry_price: float,
    exit_price: float,
    realized_pnl: float,
    trade_date: str | None,
) -> None:
    """Stage a `StrategyClosedTrade` row on the caller's open session.

    Deliberately does not commit — called from inside
    `strategy_book_db._apply_fill_locked`'s own transaction, which commits
    (or rolls back) the position update and this row together atomically.
    """
    db_session.add(
        StrategyClosedTrade(
            user_id=user_id,
            strategy=strategy,
            symbol=symbol,
            exchange=exchange,
            product=product,
            closed_quantity=closed_quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            trade_date=trade_date,
        )
    )


def get_closed_trades(
    user_id: str | None = None,
    strategy: str | None = None,
    *,
    limit: int | None = None,
) -> list[dict]:
    """Realized-trade log, newest first. Feeds win-rate/expectancy stats."""
    query = db_session.query(StrategyClosedTrade)
    if user_id:
        query = query.filter(StrategyClosedTrade.user_id == user_id)
    if strategy:
        query = query.filter(StrategyClosedTrade.strategy == strategy)
    query = query.order_by(StrategyClosedTrade.closed_at.desc())
    if limit:
        query = query.limit(limit)
    return [
        {
            "strategy": row.strategy,
            "symbol": row.symbol,
            "exchange": row.exchange,
            "product": row.product,
            "closed_quantity": round(float(row.closed_quantity), 4),
            "entry_price": round(float(row.entry_price), 4),
            "exit_price": round(float(row.exit_price), 4),
            "realized_pnl": round(float(row.realized_pnl), 4),
            "trade_date": row.trade_date,
            "closed_at": row.closed_at.isoformat() if row.closed_at else None,
        }
        for row in query.all()
    ]


def set_strategy_risk_profile(
    user_id: str, strategy: str, max_risk: float, max_profit: float | None = None
) -> None:
    """Record (or replace) the max-risk/max-profit-at-entry figures for one
    strategy group. Idempotent — re-recording the same strategy overwrites its
    prior snapshot rather than accumulating rows."""
    try:
        row = (
            db_session.query(StrategyRiskProfile)
            .filter_by(user_id=user_id, strategy=strategy)
            .first()
        )
        if row is None:
            row = StrategyRiskProfile(user_id=user_id, strategy=strategy)
            db_session.add(row)
        row.max_risk = max_risk
        row.max_profit = max_profit
        row.updated_at = datetime.now()
        db_session.commit()
    except Exception:
        db_session.rollback()
        logger.exception(f"Could not set risk profile for strategy {strategy}")
        raise


def get_strategy_risk_profile(user_id: str, strategy: str) -> dict | None:
    row = (
        db_session.query(StrategyRiskProfile).filter_by(user_id=user_id, strategy=strategy).first()
    )
    if row is None:
        return None
    return {
        "strategy": row.strategy,
        "max_risk": row.max_risk,
        "max_profit": row.max_profit,
        "recorded_at": row.recorded_at.isoformat() if row.recorded_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def clear_strategy_risk_profile(user_id: str, strategy: str) -> bool:
    """Drop a strategy's risk-profile snapshot, e.g. once it has fully closed
    and should no longer count toward capital-at-risk."""
    try:
        n = (
            db_session.query(StrategyRiskProfile)
            .filter_by(user_id=user_id, strategy=strategy)
            .delete()
        )
        db_session.commit()
        return n > 0
    except Exception:
        db_session.rollback()
        logger.exception(f"Could not clear risk profile for strategy {strategy}")
        return False
