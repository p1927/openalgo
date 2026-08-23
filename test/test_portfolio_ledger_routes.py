"""Route-level tests for blueprints/portfolio_ledger.py — the first live
caller of services/portfolio_ledger_service.py (previously exercised only by
its own unit tests, per
.claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md's
"Attempts" log)."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blueprints.portfolio_ledger as portfolio_ledger_module  # noqa: E402
from blueprints.portfolio_ledger import portfolio_ledger_bp  # noqa: E402


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    application.register_blueprint(portfolio_ledger_bp)
    return application


@pytest.fixture()
def client(app, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_module.limiter, "enabled", False)
    return app.test_client()


def _log_in(client):
    with client.session_transaction() as sess:
        sess["user"] = "prat"
        sess["logged_in"] = True
        sess["login_time"] = datetime.now(UTC).isoformat()
        sess["broker"] = "stock_simulator"


@pytest.mark.unit
def test_capital_account_requires_a_session(client):
    response = client.get("/portfolio-ledger/api/capital-account")

    assert response.status_code in (302, 401)


@pytest.mark.unit
def test_capital_account_requires_an_api_key(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: None
    )

    response = client.get("/portfolio-ledger/api/capital-account")

    assert response.status_code == 401
    assert response.get_json()["status"] == "error"


@pytest.mark.unit
def test_capital_account_404_when_no_sandbox_funds_row(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")
    monkeypatch.setattr(portfolio_ledger_module, "get_capital_account", lambda _user_id: None)

    response = client.get("/portfolio-ledger/api/capital-account")

    assert response.status_code == 404


@pytest.mark.unit
def test_capital_account_returns_the_service_payload(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")
    monkeypatch.setattr(
        portfolio_ledger_module,
        "get_capital_account",
        lambda user_id: {"total_capital": 1000.0} if user_id == "user-1" else None,
    )

    response = client.get("/portfolio-ledger/api/capital-account")

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "success"
    assert body["total_capital"] == 1000.0


@pytest.mark.unit
def test_rollup_propagates_the_service_error_status(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")
    monkeypatch.setattr(
        portfolio_ledger_module,
        "get_portfolio_rollup",
        lambda _user_id: {"status": "error", "message": "No sandbox funds found for user user-1"},
    )

    response = client.get("/portfolio-ledger/api/rollup")

    assert response.status_code == 404
    assert response.get_json()["status"] == "error"


@pytest.mark.unit
def test_rollup_returns_the_service_payload_on_success(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")
    monkeypatch.setattr(
        portfolio_ledger_module,
        "get_portfolio_rollup",
        lambda _user_id: {
            "status": "success",
            "capital_at_risk": 500.0,
            "banked_pnl": 750.0,
            "safe_to_withdraw": 250.0,
        },
    )

    response = client.get("/portfolio-ledger/api/rollup")

    assert response.status_code == 200
    body = response.get_json()
    assert body["safe_to_withdraw"] == 250.0


@pytest.mark.unit
def test_performance_passes_the_strategy_query_param_through(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")

    captured = {}

    def fake_performance(user_id, strategy=None):
        captured["user_id"] = user_id
        captured["strategy"] = strategy
        return {"status": "success", "trade_count": 0}

    monkeypatch.setattr(portfolio_ledger_module, "get_strategy_performance", fake_performance)

    response = client.get("/portfolio-ledger/api/performance?strategy=iron-condor-1")

    assert response.status_code == 200
    assert captured == {"user_id": "user-1", "strategy": "iron-condor-1"}


@pytest.mark.unit
def test_performance_defaults_to_no_strategy_scope(client, monkeypatch):
    _log_in(client)
    monkeypatch.setattr(
        portfolio_ledger_module, "get_api_key_for_tradingview", lambda _username: "key"
    )
    monkeypatch.setattr(portfolio_ledger_module, "get_user_id_from_apikey", lambda _key: "user-1")

    captured = {}

    def fake_performance(user_id, strategy=None):
        captured["strategy"] = strategy
        return {"status": "success", "trade_count": 0}

    monkeypatch.setattr(portfolio_ledger_module, "get_strategy_performance", fake_performance)

    response = client.get("/portfolio-ledger/api/performance")

    assert response.status_code == 200
    assert captured["strategy"] is None
