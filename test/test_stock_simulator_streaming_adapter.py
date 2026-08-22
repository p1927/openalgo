"""Tests for the stock_simulator WS-stream adapter's tick-handling logic.

Covers the pure snapshot/publish/dedup logic added when the adapter switched
from REST-polling `/data/quote` per symbol per tick to consuming the shared
service's own `/stream` WS route — see
.claude/backlog/items/2026-08-21-stock-simulator-ws-adapter-subscribe-stream.md.

`Stock_simulatorWebSocketAdapter.__init__` creates a real ZeroMQ context/socket
via `BaseBrokerWebSocketAdapter.__init__` (see websocket_proxy/base_adapter.py),
which is unnecessary weight for exercising the tick-handling logic in
isolation. These tests build the adapter via `__new__` and set only the
attributes that logic touches, then stub `publish_market_data` to capture
calls instead of going through ZMQ.
"""

from __future__ import annotations

import threading

import pytest

from broker.stock_simulator.streaming.stock_simulator_adapter import (
    Stock_simulatorWebSocketAdapter,
)


def _make_adapter() -> Stock_simulatorWebSocketAdapter:
    adapter = Stock_simulatorWebSocketAdapter.__new__(Stock_simulatorWebSocketAdapter)
    adapter.broker_name = "stock_simulator"
    adapter.user_id = "test-user"
    adapter.running = False
    adapter._stream_thread = None
    adapter.lock = threading.Lock()
    adapter.subscriptions = {}
    adapter._last_ltp = {}
    adapter._last_sim_ts = {}
    adapter.published: list[tuple[str, dict]] = []
    adapter.publish_market_data = lambda topic, data: adapter.published.append((topic, data))
    return adapter


def _quote(**overrides) -> dict:
    base = {
        "ltp": 100.0,
        "sim_ts": "2026-08-22T09:30:00+05:30",
        "bid": 99.9,
        "ask": 100.1,
        "open": 99.0,
        "high": 101.0,
        "low": 98.5,
        "volume": 1000,
        "oi": 0,
    }
    base.update(overrides)
    return base


@pytest.mark.unit
def test_desired_subscriptions_reflects_symbol_exchange_pairs():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)
    adapter.subscribe("BANKNIFTY", "NSE_INDEX", mode=1)

    assert adapter._desired_subscriptions() == {("NIFTY", "NSE_INDEX"), ("BANKNIFTY", "NSE_INDEX")}

    adapter.unsubscribe("NIFTY", "NSE_INDEX")
    assert adapter._desired_subscriptions() == {("BANKNIFTY", "NSE_INDEX")}


@pytest.mark.unit
def test_mode_for_defaults_to_quote_when_unsubscribed():
    adapter = _make_adapter()
    assert adapter._mode_for("NIFTY", "NSE_INDEX") == 2

    adapter.subscribe("NIFTY", "NSE_INDEX", mode=3)
    assert adapter._mode_for("NIFTY", "NSE_INDEX") == 3


@pytest.mark.unit
def test_handle_snapshot_publishes_quote_mode_fields():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)

    payload = {
        "mode": {"mode": "replay"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}],
    }
    adapter._handle_snapshot(payload)

    assert len(adapter.published) == 1
    topic, data = adapter.published[0]
    assert topic == "NSE_INDEX_NIFTY_QUOTE"
    assert data["ltp"] == 100.0
    assert data["simulated"] is True
    assert data["sim_source"] == "replay"
    assert data["bid"] == 99.9
    assert data["ask"] == 100.1
    assert data["volume"] == 1000
    assert "depth" not in data


@pytest.mark.unit
def test_handle_snapshot_live_mode_marks_not_simulated():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=1)

    payload = {
        "mode": {"mode": "live"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}],
    }
    adapter._handle_snapshot(payload)

    _, data = adapter.published[0]
    assert data["simulated"] is False
    assert data["sim_source"] == "live"


@pytest.mark.unit
def test_handle_snapshot_depth_mode_adds_depth():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=3)

    payload = {
        "mode": {"mode": "replay"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}],
    }
    adapter._handle_snapshot(payload)

    _, data = adapter.published[0]
    assert data["depth"]["buy"][0]["price"] == 99.9
    assert data["depth"]["sell"][0]["price"] == 100.1


@pytest.mark.unit
def test_handle_snapshot_dedupes_unchanged_ltp_same_sim_ts():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)

    payload = {
        "mode": {"mode": "replay"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}],
    }
    adapter._handle_snapshot(payload)
    adapter._handle_snapshot(payload)  # identical tick again

    assert len(adapter.published) == 1


@pytest.mark.unit
def test_handle_snapshot_publishes_when_sim_ts_advances():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)

    adapter._handle_snapshot(
        {"mode": {"mode": "replay"}, "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}]}
    )
    adapter._handle_snapshot(
        {
            "mode": {"mode": "replay"},
            "quotes": [
                {
                    "symbol": "NIFTY",
                    "exchange": "NSE_INDEX",
                    "data": _quote(sim_ts="2026-08-22T09:31:00+05:30"),
                }
            ],
        }
    )

    assert len(adapter.published) == 2


@pytest.mark.unit
def test_handle_snapshot_skips_error_entries():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)

    payload = {
        "mode": {"mode": "replay"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "error": "no replay bar"}],
    }
    adapter._handle_snapshot(payload)

    assert adapter.published == []


@pytest.mark.unit
def test_handle_snapshot_skips_non_positive_ltp():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)

    payload = {
        "mode": {"mode": "replay"},
        "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote(ltp=0)}],
    }
    adapter._handle_snapshot(payload)

    assert adapter.published == []


@pytest.mark.unit
def test_unsubscribe_clears_dedup_state():
    adapter = _make_adapter()
    adapter.subscribe("NIFTY", "NSE_INDEX", mode=2)
    adapter._handle_snapshot(
        {"mode": {"mode": "replay"}, "quotes": [{"symbol": "NIFTY", "exchange": "NSE_INDEX", "data": _quote()}]}
    )
    assert adapter._last_ltp

    adapter.unsubscribe("NIFTY", "NSE_INDEX")
    assert adapter._last_ltp == {}
    assert adapter._last_sim_ts == {}
