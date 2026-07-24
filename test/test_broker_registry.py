"""Tests for utils/broker_registry.py."""

from __future__ import annotations

import pytest

from utils import broker_registry as reg


@pytest.mark.unit
def test_get_broker_from_redirect_url() -> None:
    assert reg.get_broker_from_redirect_url("http://127.0.0.1:5001/stock_simulator/callback") == "stock_simulator"
    assert reg.get_broker_from_redirect_url("http://127.0.0.1:5000/zerodha/callback") == "zerodha"
    assert reg.get_broker_from_redirect_url("") == ""


@pytest.mark.unit
def test_list_available_brokers_includes_stock_simulator(monkeypatch) -> None:
    monkeypatch.setenv(
        "VALID_BROKERS",
        "zerodha,stock_simulator,indmoney,alpaca,missing_broker",
    )
    brokers = reg.list_available_brokers()
    ids = {b.id for b in brokers}
    assert "stock_simulator" in ids
    assert "indmoney" in ids
    assert "alpaca" in ids
    assert "missing_broker" not in ids


@pytest.mark.unit
def test_stock_simulator_display_name(monkeypatch) -> None:
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator")
    desc = reg.get_broker_descriptor("stock_simulator")
    assert desc is not None
    assert desc.display_name == "NSE Simulator (Replay)"
    assert desc.auth_flow == "callback"
    assert desc.credentials_configured is True


@pytest.mark.unit
def test_default_broker_from_redirect(monkeypatch) -> None:
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")
    assert reg.get_default_broker() == "stock_simulator"


@pytest.mark.unit
def test_validate_registry_missing_plugin(monkeypatch) -> None:
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,not_a_real_broker")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/stock_simulator/callback")
    warnings = reg.validate_registry_at_startup()
    assert any("not_a_real_broker" in w for w in warnings)


@pytest.mark.unit
def test_build_connect_url_stock_simulator(monkeypatch) -> None:
    from utils.broker_login import build_connect_url

    monkeypatch.setenv("HOST_SERVER", "http://127.0.0.1:5001")
    url = build_connect_url("stock_simulator")
    assert url == "http://127.0.0.1:5001/stock_simulator/callback"


@pytest.mark.unit
def test_build_connect_url_dhan_oauth_init(monkeypatch) -> None:
    from utils.broker_login import build_connect_url

    monkeypatch.setenv("HOST_SERVER", "http://127.0.0.1:5000")
    url = build_connect_url("dhan")
    assert url == "http://127.0.0.1:5000/dhan/initiate-oauth"


@pytest.mark.unit
def test_sample_env_valid_brokers_has_trade_brokers() -> None:
    sample = reg.default_valid_brokers_sample()
    assert "stock_simulator" in sample
    assert "alpaca" in sample


@pytest.mark.unit
def test_resolve_default_broker_clamps_when_redirect_not_available(monkeypatch) -> None:
    monkeypatch.setenv("VALID_BROKERS", "stock_simulator,indmoney")
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/zerodha/callback")
    brokers = reg.list_available_brokers()
    resolved = reg.resolve_default_broker_for_list(b.id for b in brokers)
    assert resolved == "indmoney"
