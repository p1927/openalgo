"""Tests for Alpaca broker market data adapter."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from broker.alpaca.api.data import BrokerData
from services import quotes_service as qs


SNAPSHOT = {
    "symbol": "AAPL",
    "latestQuote": {"bp": 149.5, "ap": 149.7},
    "latestTrade": {"p": 149.6},
    "dailyBar": {"o": 148.0, "h": 151.0, "l": 147.5, "c": 149.6, "v": 1000000},
}


@pytest.mark.unit
def test_broker_data_get_quotes() -> None:
    with patch("broker.alpaca.api.data.alpaca_request", return_value=(200, SNAPSHOT)):
        handler = BrokerData("test-key")
        quote = handler.get_quotes("AAPL", "NASDAQ")

    assert quote["ltp"] == 149.6
    assert quote["bid"] == 149.5
    assert quote["ask"] == 149.7
    assert quote["open"] == 148.0
    assert quote["high"] == 151.0
    assert quote["low"] == 147.5
    assert quote["close"] == 149.6
    assert quote["volume"] == 1000000


@pytest.mark.unit
def test_broker_data_get_history() -> None:
    bars_payload = {
        "bars": [
            {"t": "2024-01-02T00:00:00Z", "o": 100, "h": 101, "l": 99, "c": 100.5, "v": 5000},
        ]
    }
    with patch("broker.alpaca.api.data.alpaca_request", return_value=(200, bars_payload)):
        handler = BrokerData("test-key")
        frame = handler.get_history("AAPL", "NASDAQ", "D", "2024-01-01", "2024-01-03")

    assert len(frame) == 1
    assert float(frame.iloc[0]["close"]) == 100.5


@pytest.mark.unit
def test_validate_symbol_exchange_skips_token_for_alpaca() -> None:
    ok, err = qs.validate_symbol_exchange("AAPL", "NASDAQ", broker="alpaca")
    assert ok is True
    assert err is None


@pytest.mark.unit
def test_get_quotes_with_auth_alpaca_no_token_db() -> None:
    with patch("broker.alpaca.api.data.alpaca_request", return_value=(200, SNAPSHOT)):
        ok, payload, status = qs.get_quotes_with_auth(
            "test-key", None, "alpaca", "AAPL", "NASDAQ"
        )

    assert ok is True
    assert status == 200
    assert payload["status"] == "success"
    assert payload["data"]["ltp"] == 149.6


@pytest.mark.unit
def test_transform_openalgo_order_market() -> None:
    from broker.alpaca.mapping.order_data import transform_openalgo_order

    body = transform_openalgo_order(
        {
            "symbol": "AAPL",
            "exchange": "NASDAQ",
            "action": "BUY",
            "quantity": 5,
            "pricetype": "MARKET",
        }
    )
    assert body["symbol"] == "AAPL"
    assert body["side"] == "buy"
    assert body["type"] == "market"
    assert body["qty"] == "5"
