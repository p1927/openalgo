"""Route-level tests for restx_api/portfolio_ledger.py -- the apikey-authed
counterpart to blueprints/portfolio_ledger.py, added so server-to-server
callers (e.g. trade_integrations' knowledge_engine) can query strategy-scoped
track record without a browser session. See
.claude/backlog/items/2026-08-22-financial-knowledge-engine.md."""

from __future__ import annotations

import pytest
from flask import Flask
from flask_restx import Api

import restx_api.portfolio_ledger as portfolio_ledger_api
from limiter import limiter


@pytest.fixture
def client(monkeypatch):
    app = Flask(__name__)
    app.config.update(TESTING=True, PROPAGATE_EXCEPTIONS=False)
    monkeypatch.setattr(limiter, "enabled", False)
    rest_api = Api(app)
    rest_api.add_namespace(portfolio_ledger_api.api, path="/strategyperformance")
    return app.test_client()


@pytest.mark.unit
def test_rejects_an_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: None)

    response = client.post("/strategyperformance/", json={"apikey": "bad-key"})

    assert response.status_code == 403
    assert response.get_json()["message"] == "Invalid openalgo apikey"


@pytest.mark.unit
def test_rejects_a_missing_api_key(client):
    response = client.post("/strategyperformance/", json={})

    assert response.status_code == 400


@pytest.mark.unit
def test_returns_all_time_performance_when_no_strategy_given(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_get_strategy_performance(user_id, strategy=None):
        captured["user_id"] = user_id
        captured["strategy"] = strategy
        return {"status": "success", "trade_count": 3}

    monkeypatch.setattr(
        portfolio_ledger_api, "get_strategy_performance", fake_get_strategy_performance
    )

    response = client.post("/strategyperformance/", json={"apikey": "key"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "success", "trade_count": 3}
    assert captured == {"user_id": "user-1", "strategy": None}


@pytest.mark.unit
def test_scopes_to_the_given_strategy(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_get_strategy_performance(user_id, strategy=None):
        captured["strategy"] = strategy
        return {"status": "success", "trade_count": 0}

    monkeypatch.setattr(
        portfolio_ledger_api, "get_strategy_performance", fake_get_strategy_performance
    )

    response = client.post(
        "/strategyperformance/", json={"apikey": "key", "strategy": "iron-condor-1"}
    )

    assert response.status_code == 200
    assert captured["strategy"] == "iron-condor-1"


@pytest.mark.unit
def test_legs_rejects_an_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: None)

    response = client.post("/strategyperformance/legs", json={"apikey": "bad-key"})

    assert response.status_code == 403
    assert response.get_json()["message"] == "Invalid openalgo apikey"


@pytest.mark.unit
def test_legs_rejects_a_missing_api_key(client):
    response = client.post("/strategyperformance/legs", json={})

    assert response.status_code == 400


@pytest.mark.unit
def test_legs_returns_all_legs_when_no_strategy_given(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_get_strategy_legs(user_id=None, strategy=None):
        captured["user_id"] = user_id
        captured["strategy"] = strategy
        return [{"strategy": "iron_condor", "symbol": "NIFTY24500CE"}]

    monkeypatch.setattr("database.strategy_book_db.get_strategy_legs", fake_get_strategy_legs)

    response = client.post("/strategyperformance/legs", json={"apikey": "key"})

    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["legs"] == [{"strategy": "iron_condor", "symbol": "NIFTY24500CE"}]
    assert captured == {"user_id": "user-1", "strategy": None}


@pytest.mark.unit
def test_legs_scopes_to_the_given_strategy(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_get_strategy_legs(user_id=None, strategy=None):
        captured["strategy"] = strategy
        return []

    monkeypatch.setattr("database.strategy_book_db.get_strategy_legs", fake_get_strategy_legs)

    response = client.post(
        "/strategyperformance/legs", json={"apikey": "key", "strategy": "iron-condor-1"}
    )

    assert response.status_code == 200
    assert captured["strategy"] == "iron-condor-1"


@pytest.mark.unit
def test_legs_degrades_gracefully_when_book_unavailable(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    from database.strategy_book_db import StrategyBookUnavailable

    def fake_get_strategy_legs(user_id=None, strategy=None):
        raise StrategyBookUnavailable("not initialized")

    monkeypatch.setattr("database.strategy_book_db.get_strategy_legs", fake_get_strategy_legs)

    response = client.post("/strategyperformance/legs", json={"apikey": "key"})

    assert response.status_code == 200
    assert response.get_json() == {"status": "unavailable", "legs": []}


@pytest.mark.unit
def test_riskprofile_rejects_an_invalid_api_key(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: None)

    response = client.post(
        "/strategyperformance/riskprofile",
        json={"apikey": "bad-key", "strategy": "iron-condor-1", "max_risk": 500},
    )

    assert response.status_code == 403
    assert response.get_json()["message"] == "Invalid openalgo apikey"


@pytest.mark.unit
def test_riskprofile_rejects_missing_required_fields(client):
    response = client.post("/strategyperformance/riskprofile", json={"apikey": "key"})

    assert response.status_code == 400
    body = response.get_json()
    assert "strategy" in body["message"]
    assert "max_risk" in body["message"]


@pytest.mark.unit
def test_riskprofile_rejects_a_negative_max_risk(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    response = client.post(
        "/strategyperformance/riskprofile",
        json={"apikey": "key", "strategy": "iron-condor-1", "max_risk": -500},
    )

    assert response.status_code == 400


@pytest.mark.unit
def test_riskprofile_persists_max_risk_and_max_profit(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_set_strategy_risk_profile(user_id, strategy, max_risk, max_profit=None):
        captured["user_id"] = user_id
        captured["strategy"] = strategy
        captured["max_risk"] = max_risk
        captured["max_profit"] = max_profit

    monkeypatch.setattr(
        "database.strategy_book_db.set_strategy_risk_profile", fake_set_strategy_risk_profile
    )

    response = client.post(
        "/strategyperformance/riskprofile",
        json={"apikey": "key", "strategy": "iron-condor-1", "max_risk": 500, "max_profit": 1200},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "status": "success",
        "strategy": "iron-condor-1",
        "max_risk": 500,
        "max_profit": 1200,
    }
    assert captured == {
        "user_id": "user-1",
        "strategy": "iron-condor-1",
        "max_risk": 500.0,
        "max_profit": 1200.0,
    }


@pytest.mark.unit
def test_riskprofile_defaults_max_profit_to_none_for_undefined_risk_positions(client, monkeypatch):
    monkeypatch.setattr(portfolio_ledger_api, "verify_api_key", lambda _key: "user-1")

    captured = {}

    def fake_set_strategy_risk_profile(user_id, strategy, max_risk, max_profit=None):
        captured["max_profit"] = max_profit

    monkeypatch.setattr(
        "database.strategy_book_db.set_strategy_risk_profile", fake_set_strategy_risk_profile
    )

    response = client.post(
        "/strategyperformance/riskprofile",
        json={"apikey": "key", "strategy": "long-call-1", "max_risk": 350},
    )

    assert response.status_code == 200
    assert response.get_json()["max_profit"] is None
    assert captured["max_profit"] is None
