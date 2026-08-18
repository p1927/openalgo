"""Locks in the invariant documented in openalgo/CLAUDE.md: the
``stock_simulator`` broker plugin must never place a real order. Selecting
it in place of a real broker is what makes Nautilus/MCP/Vibe safe to trade
against replayed/recorded data on the same instance that may also hold a
real broker session — the only legitimate order path is OpenAlgo's
Analyzer/sandbox paper-fill engine, never this plugin's own API layer.
"""

from __future__ import annotations

import pytest

from broker.stock_simulator.api import order_api


@pytest.mark.unit
def test_place_order_api_always_refuses() -> None:
    response, payload, order_id = order_api.place_order_api(
        {"symbol": "NIFTY", "action": "BUY", "quantity": 1}, "dummy-auth-token"
    )

    assert response.status == 501
    assert payload["status"] == "error"
    assert order_id is None


@pytest.mark.unit
def test_stock_simulator_has_no_credentialed_auth_path() -> None:
    from broker.stock_simulator.api import auth_api

    token, error = auth_api.authenticate_broker("stock_simulator")

    assert token == "stock_simulator_session_token"
    assert error is None
