"""
Unit tests for the capital/risk-reward ledger (services/portfolio_ledger_service.py).

Exercises real DB writes against the test sandbox/strategy-book databases
(see test/conftest.py) rather than mocking the store layer, since the whole
point of this module is combining two existing tables correctly.

Run with: uv run pytest test/test_portfolio_ledger_service.py -v
"""

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
    """A fresh user id per test — the test databases are real sqlite files
    shared across the run, so reusing one id would let tests see each
    other's rows."""
    return f"test-portfolio-ledger-{uuid.uuid4().hex[:12]}"


def _open_funds(user_id: str, *, realized_pnl: float = 0.0):
    from sandbox.fund_manager import FundManager

    fm = FundManager(user_id)
    fm.initialize_funds()

    from database.sandbox_db import SandboxFunds, db_session

    funds = SandboxFunds.query.filter_by(user_id=user_id).first()
    funds.realized_pnl = realized_pnl
    db_session.commit()


def _add_leg(
    user_id: str, strategy: str, symbol: str, quantity: float, average_price: float = 100.0
):
    from database.strategy_book_db import StrategyPosition, db_session

    db_session.add(
        StrategyPosition(
            user_id=user_id,
            strategy=strategy,
            symbol=symbol,
            exchange="NFO",
            product="NRML",
            quantity=quantity,
            average_price=average_price,
        )
    )
    db_session.commit()


class TestGetCapitalAccount:
    def test_returns_none_for_unknown_user(self, user_id):
        from services.portfolio_ledger_service import get_capital_account

        assert get_capital_account(user_id) is None

    def test_reads_the_sandbox_funds_row(self, user_id):
        from services.portfolio_ledger_service import get_capital_account

        _open_funds(user_id, realized_pnl=1234.5)

        account = get_capital_account(user_id)
        assert account is not None
        assert account["realized_pnl"] == 1234.5
        assert account["total_capital"] > 0


class TestGetPortfolioRollup:
    def test_error_for_unknown_user(self, user_id):
        from services.portfolio_ledger_service import get_portfolio_rollup

        result = get_portfolio_rollup(user_id)
        assert result["status"] == "error"

    def test_no_open_strategies_is_all_banked_and_none_at_risk(self, user_id):
        from services.portfolio_ledger_service import get_portfolio_rollup

        _open_funds(user_id, realized_pnl=5000.0)

        result = get_portfolio_rollup(user_id)
        assert result["status"] == "success"
        assert result["capital_at_risk"] == 0.0
        assert result["banked_pnl"] == 5000.0
        assert result["safe_to_withdraw"] == 5000.0
        assert result["open_strategies"] == []
        assert result["unprofiled_open_strategies"] == []

    def test_open_risk_profiled_strategy_counts_against_capital_at_risk(self, user_id):
        from database.strategy_book_db import set_strategy_risk_profile
        from services.portfolio_ledger_service import get_portfolio_rollup

        _open_funds(user_id, realized_pnl=10000.0)
        _add_leg(user_id, "bear_put_spread", "NIFTY24AUG2624250PE", quantity=65)
        set_strategy_risk_profile(user_id, "bear_put_spread", max_risk=2291.25, max_profit=4208.75)

        result = get_portfolio_rollup(user_id)
        assert result["status"] == "success"
        assert result["capital_at_risk"] == 2291.25
        assert result["banked_pnl"] == 10000.0
        assert result["safe_to_withdraw"] == pytest.approx(10000.0 - 2291.25)
        assert [s["strategy"] for s in result["open_strategies"]] == ["bear_put_spread"]
        assert result["unprofiled_open_strategies"] == []

    def test_closed_strategy_does_not_count_against_capital_at_risk(self, user_id):
        from database.strategy_book_db import set_strategy_risk_profile
        from services.portfolio_ledger_service import get_portfolio_rollup

        _open_funds(user_id, realized_pnl=10000.0)
        # Flat leg (quantity 0) — the strategy has closed.
        _add_leg(user_id, "closed_straddle", "NIFTY24AUG2624250CE", quantity=0)
        set_strategy_risk_profile(user_id, "closed_straddle", max_risk=1500.0)

        result = get_portfolio_rollup(user_id)
        assert result["capital_at_risk"] == 0.0
        assert result["open_strategies"] == []

    def test_open_strategy_missing_a_risk_profile_is_surfaced_not_silently_dropped(self, user_id):
        from services.portfolio_ledger_service import get_portfolio_rollup

        _open_funds(user_id, realized_pnl=10000.0)
        _add_leg(user_id, "unprofiled_strategy", "NIFTY24AUG2624250CE", quantity=65)

        result = get_portfolio_rollup(user_id)
        assert result["capital_at_risk"] == 0.0
        assert result["open_strategies"] == []
        assert result["unprofiled_open_strategies"] == ["unprofiled_strategy"]

    def test_multiple_open_strategies_sum_into_capital_at_risk(self, user_id):
        from database.strategy_book_db import set_strategy_risk_profile
        from services.portfolio_ledger_service import get_portfolio_rollup

        _open_funds(user_id, realized_pnl=10000.0)
        _add_leg(user_id, "spread_a", "LEG_A", quantity=65)
        set_strategy_risk_profile(user_id, "spread_a", max_risk=1000.0)
        _add_leg(user_id, "spread_b", "LEG_B", quantity=-65)
        set_strategy_risk_profile(user_id, "spread_b", max_risk=2500.0)

        result = get_portfolio_rollup(user_id)
        assert result["capital_at_risk"] == 3500.0
        assert result["safe_to_withdraw"] == pytest.approx(6500.0)


