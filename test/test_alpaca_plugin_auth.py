"""Tests for Alpaca broker plugin authentication."""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.mark.unit
def test_authenticate_broker_from_env(monkeypatch) -> None:
    monkeypatch.setenv("BROKER_API_KEY", "test-key")
    monkeypatch.setenv("BROKER_API_SECRET", "test-secret")

    account = {"status": "ACTIVE", "account_number": "PA123"}
    with patch("broker.alpaca.api.auth_api.alpaca_request", return_value=(200, account)):
        from broker.alpaca.api.auth_api import authenticate_broker

        token, error = authenticate_broker("alpaca")

    assert error is None
    assert token == "test-key"


@pytest.mark.unit
def test_authenticate_broker_key_secret_login(monkeypatch) -> None:
    monkeypatch.delenv("BROKER_API_KEY", raising=False)
    account = {"status": "ACTIVE", "account_number": "PA456"}
    with patch("broker.alpaca.api.auth_api.alpaca_request", return_value=(200, account)):
        from broker.alpaca.api.auth_api import authenticate_broker

        token, error = authenticate_broker("login-key:login-secret")

    assert error is None
    assert token == "login-key"


@pytest.mark.unit
def test_authenticate_broker_failure(monkeypatch) -> None:
    monkeypatch.setenv("BROKER_API_KEY", "bad-key")
    monkeypatch.setenv("BROKER_API_SECRET", "bad-secret")
    with patch(
        "broker.alpaca.api.auth_api.alpaca_request",
        return_value=(401, {"message": "unauthorized"}),
    ):
        from broker.alpaca.api.auth_api import authenticate_broker

        token, error = authenticate_broker("alpaca")

    assert token is None
    assert "401" in (error or "")


@pytest.mark.unit
def test_get_direct_access_token_valid(monkeypatch) -> None:
    monkeypatch.setenv("BROKER_API_SECRET", "test-secret")
    with patch(
        "broker.alpaca.api.auth_api.alpaca_request",
        return_value=(200, {"status": "ACTIVE"}),
    ):
        from broker.alpaca.api.auth_api import get_direct_access_token

        token, error = get_direct_access_token("stored-key")

    assert error is None
    assert token == "stored-key"


@pytest.mark.unit
@patch("broker.alpaca.api.baseurl.get_analyze_mode", return_value=True)
def test_trade_base_paper_when_analyze_on(_analyze) -> None:
    from broker.alpaca.api.baseurl import PAPER_HOST, trade_base

    assert trade_base() == PAPER_HOST


@pytest.mark.unit
@patch("broker.alpaca.api.baseurl.get_analyze_mode", return_value=False)
def test_trade_base_live_when_profile_live(_analyze, monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_PROFILE", "live")
    from broker.alpaca.api.baseurl import LIVE_HOST, trade_base

    assert trade_base() == LIVE_HOST
