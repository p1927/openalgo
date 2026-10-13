"""The expiry override's live branch keys off the service's effective mode, not the clock date.

Regression (review openalgo-fork-brokers): it treated a missing ``clock.replay_date`` as "live",
but the simulator clock always reports the day it is parked on, so in live mode the dropdown was
served replay-bundle expiries and the live-expiry branch was unreachable.
"""

from __future__ import annotations

import pytest

from broker.stock_simulator.api import expiry_overrides as ov


class _Client:
    def __init__(self, status):
        self._status = status

    def status(self):
        return self._status


@pytest.fixture
def patched(monkeypatch):
    def install(status, live_expiries=None):
        monkeypatch.setattr(
            "trade_integrations.stock_simulator.client.StockSimulatorClient", lambda: _Client(status)
        )
        monkeypatch.setattr(
            "trade_integrations.stock_simulator.live_quotes.get_live_quote_service",
            lambda: type("S", (), {"list_expiries": lambda self, u: live_expiries})(),
        )
        monkeypatch.setattr(ov, "_is_master_contract_ready", lambda: True)
        monkeypatch.setattr(
            "trade_integrations.stock_simulator.options.expiry_query.get_expiries_for_sim_now",
            lambda **kw: ["REPLAY-BUNDLE"],
        )
        monkeypatch.setattr(
            "trade_integrations.stock_simulator.config.load_sim_config",
            lambda: type("C", (), {"data_root": "/nowhere"})(),
        )

    return install


CLOCK = {"replay_date": "2026-10-08", "sim_now": "2026-10-08T10:00:00+05:30"}


@pytest.mark.unit
def test_live_mode_serves_live_expiries_even_though_the_clock_has_a_replay_date(patched) -> None:
    patched({"mode": "live", "clock": CLOCK}, live_expiries=["14-OCT-26"])
    assert ov.get_expiries_override("NIFTY", "NFO") == {
        "status": "success",
        "expiries": ["14-OCT-26"],
        "source": "simulator_live",
    }


@pytest.mark.unit
def test_live_mode_with_unreachable_vendor_is_an_explicit_error_not_replay_dates(patched) -> None:
    patched({"mode": "live", "clock": CLOCK}, live_expiries=[])
    out = ov.get_expiries_override("NIFTY", "NFO")
    assert out["status"] == "error" and out["source"] == "simulator_live_unavailable"


@pytest.mark.unit
def test_replay_mode_serves_the_replay_bundle(patched) -> None:
    patched({"mode": "replay", "clock": CLOCK})
    assert ov.get_expiries_override("NIFTY", "NFO")["source"] == "simulator_replay"