class TestStrategyRiskProfileCrud:
    def test_set_then_get_round_trips(self, user_id):
        from database.strategy_book_db import get_strategy_risk_profile, set_strategy_risk_profile

        set_strategy_risk_profile(user_id, "iron_condor", max_risk=800.0, max_profit=200.0)

        profile = get_strategy_risk_profile(user_id, "iron_condor")
        assert profile["max_risk"] == 800.0
        assert profile["max_profit"] == 200.0

    def test_undefined_risk_position_has_no_max_profit(self, user_id):
        from database.strategy_book_db import get_strategy_risk_profile, set_strategy_risk_profile

        set_strategy_risk_profile(user_id, "long_call", max_risk=350.0, max_profit=None)

        profile = get_strategy_risk_profile(user_id, "long_call")
        assert profile["max_profit"] is None

    def test_re_recording_a_strategy_overwrites_not_duplicates(self, user_id):
        from database.strategy_book_db import get_strategy_risk_profile, set_strategy_risk_profile

        set_strategy_risk_profile(user_id, "iron_condor", max_risk=800.0)
        set_strategy_risk_profile(user_id, "iron_condor", max_risk=650.0)

        profile = get_strategy_risk_profile(user_id, "iron_condor")
        assert profile["max_risk"] == 650.0

    def test_get_unknown_strategy_returns_none(self, user_id):
        from database.strategy_book_db import get_strategy_risk_profile

        assert get_strategy_risk_profile(user_id, "never-traded") is None

    def test_clear_removes_the_profile(self, user_id):
        from database.strategy_book_db import (
            clear_strategy_risk_profile,
            get_strategy_risk_profile,
            set_strategy_risk_profile,
        )

        set_strategy_risk_profile(user_id, "iron_condor", max_risk=800.0)
        assert clear_strategy_risk_profile(user_id, "iron_condor") is True

        assert get_strategy_risk_profile(user_id, "iron_condor") is None

    def test_clear_unknown_strategy_returns_false(self, user_id):
        from database.strategy_book_db import clear_strategy_risk_profile

        assert clear_strategy_risk_profile(user_id, "never-traded") is False


def _add_closed_trade(
    user_id: str,
    strategy: str,
    realized_pnl: float,
    *,
    symbol: str = "SYM",
    closed_quantity: float = 65.0,
    entry_price: float = 100.0,
    exit_price: float = 100.0,
):
    from database.strategy_book_db import StrategyClosedTrade, db_session

    db_session.add(
        StrategyClosedTrade(
            user_id=user_id,
            strategy=strategy,
            symbol=symbol,
            exchange="NFO",
            product="NRML",
            closed_quantity=closed_quantity,
            entry_price=entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
        )
    )
    db_session.commit()


