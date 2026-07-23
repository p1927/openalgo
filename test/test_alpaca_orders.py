"""Tests for Alpaca broker order execution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from broker.alpaca.api import order_api
from broker.alpaca.mapping.order_data import map_order_data, transform_positions_data


ORDER_RESPONSE = {
    "id": "ord-abc",
    "symbol": "AAPL",
    "qty": "10",
    "side": "buy",
    "type": "market",
    "status": "accepted",
}


@pytest.mark.unit
def test_place_order_api() -> None:
    with patch("broker.alpaca.api.order_api.alpaca_request", return_value=(200, ORDER_RESPONSE)):
        res, payload, orderid = order_api.place_order_api(
            {
                "symbol": "AAPL",
                "exchange": "NASDAQ",
                "action": "BUY",
                "quantity": 10,
                "pricetype": "MARKET",
            },
            "test-key",
        )

    assert res.status == 200
    assert orderid == "ord-abc"
    assert payload["symbol"] == "AAPL"


@pytest.mark.unit
def test_get_positions() -> None:
    positions = [
        {
            "symbol": "AAPL",
            "qty": "5",
            "side": "long",
            "avg_entry_price": "150",
            "current_price": "155",
            "unrealized_pl": "25",
        }
    ]
    with patch("broker.alpaca.api.order_api.alpaca_request", return_value=(200, positions)):
        rows = order_api.get_positions("test-key")

    assert isinstance(rows, list)
    assert rows[0]["symbol"] == "AAPL"


@pytest.mark.unit
def test_cancel_order_success() -> None:
    with patch("broker.alpaca.api.order_api.alpaca_request", return_value=(204, {})):
        message, status = order_api.cancel_order("ord-abc", "test-key")

    assert status == 200
    assert message["status"] == "success"


@pytest.mark.unit
def test_get_order_book() -> None:
    with patch(
        "broker.alpaca.api.order_api.alpaca_request",
        return_value=(200, [ORDER_RESPONSE]),
    ):
        rows = order_api.get_order_book("test-key")

    assert len(rows) == 1
    assert rows[0]["id"] == "ord-abc"


@pytest.mark.unit
def test_map_order_data() -> None:
    mapped = map_order_data([ORDER_RESPONSE])
    assert mapped[0]["orderId"] == "ord-abc"
    assert mapped[0]["tradingSymbol"] == "AAPL"
    assert mapped[0]["transactionType"] == "BUY"


@pytest.mark.unit
def test_transform_positions_data() -> None:
    raw = [
        {
            "symbol": "MSFT",
            "exchange": "NASDAQ",
            "qty": "3",
            "side": "long",
            "avg_entry_price": "400",
            "current_price": "410",
            "unrealized_pl": "30",
        }
    ]
    rows = transform_positions_data(raw)
    assert rows[0]["symbol"] == "MSFT"
    assert rows[0]["quantity"] == 3
    assert rows[0]["pnl"] == 30.0


@pytest.mark.unit
def test_get_margin_data(monkeypatch) -> None:
    monkeypatch.setenv("BROKER_API_SECRET", "secret")
    account = {
        "cash": "10000",
        "buying_power": "20000",
        "equity": "15000",
    }
    positions = [{"unrealized_pl": "12.5"}]

    with patch("broker.alpaca.api.funds.alpaca_request", return_value=(200, account)):
        with patch("broker.alpaca.api.funds.get_positions", return_value=positions):
            from broker.alpaca.api.funds import get_margin_data

            margin = get_margin_data("test-key")

    assert margin["availablecash"] == "20000.00"
    assert margin["m2munrealized"] == "12.50"


@pytest.mark.unit
def test_place_order_service_alpaca_skips_sandbox_in_analyze_mode() -> None:
    from pathlib import Path

    source = Path(__file__).resolve().parents[1] / "services" / "place_order_service.py"
    text = source.read_text(encoding="utf-8")
    assert 'get_analyze_mode() and broker != "alpaca"' in text
