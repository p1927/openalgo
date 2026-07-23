"""Tests for marketcontext_service."""

from __future__ import annotations

import pytest

from services import marketcontext_service as svc


@pytest.mark.unit
def test_build_marketcontext_alpaca_paper() -> None:
    data = svc.build_marketcontext_data(broker="alpaca", analyze_mode=True)
    assert data["data_broker"] == "alpaca"
    assert data["market_region"] == "US"
    assert data["execution_venue"] == "paper-api.alpaca.markets"
    assert data["positions_authority"] == "alpaca.paper"
    assert data["capabilities"] == ["equity", "basket"]
    assert "options" not in data["capabilities"]
    assert "websocket" not in data["capabilities"]


@pytest.mark.unit
def test_build_marketcontext_alpaca_live() -> None:
    data = svc.build_marketcontext_data(broker="alpaca", analyze_mode=False)
    assert data["execution_venue"] == "api.alpaca.markets"
    assert data["positions_authority"] == "alpaca"
    assert data["capabilities"] == ["equity", "basket"]


@pytest.mark.unit
def test_build_marketcontext_analyze_on() -> None:
    data = svc.build_marketcontext_data(broker="zerodha", analyze_mode=True)
    assert data["data_broker"] == "zerodha"
    assert data["execution_venue"] == "sandbox"
    assert data["positions_authority"] == "sandbox.db"
    assert data["analyze_mode"] is True
    assert data["market_region"] == "IN"
    assert data["simulator"] == {"active": False}
    assert "context_generation" in data
    assert "websocket" in data["capabilities"]


@pytest.mark.unit
def test_build_marketcontext_analyze_off() -> None:
    data = svc.build_marketcontext_data(broker="zerodha", analyze_mode=False)
    assert data["execution_venue"] == "broker"
    assert data["positions_authority"] == "broker"
    assert data["analyze_mode"] is False


@pytest.mark.unit
def test_build_marketcontext_stock_simulator(monkeypatch) -> None:
    monkeypatch.setattr(
        svc,
        "_simulator_block",
        lambda _broker: {
            "active": True,
            "replay_date": "2024-01-15",
            "session_open": True,
        },
    )
    data = svc.build_marketcontext_data(broker="stock_simulator", analyze_mode=True)
    assert data["simulator"]["active"] is True
    assert data["simulator"]["replay_date"] == "2024-01-15"


@pytest.mark.unit
def test_get_marketcontext_requires_api_key() -> None:
    ok, payload, status = svc.get_marketcontext(api_key=None)
    assert ok is False
    assert status == 400


@pytest.mark.unit
def test_get_marketcontext_invalid_api_key(monkeypatch) -> None:
    monkeypatch.setattr(svc, "verify_api_key", lambda _key: None)
    ok, payload, status = svc.get_marketcontext(api_key="bad-key")
    assert ok is False
    assert status == 403


@pytest.mark.unit
def test_get_marketcontext_success(monkeypatch) -> None:
    monkeypatch.setattr(svc, "verify_api_key", lambda _key: "admin")
    monkeypatch.setattr(svc, "get_broker_name", lambda _key: "zerodha")
    monkeypatch.setattr(svc, "get_configured_broker", lambda: "zerodha")
    monkeypatch.setattr(svc, "get_analyze_mode", lambda: True)
    ok, payload, status = svc.get_marketcontext(api_key="good-key")
    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert payload["data"]["data_broker"] == "zerodha"
    assert payload["data"]["execution_venue"] == "sandbox"
