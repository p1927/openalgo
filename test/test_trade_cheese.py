"""POST /api/trade-cheese: input validation and error mapping around Trade's cheese payload.

The session decorator and rate limiter are reached past with ``inspect.unwrap`` (the same trick
test_search_api_expiries_simulator_seam uses); the ranking itself is tested in Trade
(tests/test_cheese_payload.py).
"""

import inspect

import pytest
from flask import Flask


@pytest.fixture
def cheese_client(monkeypatch):
    from blueprints import trade_cheese

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.add_url_rule(
        "/api/trade-cheese",
        endpoint="trade_cheese_test",
        view_func=inspect.unwrap(trade_cheese.post_trade_cheese),
        methods=["POST"],
    )
    calls = {}

    def fake_build(**kwargs):
        calls.update(kwargs)
        return {"status": "ok", "buys": [], "sells": [], "combos": []}

    monkeypatch.setattr(trade_cheese, "_build_cheese_payload", fake_build)
    return app.test_client(), calls, trade_cheese, monkeypatch


GOOD = {
    "underlying": "nifty",
    "chain": [{"strike": 100.0, "ce": {"ltp": 1.0}, "pe": {"ltp": 1.0}}],
    "edges": [{"strike": 100.0, "CE": None, "PE": None}],
    "spot": 100.0,
    "dte_days": 7,
    "expiry": "28OCT26",
}


def test_valid_request_passes_the_normalised_inputs_through(cheese_client):
    client, calls, _, _ = cheese_client
    resp = client.post("/api/trade-cheese", json=GOOD)
    assert resp.status_code == 200
    assert resp.get_json()["cheese"]["status"] == "ok"
    assert calls["underlying"] == "NIFTY" and calls["spot"] == 100.0 and calls["dte_days"] == 7
    assert calls["expiry"] == "28OCT26" and calls["edges"] == GOOD["edges"]


@pytest.mark.parametrize(
    "patch",
    [
        {"underlying": ""},
        {"chain": []},
        {"chain": "x"},
        {"edges": None},
        {"spot": 0},
        {"spot": "abc"},
        {"dte_days": -1},
        {"dte_days": "soon"},
    ],
)
def test_bad_input_is_a_400_and_never_reaches_trade(cheese_client, patch):
    client, calls, _, _ = cheese_client
    resp = client.post("/api/trade-cheese", json={**GOOD, **patch})
    assert resp.status_code == 400
    assert calls == {}


def test_missing_spot_is_a_400(cheese_client):
    client, _, _, _ = cheese_client
    body = {k: v for k, v in GOOD.items() if k != "spot"}
    assert client.post("/api/trade-cheese", json=body).status_code == 400


def test_a_trade_failure_is_reported_not_swallowed(cheese_client):
    client, _, trade_cheese, monkeypatch = cheese_client

    def boom(**_):
        raise RuntimeError("stock_simulator get_index_history unavailable: down")

    monkeypatch.setattr(trade_cheese, "_build_cheese_payload", boom)
    resp = client.post("/api/trade-cheese", json=GOOD)
    assert resp.status_code == 502
    assert "unavailable" in resp.get_json()["message"]