class TestClosedTradeLog:
    def test_apply_fill_open_then_close_writes_one_closed_trade_row(self, user_id):
        """End-to-end through the real fill-application path, not a direct-row
        fixture — proves the write hook in `_apply_fill_locked` actually
        fires on a realistic open-then-close fill sequence."""
        from database.strategy_book_db import apply_fill, get_closed_trades, record_order_tag

        record_order_tag(f"{user_id}-OPEN", user_id, "e2e_strategy", "SYM", "NFO", "NRML")
        apply_fill(f"{user_id}-OPEN", filled_quantity=65, average_price=100.0, action="BUY")

        record_order_tag(f"{user_id}-CLOSE", user_id, "e2e_strategy", "SYM", "NFO", "NRML")
        apply_fill(f"{user_id}-CLOSE", filled_quantity=65, average_price=120.0, action="SELL")

        trades = get_closed_trades(user_id=user_id, strategy="e2e_strategy")
        assert len(trades) == 1
        trade = trades[0]
        assert trade["closed_quantity"] == 65.0
        assert trade["entry_price"] == 100.0
        assert trade["exit_price"] == 120.0
        assert trade["realized_pnl"] == pytest.approx(65.0 * (120.0 - 100.0))

    def test_partial_close_writes_its_own_row_not_the_whole_leg(self, user_id):
        from database.strategy_book_db import apply_fill, get_closed_trades, record_order_tag

        record_order_tag(f"{user_id}-OPEN2", user_id, "partial_strategy", "SYM2", "NFO", "NRML")
        apply_fill(f"{user_id}-OPEN2", filled_quantity=100, average_price=50.0, action="BUY")

        record_order_tag(f"{user_id}-PARTIAL", user_id, "partial_strategy", "SYM2", "NFO", "NRML")
        apply_fill(f"{user_id}-PARTIAL", filled_quantity=40, average_price=60.0, action="SELL")

        trades = get_closed_trades(user_id=user_id, strategy="partial_strategy")
        assert len(trades) == 1
        assert trades[0]["closed_quantity"] == 40.0
        assert trades[0]["realized_pnl"] == pytest.approx(40.0 * (60.0 - 50.0))

    def test_get_closed_trades_filters_by_strategy(self, user_id):
        from database.strategy_book_db import get_closed_trades

        _add_closed_trade(user_id, "strat_a", realized_pnl=100.0)
        _add_closed_trade(user_id, "strat_b", realized_pnl=-50.0)

        assert len(get_closed_trades(user_id=user_id, strategy="strat_a")) == 1
        assert len(get_closed_trades(user_id=user_id)) == 2


class TestGetStrategyPerformance:
    def test_no_trades_returns_empty_shape_not_an_error(self, user_id):
        from services.portfolio_ledger_service import get_strategy_performance

        result = get_strategy_performance(user_id)
        assert result["status"] == "success"
        assert result["trade_count"] == 0
        assert result["win_rate"] is None
        assert result["expectancy"] is None

    def test_win_rate_and_expectancy_over_mixed_trades(self, user_id):
        from services.portfolio_ledger_service import get_strategy_performance

        _add_closed_trade(user_id, "s", realized_pnl=200.0)
        _add_closed_trade(user_id, "s", realized_pnl=300.0)
        _add_closed_trade(user_id, "s", realized_pnl=-100.0)

        result = get_strategy_performance(user_id, strategy="s")
        assert result["trade_count"] == 3
        assert result["win_count"] == 2
        assert result["loss_count"] == 1
        assert result["win_rate"] == pytest.approx(2 / 3, abs=1e-4)
        assert result["average_win"] == pytest.approx(250.0)
        assert result["average_loss"] == pytest.approx(-100.0)
        # expectancy = (2/3 * 250) + (1/3 * -100)
        assert result["expectancy"] == pytest.approx((2 / 3) * 250.0 + (1 / 3) * -100.0, abs=1e-2)
        assert result["net_pnl"] == pytest.approx(400.0)
        assert result["gross_profit"] == pytest.approx(500.0)
        assert result["gross_loss"] == pytest.approx(-100.0)

    def test_all_losing_trades_gives_zero_win_rate(self, user_id):
        from services.portfolio_ledger_service import get_strategy_performance

        _add_closed_trade(user_id, "losers", realized_pnl=-50.0)
        _add_closed_trade(user_id, "losers", realized_pnl=-75.0)

        result = get_strategy_performance(user_id, strategy="losers")
        assert result["win_rate"] == 0.0
        assert result["average_win"] == 0.0
        assert result["expectancy"] == pytest.approx(-62.5)

    def test_scoped_to_one_strategy_excludes_others(self, user_id):
        from services.portfolio_ledger_service import get_strategy_performance

        _add_closed_trade(user_id, "only_this", realized_pnl=100.0)
        _add_closed_trade(user_id, "not_this", realized_pnl=-1000.0)

        result = get_strategy_performance(user_id, strategy="only_this")
        assert result["trade_count"] == 1
        assert result["net_pnl"] == pytest.approx(100.0)

    def test_no_strategy_filter_aggregates_across_all(self, user_id):
        from services.portfolio_ledger_service import get_strategy_performance

        _add_closed_trade(user_id, "a", realized_pnl=100.0)
        _add_closed_trade(user_id, "b", realized_pnl=50.0)

        result = get_strategy_performance(user_id)
        assert result["trade_count"] == 2
        assert result["net_pnl"] == pytest.approx(150.0)
