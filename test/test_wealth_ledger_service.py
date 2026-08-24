"""
Unit tests for the agent-actual vs. shadow-track wealth comparison
(services/wealth_ledger_service.py, 2026-08-25-wealth-accumulation-ledger-live-agent).

Exercises real DB writes against the test sandbox/strategy-book databases for the
agent-actual side (mirroring test_portfolio_ledger_service.py's own convention), and a real
tmp-path parquet ledger for the shadow side (mirroring trade_integrations's own
shadow_pnl.py test convention), rather than mocking either store.

Run with: uv run pytest test/test_wealth_ledger_service.py -v
"""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module", autouse=True)
def _init_dbs():
    from database.sandbox_db import init_db as init_sandbox_db
    from database.strategy_book_db import init_strategy_book_db

    init_sandbox_db()
    init_strategy_book_db()
    yield


@pytest.fixture
def user_id():
    return f"test-wealth-ledger-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def agent_id():
    return f"agent-wealth-ledger-{uuid.uuid4().hex[:12]}"


@pytest.fixture
def hub_tmp(monkeypatch: pytest.MonkeyPatch, tmp_path):
    hub = tmp_path / "hub"
    hub.mkdir(parents=True)
    monkeypatch.setenv("TRADE_STACK_HUB_DIR", str(hub))
    return hub


def _add_closed_trade(user_id: str, strategy: str, realized_pnl: float, *, symbol: str = "SYM"):
    from database.strategy_book_db import StrategyClosedTrade, db_session

    db_session.add(
        StrategyClosedTrade(
            user_id=user_id,
            strategy=strategy,
            symbol=symbol,
            exchange="NFO",
            product="NRML",
            closed_quantity=65.0,
            entry_price=100.0,
            exit_price=100.0,
            realized_pnl=realized_pnl,
        )
    )
    db_session.commit()


def _add_shadow_entry(agent_id: str, *, strategy="s", shadow_pnl: float, actual_pnl: float | None = None):
    from trade_integrations.autonomous_agents.shadow_pnl import (
        ShadowPnlEntry,
        record_shadow_pnl_entry,
    )

    record_shadow_pnl_entry(
        ShadowPnlEntry(
            agent_id=agent_id,
            symbol="NIFTY",
            strategy=strategy,
            entry_at="2026-08-20T00:00:00+00:00",
            exit_at="2026-08-21T00:00:00+00:00",
            direction="bullish",
            confidence=0.6,
            entry_spot=24000.0,
            exit_spot=24100.0,
            shadow_return_pct=0.4,
            shadow_pnl_inr=shadow_pnl,
            agent_actual_pnl_inr=actual_pnl,
        )
    )


class TestGetAgentVsShadowWealth:
    def test_empty_both_sides_returns_success_not_error(self, user_id, agent_id, hub_tmp):
        from services.wealth_ledger_service import get_agent_vs_shadow_wealth

        result = get_agent_vs_shadow_wealth(user_id, agent_id)
        assert result["status"] == "success"
        assert result["agent_actual"]["trade_count"] == 0
        assert result["shadow"]["trade_count"] == 0
        assert result["wealth_curve"] == []

    def test_combines_real_agent_actual_and_shadow_tracks(self, user_id, agent_id, hub_tmp):
        from services.wealth_ledger_service import get_agent_vs_shadow_wealth

        _add_closed_trade(user_id, "s", realized_pnl=200.0)
        _add_closed_trade(user_id, "s", realized_pnl=-50.0)
        _add_shadow_entry(agent_id, shadow_pnl=1000.0, actual_pnl=200.0)
        _add_shadow_entry(agent_id, shadow_pnl=-200.0, actual_pnl=-50.0)

        result = get_agent_vs_shadow_wealth(user_id, agent_id)
        assert result["agent_actual"]["trade_count"] == 2
        assert result["agent_actual"]["net_pnl"] == pytest.approx(150.0)
        assert result["shadow"]["trade_count"] == 2
        assert result["shadow"]["net_pnl"] == pytest.approx(800.0)
        assert result["summary"]["shadow_total_pnl_inr"] == pytest.approx(800.0)
        assert result["summary"]["agent_actual_total_pnl_inr"] == pytest.approx(150.0)
        assert len(result["wealth_curve"]) == 2

    def test_scoped_by_strategy_on_both_sides(self, user_id, agent_id, hub_tmp):
        from services.wealth_ledger_service import get_agent_vs_shadow_wealth

        _add_closed_trade(user_id, "strat_a", realized_pnl=100.0)
        _add_closed_trade(user_id, "strat_b", realized_pnl=-999.0)
        _add_shadow_entry(agent_id, strategy="strat_a", shadow_pnl=300.0)
        _add_shadow_entry(agent_id, strategy="strat_b", shadow_pnl=-999.0)

        result = get_agent_vs_shadow_wealth(user_id, agent_id, strategy="strat_a")
        assert result["agent_actual"]["trade_count"] == 1
        assert result["agent_actual"]["net_pnl"] == pytest.approx(100.0)
        assert result["shadow"]["trade_count"] == 1
        assert result["shadow"]["net_pnl"] == pytest.approx(300.0)
