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
    out = _fund(monkeypatch, {"cash": "1000", "buying_power": "4000", "equity": "1500", "initial_margin": "500"}, [])
    assert out["availablecash"] == "1000.00"
    assert out["buyingpower"] == "4000.00"
    assert out["utiliseddebits"] == "500.00"


def test_m2mrealized_is_omitted(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1", "buying_power": "4", "equity": "1", "initial_margin": "0"}, [{"unrealized_pl": "2.5"}])
    assert "m2mrealized" not in out
    assert out["m2munrealized"] == "2.50"


def test_consumer_default_still_zero(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1", "buying_power": "4", "equity": "1", "initial_margin": "0"}, [])
    assert float(out.get("m2mrealized", 0)) == 0.0


@pytest.mark.parametrize("field", ["cash", "buying_power", "initial_margin"])
@pytest.mark.parametrize("value", [None, "", "NaN", "Infinity", "-Infinity", True, "nope"])
def test_invalid_account_values_raise(monkeypatch, field, value):
    account = {"cash": "1", "buying_power": "4", "initial_margin": "0"}
    account[field] = value
    with pytest.raises(RuntimeError, match=field):
        _fund(monkeypatch, account, [])
    del account[field]
    with pytest.raises(RuntimeError, match=field):
        _fund(monkeypatch, account, [])


@pytest.mark.parametrize("position", [{}, {"unrealized_pl": None}, {"unrealized_pl": "NaN"},
                                      {"unrealized_pl": "Infinity"}, {"unrealized_pl": True}, None])
def test_invalid_position_pnl_is_not_silently_dropped(monkeypatch, position):
    with pytest.raises(RuntimeError, match="Alpaca"):
        _fund(monkeypatch, {"cash": "0", "buying_power": "0", "initial_margin": "0"}, [position])


def test_short_book_uses_reported_margin_not_equity_minus_cash(monkeypatch):
    out = _fund(monkeypatch, {"cash": "1500", "equity": "1000", "buying_power": "1800",
                             "initial_margin": "750"}, [{"unrealized_pl": "-2.25"}])
    assert out["utiliseddebits"] == "750.00"
    assert out["m2munrealized"] == "-2.25"


def test_zero_and_negative_cash_are_real_values(monkeypatch):
    out = _fund(monkeypatch, {"cash": "-1", "buying_power": "0", "initial_margin": "0"}, [])
    assert out["availablecash"] == "-1.00"
    assert out["buyingpower"] == out["utiliseddebits"] == "0.00"


def test_negative_margin_fails(monkeypatch):
    with pytest.raises(RuntimeError, match="negative"):
        _fund(monkeypatch, {"cash": "0", "buying_power": "0", "initial_margin": "-1"}, [])


def test_real_http_account_and_positions_boundary(monkeypatch):
    """Real REST transport against an owned loopback fixture, with no broker credentials."""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    account = {"cash": "1500", "equity": "1000", "buying_power": "1800", "initial_margin": "750"}
    state = {"positions": [{"unrealized_pl": "-2.25"}]}
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append((self.path, self.headers["APCA-API-KEY-ID"]))
            body = json.dumps(account if self.path == "/v2/account" else state["positions"]).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("ALPACA_API_BASE", f"http://127.0.0.1:{server.server_port}")
    monkeypatch.setenv("BROKER_API_SECRET", "fixture-only")
    try:
        out = funds.get_margin_data("fixture-only")
        assert out["utiliseddebits"] == "750.00"
        assert out["m2munrealized"] == "-2.25"
        assert seen == [("/v2/account", "fixture-only"), ("/v2/positions", "fixture-only")]
        state["positions"] = {"message": "bad successful envelope"}
        with pytest.raises(RuntimeError, match="positions fetch failed"):
            funds.get_margin_data("fixture-only")
        account["cash"] = "NaN"
        with pytest.raises(RuntimeError, match="cash is nonfinite"):
            funds.get_margin_data("fixture-only")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)
