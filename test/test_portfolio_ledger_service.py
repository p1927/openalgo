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
