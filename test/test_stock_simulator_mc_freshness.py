"""stock_simulator master-contract freshness must compare against what the downloader stores.

Regression (review openalgo-fork-brokers): the check read the replay date from ``NSE_REPLAY_DATE``
with a hardcoded ``2021-03-25`` default, but the tier .env leaves that key unset under
week-rotation -- so every check reported "replay date changed" and the contract was rebuilt on
every boot/login. The downloader stores ``mc_cache_fingerprint(replay_date=load_sim_config()...)``;
the check now recomputes exactly that.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from broker.stock_simulator.api import mc_freshness


def _stats(replay_date="2026-10-08", **over):
    stats = {
        "replay_date": replay_date,
        "underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"],
        "max_expiries": 12,
        "equities": ["INFY", "TCS"],
        "stock_options": ["INFY"],
    }
    stats.update(over)
    return stats


@pytest.fixture
def wired(monkeypatch):
    """Cached stats == what the fingerprint would produce, with NSE_REPLAY_DATE unset."""
    monkeypatch.delenv("NSE_REPLAY_DATE", raising=False)
    state = {"stats": _stats(), "status": "success"}
    monkeypatch.setattr(
        mc_freshness,
        "get_status",
        lambda _b: {"status": state["status"], "exchange_stats": state["stats"]},
    )
    monkeypatch.setattr("database.token_db.get_symbol_count", lambda: 10)
    monkeypatch.setattr(
        "trade_integrations.stock_simulator.config.load_sim_config",
        lambda: SimpleNamespace(replay_date="2026-10-08", data_root="/nowhere"),
    )
    monkeypatch.setattr(
        "trade_integrations.stock_simulator.master_contract.mc_cache_fingerprint",
        lambda **kw: _stats(replay_date=kw["replay_date"][:10]),
    )
    return state


@pytest.mark.unit
def test_fresh_cache_is_not_redownloaded_when_replay_date_env_is_unset(wired) -> None:
    assert mc_freshness.should_download_stock_simulator_contract() == (
        False,
        "Simulator master contract matches replay fingerprint",
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "over, reason",
    [
        ({"replay_date": "2026-10-01"}, "replay date changed to 2026-10-08"),
        ({"underlyings": ["NIFTY"]}, "underlying universe changed"),
        ({"max_expiries": 6}, "max expiries changed"),
        ({"equities": ["INFY"]}, "recorded equities changed"),
        ({"stock_options": []}, "stock-option symbols changed"),
        ({"equities": None}, "recorded equities not cached"),
    ],
)
def test_any_drifted_fingerprint_field_forces_a_download(wired, over, reason) -> None:
    wired["stats"] = _stats(**over)
    should, why = mc_freshness.should_download_stock_simulator_contract()
    assert should is True and reason in why


@pytest.mark.unit
def test_failed_last_download_and_empty_table_force_a_download(wired, monkeypatch) -> None:
    wired["status"] = "error"
    assert mc_freshness.should_download_stock_simulator_contract()[0] is True
    wired["status"] = "success"
    monkeypatch.setattr("database.token_db.get_symbol_count", lambda: 0)
    assert mc_freshness.should_download_stock_simulator_contract() == (
        True,
        "Simulator symtoken table is empty",
    )


@pytest.mark.unit
def test_list_order_in_the_cached_fingerprint_does_not_force_a_download(wired) -> None:
    wired["stats"] = _stats(underlyings=["SENSEX", "NIFTY", "BANKNIFTY"], equities=["TCS", "INFY"])
    assert mc_freshness.should_download_stock_simulator_contract()[0] is False


@pytest.mark.unit
def test_unreadable_symbol_count_is_not_swallowed(wired, monkeypatch) -> None:
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("database.token_db.get_symbol_count", boom)
    with pytest.raises(RuntimeError):
        mc_freshness.should_download_stock_simulator_contract()


@pytest.mark.unit
@pytest.mark.parametrize(
    "url, want",
    [
        ("http://127.0.0.1:5001/stock_simulator/callback", "stock_simulator"),
        ("  http://127.0.0.1:5001/Alpaca/callback  ", "alpaca"),
        ("", ""),
        ("http://127.0.0.1:5001/callback/extra", ""),
    ],
)
def test_one_redirect_url_parser_for_configured_and_default_broker(monkeypatch, url, want) -> None:
    from utils import broker_env_sync, broker_registry

    monkeypatch.setenv("REDIRECT_URL", url)
    assert broker_env_sync.get_configured_broker() == broker_registry.get_default_broker() == want


@pytest.mark.unit
def test_config_failure_is_loud_not_swallowed(wired, monkeypatch) -> None:
    def boom():
        raise ValueError("NSE_REPLAY_WEEK_COUNT must be an integer")

    monkeypatch.setattr("trade_integrations.stock_simulator.config.load_sim_config", boom)
    with pytest.raises(ValueError):
        mc_freshness.should_download_stock_simulator_contract()
