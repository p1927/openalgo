"""Endpoint tests for the simulator-aware expiry seam in
``openalgo/blueprints/search.py::api_expiries``.

These tests stand up the search blueprint in a minimal Flask app,
mock the session layer so the request is treated as authenticated, and
assert that:

* When ``session['broker'] == 'stock_simulator'`` AND the override
  returns a dict, the endpoint returns the override's payload — and
  the ``source: 'simulator_replay'`` marker is preserved.
* When ``session['broker'] == 'stock_simulator'`` AND the override
  returns ``None``, the endpoint falls through to the default path.
* When ``session['broker']`` is any other broker (or missing), the
  override is never consulted, and the default path runs unchanged.

This guards the two regression risks the modular seam introduces:
1. A non-simulator session accidentally hitting the override path
   (would slow the endpoint, never break it, but is "wrong behaviour").
2. A simulator session falling through to a stale symtoken list
   (the bug the seam exists to fix).

The override is monkey-patched so the test does not need a real
replay service or parquet files on disk.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask, session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def search_app():
    """A minimal Flask app with only the search blueprint registered.

    ``@check_session_validity`` wraps ``api_expiries`` at definition
    time. ``functools.wraps`` preserves the original function as
    ``__wrapped__``, so we can reach the unwrapped view and re-register
    it on a clean test app. This keeps the test focused on the simulator
    seam logic and avoids dragging in the full session machinery.
    """
    from openalgo.blueprints import search as search_bp_module

    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True

    # Reach past the ``@check_session_validity`` decorator.
    view = search_bp_module.api_expiries.__wrapped__
    app.add_url_rule(
        "/search/api/expiries",
        endpoint="api_expiries_test",
        view_func=view,
        methods=["GET", "POST"],
    )
    return app


@pytest.fixture
def override_dict():
    """A canned override response for the happy path."""
    return {
        "status": "success",
        "expiries": ["02-JUL-26", "09-JUL-26", "30-JUL-26"],
        "source": "simulator_replay",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_simulator_session_uses_override(search_app, override_dict) -> None:
    """broker=stock_simulator + override returns dict -> endpoint returns dict."""
    client = search_app.test_client()

    with client.session_transaction() as sess:
        sess["broker"] = "stock_simulator"

    # Patch the override at the import location used by ``search.py``.
    # The blueprint does a lazy ``from broker.stock_simulator.api.expiry_overrides
    # import get_expiries_override`` inside the function body, so the
    # name lives in the *override module's* globals at call time.
    with patch(
        "broker.stock_simulator.api.expiry_overrides.get_expiries_override",
        return_value=override_dict,
    ) as mock_override:
        res = client.get(
            "/search/api/expiries?exchange=NFO&underlying=NIFTY&instrumenttype=options"
        )

    assert res.status_code == 200
    body = json.loads(res.data)
    assert body == override_dict
    assert body["source"] == "simulator_replay"
    assert mock_override.called, "override must be consulted on simulator session"
    # It was called with the underlying and exchange from the query.
    args, kwargs = mock_override.call_args
    assert "NIFTY" in args or kwargs.get("underlying") == "NIFTY"


def test_simulator_session_falls_through_when_override_returns_none(search_app) -> None:
    """broker=stock_simulator + override returns None -> default path runs."""
    client = search_app.test_client()

    with client.session_transaction() as sess:
        sess["broker"] = "stock_simulator"

    with patch(
        "broker.stock_simulator.api.expiry_overrides.get_expiries_override",
        return_value=None,
    ) as mock_override:
        # The default path hits the symtoken table. We patch
        # ``get_distinct_expiries`` in the *search blueprint's*
        # imported-name slot, which is the cached wrapper.
        with patch(
            "openalgo.blueprints.search.get_distinct_expiries",
            return_value=["28-AUG-25"],
        ) as mock_default:
            res = client.get(
                "/search/api/expiries?exchange=NFO&underlying=NIFTY&instrumenttype=options"
            )

    assert res.status_code == 200
    body = json.loads(res.data)
    # Default path: no ``source`` field, no ``simulator_replay`` marker.
    assert body == {"status": "success", "expiries": ["28-AUG-25"]}
    assert "source" not in body
    assert mock_override.called
    assert mock_default.called, "default path must run when override returns None"


def test_non_simulator_session_skips_override(search_app) -> None:
    """broker=zerodha (or any other broker) -> override is NEVER consulted."""
    client = search_app.test_client()

    with client.session_transaction() as sess:
        sess["broker"] = "zerodha"

    with patch(
        "broker.stock_simulator.api.expiry_overrides.get_expiries_override",
        return_value=SimpleNamespace(),  # would explode if used
    ) as mock_override:
        with patch(
            "openalgo.blueprints.search.get_distinct_expiries",
            return_value=["28-AUG-25"],
        ) as mock_default:
            res = client.get(
                "/search/api/expiries?exchange=NFO&underlying=NIFTY&instrumenttype=options"
            )

    assert res.status_code == 200
    body = json.loads(res.data)
    assert body == {"status": "success", "expiries": ["28-AUG-25"]}
    assert not mock_override.called, "non-simulator broker must not call override"
    assert mock_default.called


def test_missing_broker_session_treated_as_non_simulator(search_app) -> None:
    """No session['broker'] at all -> default path, no override consult."""
    client = search_app.test_client()
    # No session_transaction: session is empty.

    with patch(
        "broker.stock_simulator.api.expiry_overrides.get_expiries_override",
    ) as mock_override:
        with patch(
            "openalgo.blueprints.search.get_distinct_expiries",
            return_value=[],
        ):
            res = client.get("/search/api/expiries?exchange=NFO&underlying=NIFTY")

    assert res.status_code == 200
    body = json.loads(res.data)
    assert body == {"status": "success", "expiries": []}
    assert not mock_override.called


def test_simulator_session_with_broken_override_import_falls_through(
    search_app,
) -> None:
    """A missing override module (e.g. simulator package gone) must not 500.

    This is the "easy to remove" property: deleting the override file
    should leave the endpoint functional via the default path.
    """
    client = search_app.test_client()
    with client.session_transaction() as sess:
        sess["broker"] = "stock_simulator"

    # Patch the lazy import target to raise ImportError as if the
    # simulator package had been removed.
    with patch(
        "broker.stock_simulator.api.expiry_overrides.get_expiries_override",
        side_effect=ImportError("simulator package gone"),
    ):
        with patch(
            "openalgo.blueprints.search.get_distinct_expiries",
            return_value=["28-AUG-25"],
        ):
            res = client.get(
                "/search/api/expiries?exchange=NFO&underlying=NIFTY&instrumenttype=options"
            )

    # The ``try/except`` in the seam catches the ImportError and falls
    # through to the default path. The endpoint must succeed.
    assert res.status_code == 200
    body = json.loads(res.data)
    assert body == {"status": "success", "expiries": ["28-AUG-25"]}


def test_mc_status_check_handles_db_error(monkeypatch) -> None:
    """The MC-status check itself is defensive: a DB failure must
    not crash the override, and must return ``False`` (opt out).

    Defensive behaviour: any exception in the status read is caught
    and treated as "not ready". The chain call then falls through
    to the symtoken path, which is the safer degraded state.

    Note: the override imports via the top-level ``database.``
    package (not ``openalgo.database.``); the conftest adds
    ``openalgo/`` to sys.path so the same file is reachable both
    ways, but as two distinct module objects. We patch the path
    the override actually uses.
    """
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status",
        lambda broker: (_ for _ in ()).throw(RuntimeError("DB down")),
    )
    import openalgo.broker.stock_simulator.api.expiry_overrides as override_mod
    assert override_mod._is_master_contract_ready() is False


def test_mc_status_check_handles_missing_status_row(monkeypatch) -> None:
    """``get_status`` returning ``None`` (no row yet) is treated as
    not-ready, so a first arm before the MC has ever run does not
    surface bundle-driven expiries the symtoken table can't serve.
    """
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status", lambda broker: None
    )
    import openalgo.broker.stock_simulator.api.expiry_overrides as override_mod
    assert override_mod._is_master_contract_ready() is False


def test_mc_status_check_uses_is_ready_field(monkeypatch) -> None:
    """The real ``_is_master_contract_ready`` returns True only when
    the status row's ``is_ready`` is truthy.

    Exercises the real function (not a monkey-patched stub) with
    representative status dict shapes — including the
    ``is_ready``-missing case which is treated as not-ready.
    """
    import openalgo.broker.stock_simulator.api.expiry_overrides as override_mod

    # is_ready explicitly True -> ready
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status",
        lambda broker: {"status": "success", "is_ready": True},
    )
    assert override_mod._is_master_contract_ready() is True

    # is_ready explicitly False -> not ready
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status",
        lambda broker: {"status": "downloading", "is_ready": False},
    )
    assert override_mod._is_master_contract_ready() is False

    # is_ready key missing entirely -> not ready
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status",
        lambda broker: {"status": "success"},
    )
    assert override_mod._is_master_contract_ready() is False

    # is_ready is something truthy (e.g. 1) -> ready
    monkeypatch.setattr(
        "database.master_contract_status_db.get_status",
        lambda broker: {"is_ready": 1},
    )
    assert override_mod._is_master_contract_ready() is True
