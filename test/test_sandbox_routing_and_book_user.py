"""Review R1 openalgo-mcp-ledger: the one own-paper-venue rule, and a loud unattributed strategy-book row."""

from __future__ import annotations

import types

import pytest

from subscribers import strategy_book_subscriber as sub
from utils.sandbox_routing import has_own_paper_venue


@pytest.mark.parametrize(
    ("broker", "expected"),
    [("alpaca", True), (" ALPACA ", True), ("zerodha", False), ("stock_simulator", False), ("", False), (None, False)],
)
def test_has_own_paper_venue(broker, expected) -> None:
    assert has_own_paper_venue(broker) is expected


def test_unresolvable_user_id_is_logged_loudly(monkeypatch) -> None:
    errors: list[str] = []
    monkeypatch.setattr(sub.logger, "error", lambda msg, *a, **k: errors.append(msg % a))
    monkeypatch.setattr(sub, "verify_api_key", lambda _k: None)
    event = types.SimpleNamespace(api_key="bad", request_data={}, strategy="s1", orderid="o9")
    assert sub._user_id(event) == ""
    assert errors and "o9" in errors[0] and "portfolio ledger" in errors[0]


def test_resolved_user_id_is_not_logged(monkeypatch) -> None:
    errors: list[str] = []
    monkeypatch.setattr(sub.logger, "error", lambda msg, *a, **k: errors.append(msg))
    monkeypatch.setattr(sub, "verify_api_key", lambda _k: "u1")
    assert sub._user_id(types.SimpleNamespace(api_key="k", request_data={})) == "u1"
    assert sub._user_id(types.SimpleNamespace(api_key="", request_data={"user_id": "u2"})) == "u2"
    assert errors == []
