"""Tests for per-broker credential resolution."""

from __future__ import annotations

import pytest

from utils import broker_credentials as bc


@pytest.mark.unit
def test_resolve_alpaca_from_dedicated_env(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "alpaca-key")
    monkeypatch.setenv("ALPACA_API_SECRET", "alpaca-secret")
    monkeypatch.setenv("BROKER_API_KEY", "legacy-key")
    monkeypatch.setenv("BROKER_API_SECRET", "legacy-secret")
    key, secret = bc.resolve_broker_credentials("alpaca")
    assert key == "alpaca-key"
    assert secret == "alpaca-secret"


@pytest.mark.unit
def test_resolve_indmoney_token(monkeypatch) -> None:
    monkeypatch.setenv("INDMONEY_ACCESS_TOKEN", "jwt-token-xyz")
    monkeypatch.delenv("BROKER_API_SECRET", raising=False)
    _key, secret = bc.resolve_broker_credentials("indmoney")
    assert secret == "jwt-token-xyz"


@pytest.mark.unit
def test_apply_sets_broker_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "k1")
    monkeypatch.setenv("ALPACA_API_SECRET", "s1")
    monkeypatch.setenv("BROKER_API_KEY", "old")
    monkeypatch.setenv("BROKER_API_SECRET", "old")
    bc.apply_broker_credentials("alpaca")
    import os

    assert os.environ["BROKER_API_KEY"] == "k1"
    assert os.environ["BROKER_API_SECRET"] == "s1"


@pytest.mark.unit
def test_generic_broker_prefix_fallback(monkeypatch) -> None:
    monkeypatch.setenv("KOTAK_API_KEY", "kotak-k")
    monkeypatch.setenv("KOTAK_API_SECRET", "kotak-s")
    key, secret = bc.resolve_broker_credentials("kotak")
    assert key == "kotak-k"
    assert secret == "kotak-s"


@pytest.mark.unit
def test_resolve_does_not_use_global_keys_for_other_broker(monkeypatch) -> None:
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/zerodha/callback")
    monkeypatch.setenv("ZERODHA_API_KEY", "zerodha-key")
    monkeypatch.setenv("BROKER_API_KEY", "zerodha-key")
    monkeypatch.delenv("DHAN_API_KEY", raising=False)
    monkeypatch.delenv("DHAN_API_SECRET", raising=False)
    key, secret = bc.resolve_broker_credentials("dhan")
    assert key == ""
    assert secret == ""


@pytest.mark.unit
def test_resolve_uses_global_keys_for_default_broker(monkeypatch) -> None:
    monkeypatch.setenv("REDIRECT_URL", "http://127.0.0.1:5001/zerodha/callback")
    monkeypatch.delenv("ZERODHA_API_KEY", raising=False)
    monkeypatch.setenv("BROKER_API_KEY", "legacy-key")
    monkeypatch.setenv("BROKER_API_SECRET", "legacy-secret")
    key, secret = bc.resolve_broker_credentials("zerodha")
    assert key == "legacy-key"
    assert secret == "legacy-secret"
