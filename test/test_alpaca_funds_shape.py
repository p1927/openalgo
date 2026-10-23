"""Alpaca funds: availablecash is cash (not margin buying power); m2mrealized is omitted, not faked as 0."""

import pytest

from broker.alpaca.api import funds
from database import settings_db


@pytest.fixture(autouse=True)
def _settings_db():
    settings_db.init_db()  # order_api (imported by funds) reads the settings table


def _fund(monkeypatch, account, positions):
    monkeypatch.setattr(funds, "alpaca_request", lambda *_a, **_k: (200, account))
    monkeypatch.setattr(funds, "get_positions", lambda _t: positions)
    return funds.get_margin_data("tok")


def test_available_cash_is_cash_and_buying_power_is_separate(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1000", "buying_power": "4000", "equity": "1500"}, [])
    assert out["availablecash"] == "1000.00"
    assert out["buyingpower"] == "4000.00"
    assert out["utiliseddebits"] == "500.00"


def test_m2mrealized_is_omitted(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1", "buying_power": "4", "equity": "1"}, [{"unrealized_pl": "2.5"}])
    assert "m2mrealized" not in out
    assert out["m2munrealized"] == "2.50"


def test_consumer_default_still_zero(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1", "buying_power": "4", "equity": "1"}, [])
    assert float(out.get("m2mrealized", 0)) == 0.0
