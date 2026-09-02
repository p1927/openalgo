"""Regression test for the stock_simulator option-chain fast-path partial-coverage bug.

Backlog: .claude/backlog/archive/items/2026-08-31-option-chain-simulator-fastpath-fallback.md

Before the fix, `get_option_chain`'s stock_simulator fast-path
(`services/option_chain_service.py`) wiped *every* leg it had already mapped
from `BrokerData.get_option_chain(...)` as soon as coverage was partial
(`sim_mapped < sim_needed`), then re-fetched the *entire* symbol set via
`get_multiquotes`. That threw away correct data for legs the simulator did
have, and relied on `get_multiquotes` (per-leg `OptionsReplayStore.quote_at`)
independently disagreeing with `get_option_chain` (bundled
`OptionsReplayStore.chain_at`) about which legs have replay data to partially
self-heal.

This test drives `option_chain_service.get_option_chain` directly with every
DB/broker touchpoint monkeypatched (no live stack, no DB), simulating a
stock_simulator broker whose fast-path chain call only covers 3 of 5 strikes,
and asserts:
  - the 3 covered strikes' CE/PE quotes come straight from the fast-path
    (not clobbered/re-fetched),
  - `get_multiquotes` is asked for only the 4 still-missing CE/PE symbols
    (2 strikes x CE+PE), not the full 10-symbol set,
  - the response carries a non-empty "warnings" entry describing the partial
    coverage.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services import option_chain_service as svc

BASE_SYMBOL = "NIFTY"
QUOTE_EXCHANGE = "NSE_INDEX"
OPTIONS_EXCHANGE = "NFO"
FINAL_EXPIRY = "25SEP26"
STRIKE_COUNT = 2

# 5 strikes selected around ATM (24900).
ALL_STRIKES = [24700.0, 24800.0, 24900.0, 25000.0, 25100.0]
# Only the first 3 strikes are covered by the simulator's fast-path chain call;
# 25000/25100 are a genuine gap in the replay bundle for this repro.
COVERED_STRIKES = {24700.0, 24800.0, 24900.0}


def _symbol(strike: float, opt_type: str) -> str:
    return f"{BASE_SYMBOL}{FINAL_EXPIRY}{int(strike)}{opt_type}"


class _FakeSimulatorBrokerData:
    """Stands in for broker.stock_simulator.api.data.BrokerData."""

    def __init__(self, auth) -> None:
        self.auth = auth

    def get_option_chain(self, base_symbol, exchange, *, expiry_date, strike_count):
        assert base_symbol == BASE_SYMBOL
        chain = []
        for strike in ALL_STRIKES:
            if strike not in COVERED_STRIKES:
                continue
            chain.append(
                {
                    "strike": strike,
                    "ce_ltp": strike * 0.01,
                    "ce_oi": 100,
                    "pe_ltp": strike * 0.009,
                    "pe_oi": 90,
                }
            )
        return {"chain": chain}


class _FakeSimulatorBrokerModule:
    BrokerData = _FakeSimulatorBrokerData


@pytest.fixture
def patched_option_chain(monkeypatch):
    """Monkeypatch every DB/broker touchpoint `get_option_chain` uses, leaving
    only the fast-path-vs-fallback logic under test running for real."""

    def _fake_get_auth_token_broker(api_key, include_feed_token=False):
        return ("fake-auth-token", "fake-feed-token", "stock_simulator")

    def _fake_get_quotes(*, symbol, exchange, api_key):
        return True, {"data": {"ltp": 24900.0, "prev_close": 24850.0}}, 200

    def _fake_get_available_strikes(base_symbol, expiry, option_type, options_exchange):
        return list(ALL_STRIKES)

    def _fake_get_option_symbols_for_chain(base_symbol, expiry_date, strikes_with_labels, exchange):
        out = []
        for info in strikes_with_labels:
            strike = info["strike"]
            out.append(
                {
                    "strike": strike,
                    "ce": {
                        "symbol": _symbol(strike, "CE"),
                        "label": info["ce_label"],
                        "exists": True,
                        "lotsize": 75,
                        "tick_size": 0.05,
                    },
                    "pe": {
                        "symbol": _symbol(strike, "PE"),
                        "label": info["pe_label"],
                        "exists": True,
                        "lotsize": 75,
                        "tick_size": 0.05,
                    },
                }
            )
        return out

    def _fake_import_broker_module(broker):
        assert broker == "stock_simulator"
        return _FakeSimulatorBrokerModule

    multiquotes_calls: list[list[dict[str, str]]] = []

    def _fake_get_multiquotes(*, symbols, api_key):
        multiquotes_calls.append(list(symbols))
        results = [
            {
                "symbol": s["symbol"],
                "exchange": s["exchange"],
                "data": {
                    "ltp": 999.0,
                    "oi": 42,
                    "open": 999.0,
                    "high": 999.0,
                    "low": 999.0,
                    "prev_close": 999.0,
                    "volume": 0,
                    "bid": 998.9,
                    "ask": 999.1,
                    "bid_qty": 100,
                    "ask_qty": 100,
                },
            }
            for s in symbols
        ]
        return True, {"results": results}, 200

    def _fake_get_expiry_datetime(expiry, exchange):
        return datetime(2026, 9, 25, 15, 30, tzinfo=UTC)

    monkeypatch.setattr(svc, "get_auth_token_broker", _fake_get_auth_token_broker)
    monkeypatch.setattr(svc, "get_quotes", _fake_get_quotes)
    monkeypatch.setattr(svc, "get_available_strikes", _fake_get_available_strikes)
    monkeypatch.setattr(svc, "get_option_symbols_for_chain", _fake_get_option_symbols_for_chain)
    monkeypatch.setattr(svc, "import_broker_module", _fake_import_broker_module)
    monkeypatch.setattr(svc, "get_multiquotes", _fake_get_multiquotes)
    monkeypatch.setattr(svc, "get_expiry_datetime", _fake_get_expiry_datetime)

    return multiquotes_calls


def test_partial_fastpath_preserves_covered_legs_and_fills_only_gaps(patched_option_chain):
    multiquotes_calls = patched_option_chain

    success, response, status_code = svc.get_option_chain(
        underlying=BASE_SYMBOL,
        exchange=QUOTE_EXCHANGE,
        expiry_date=FINAL_EXPIRY,
        strike_count=STRIKE_COUNT,
        api_key="fake-api-key",
        with_quotes=True,
        with_greeks=False,
    )

    assert success is True
    assert status_code == 200

    chain_by_strike = {row["strike"]: row for row in response["chain"]}
    assert set(chain_by_strike) == set(ALL_STRIKES)

    # Covered strikes: fast-path values preserved verbatim, not overwritten by
    # the multiquotes fallback's stand-in 999.0 sentinel.
    for strike in COVERED_STRIKES:
        row = chain_by_strike[strike]
        assert row["ce"]["ltp"] == strike * 0.01
        assert row["pe"]["ltp"] == strike * 0.009
        assert row["ce"]["oi"] == 100
        assert row["pe"]["oi"] == 90

    # Gapped strikes: filled in via the fallback's per-symbol data.
    for strike in set(ALL_STRIKES) - COVERED_STRIKES:
        row = chain_by_strike[strike]
        assert row["ce"]["ltp"] == 999.0
        assert row["pe"]["ltp"] == 999.0

    # The fallback was asked for exactly the 4 missing legs (2 strikes x CE+PE),
    # never the full 10-symbol set -- this is the core regression check: a
    # partial fast-path must not wipe-and-refetch everything.
    assert len(multiquotes_calls) == 1
    requested_symbols = {s["symbol"] for s in multiquotes_calls[0]}
    expected_missing = {
        _symbol(strike, opt_type)
        for strike in (set(ALL_STRIKES) - COVERED_STRIKES)
        for opt_type in ("CE", "PE")
    }
    assert requested_symbols == expected_missing

    # Partial coverage must be surfaced, not just silently patched over.
    # sim_mapped counts legs, not strikes: 3 covered strikes x CE+PE = 6 of 10 legs.
    assert response["warnings"], "expected a partial-fastpath warning in the response"
    assert "6 of 10" in response["warnings"][0]


def test_full_fastpath_coverage_never_calls_multiquotes(monkeypatch, patched_option_chain):
    """Sanity check: when the simulator covers every leg, no fallback call happens
    at all (the pre-existing used_fast_path=True path), so this test's mocking
    doesn't accidentally make the fallback look like it's always used."""
    multiquotes_calls = patched_option_chain

    def _full_get_option_chain(self, base_symbol, exchange, *, expiry_date, strike_count):
        return {
            "chain": [
                {"strike": s, "ce_ltp": s * 0.01, "ce_oi": 1, "pe_ltp": s * 0.009, "pe_oi": 1}
                for s in ALL_STRIKES
            ]
        }

    monkeypatch.setattr(_FakeSimulatorBrokerData, "get_option_chain", _full_get_option_chain)

    success, response, status_code = svc.get_option_chain(
        underlying=BASE_SYMBOL,
        exchange=QUOTE_EXCHANGE,
        expiry_date=FINAL_EXPIRY,
        strike_count=STRIKE_COUNT,
        api_key="fake-api-key",
        with_quotes=True,
        with_greeks=False,
    )

    assert success is True
    assert status_code == 200
    assert multiquotes_calls == []
    assert response["warnings"] == []
