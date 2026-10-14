"""stock_simulator broker quiet edges: the idle stream says why, a failed live history says so.

Backlog: 2026-10-13-sim-stream-adapter-quiet-edges.
"""

from __future__ import annotations

import logging
import threading

import pandas as pd
import pytest

from broker.stock_simulator.api.data import BrokerData
from broker.stock_simulator.streaming.stock_simulator_adapter import Stock_simulatorWebSocketAdapter


class _Client:
    def __init__(self, mode: str, rows=None, error: Exception | None = None) -> None:
        self._mode, self._rows, self._error = mode, rows, error

    def mode(self):
        return {"mode": self._mode}

    def get_history(self, *_a, **_k):
        if self._error:
            raise self._error
        return {"data": self._rows}


def _data(client: _Client, monkeypatch) -> BrokerData:
    broker = BrokerData.__new__(BrokerData)
    broker._client = client
    monkeypatch.setattr(BrokerData, "_mode", lambda self: self._client.mode())
    monkeypatch.setattr(
        BrokerData,
        "_replay_history",
        lambda self, *a, **k: pd.DataFrame({"close": [1.0]}),
    )
    return broker


@pytest.mark.unit
def test_history_after_a_failed_live_fetch_is_marked_as_replay(monkeypatch, caplog):
    broker = _data(_Client("live", error=RuntimeError("indmoney down")), monkeypatch)
    with caplog.at_level(logging.WARNING):
        frame = broker.get_history("RELIANCE", "NSE", "D", "2026-10-01", "2026-10-14")
    assert frame.attrs["history_source"] == "replay_after_live_failure"
    assert any("falling back to replay" in r.getMessage() for r in caplog.records)


@pytest.mark.unit
def test_plain_replay_history_is_marked_replay(monkeypatch):
    broker = _data(_Client("replay"), monkeypatch)
    assert broker.get_history("RELIANCE", "NSE", "D", "2026-10-01", "2026-10-14").attrs["history_source"] == "replay"


@pytest.mark.unit
def test_live_history_answer_is_not_relabelled(monkeypatch):
    rows = [{"timestamp": 1, "close": 2.0}]
    broker = _data(_Client("live", rows=rows), monkeypatch)
    frame = broker.get_history("RELIANCE", "NSE", "D", "2026-10-01", "2026-10-14")
    assert "history_source" not in frame.attrs and list(frame["close"]) == [2.0]


@pytest.mark.unit
def test_option_history_in_live_mode_is_plain_replay_not_a_failure(monkeypatch):
    broker = _data(_Client("live", error=RuntimeError("must not be called for options")), monkeypatch)
    frame = broker.get_history("NIFTY28OCT2625000CE", "NFO", "D", "2026-10-01", "2026-10-14")
    assert frame.attrs["history_source"] == "replay"


@pytest.mark.unit
def test_unconfigured_stream_logs_once_and_idles(monkeypatch, caplog):
    """No control token: the stream used to sleep forever with no line saying why."""
    from trade_integrations.stock_simulator import client as sim_client

    monkeypatch.setattr(sim_client.StockSimulatorClient, "is_configured", property(lambda self: False))
    adapter = Stock_simulatorWebSocketAdapter.__new__(Stock_simulatorWebSocketAdapter)
    adapter.running, adapter._generation, adapter.lock = True, 1, threading.Lock()
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) >= 3:
            adapter.running = False

    monkeypatch.setattr("broker.stock_simulator.streaming.stock_simulator_adapter.time.sleep", fake_sleep)
    with caplog.at_level(logging.WARNING, logger="stock_simulator_websocket"):
        adapter._stream_loop(1)
    warnings = [r for r in caplog.records if "no control token" in r.getMessage()]
    assert len(warnings) == 1 and len(sleeps) == 3
