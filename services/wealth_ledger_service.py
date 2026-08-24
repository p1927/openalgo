"""Agent-actual vs. shadow-track wealth comparison — extends module 9's portfolio
ledger (see .claude/backlog/archive/items/2026-08-22-profit-accumulation-portfolio-ledger.md)
to sit the connected autonomous agent's real accumulation curve next to the prediction
platform's shadow (counterfactual) accumulation curve, per
.claude/backlog/items/2026-08-25-wealth-accumulation-ledger-live-agent.md.

Part (a) of that item — feeding the agent's real trades into `StrategyClosedTrade` as an
ongoing source — needed no new code: every real order the connected agent places already
flows through `services/place_order_service.py` -> the `basketorder`/order event bus ->
`subscribers/strategy_book_subscriber.py`, which has tagged and booked every strategy-tagged
fill (sandbox or live) since module 9 shipped. `get_strategy_performance` (below, reused
as-is) already reflects that continuous real feed with zero new wiring.

Part (b) — running the same rollup a second time over the shadow track — genuinely needs new
code, because the shadow track lives in `trade_integrations` (a separate package this app can
reach via `broker.stock_simulator.api._trade_path.ensure_trade_integrations_path()`'s
existing sys.path hydration, the same mechanism `marketcontext_service.py` already uses — but
which cannot import this Flask/SQLAlchemy app back; see the backlog item's Attempts log for
the verified import-direction check) and duplicates only the win-rate/expectancy *formula*,
not the table.
"""

from __future__ import annotations

from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


def get_agent_vs_shadow_wealth(user_id: str, agent_id: str, *, strategy: str | None = None) -> dict[str, Any]:
    """Side-by-side wealth view: the agent-actual track (module 9's existing real capital
    ledger, `StrategyClosedTrade`-backed) next to the shadow track (the prediction-ledger
    recommendation's counterfactual, `trade_integrations.autonomous_agents.shadow_pnl`).

    `user_id` scopes the real OpenAlgo/sandbox side; `agent_id` scopes the shadow side — the
    two identifiers are different systems' keys for (in the common case) the same connected
    agent, not interchangeable, so both are required rather than assumed equal.
    """
    from services.portfolio_ledger_service import get_strategy_performance

    agent_actual = get_strategy_performance(user_id, strategy=strategy)

    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.autonomous_agents.shadow_pnl import (
            shadow_strategy_performance,
            shadow_vs_actual_summary,
            wealth_curve,
        )

        shadow = shadow_strategy_performance(agent_id, strategy=strategy)
        summary = shadow_vs_actual_summary(agent_id)
        curve = wealth_curve(agent_id)
    except Exception:
        logger.exception("shadow-track read failed for agent_id=%s", agent_id)
        shadow = {"status": "error", "message": "shadow track unavailable"}
        summary = None
        curve = []

    return {
        "status": "success",
        "user_id": user_id,
        "agent_id": agent_id,
        "agent_actual": agent_actual,
        "shadow": shadow,
        "summary": summary,
        "wealth_curve": curve,
    }
