"""Regression test: switching broker while already logged in.

`broker_callback`'s `session.get("logged_in")` shortcut predates the
`stock_simulator` broker and was written for OpenAlgo's original
single-broker-per-session model, where re-hitting a broker's own callback
URL while already logged in just meant "duplicate OAuth redirect for the
SAME broker" -- safe to skip straight to the dashboard.

`stock_simulator` breaks that assumption: it lets a user switch between a
real broker and the simulator (or back) within one running session. Doing
so while already logged in used to hit the same shortcut for a *different*
target broker, which flipped `session["broker"]` without ever calling that
broker's auth function or storing a token for it -- leaving the dashboard
looking up a token that was never written, surfaced to the user as
"Broker session expired" for a broker they'd just selected.
"""

from __future__ import annotations

import os
import sys

import pytest
from flask import Flask

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import blueprints.brlogin as brlogin_module  # noqa: E402
from blueprints.brlogin import brlogin_bp  # noqa: E402
from blueprints.dashboard import dashboard_bp  # noqa: E402


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.register_blueprint(brlogin_bp)
    application.register_blueprint(dashboard_bp)
    application.broker_auth_functions = {
        "stock_simulator_auth": lambda code: ("stock_simulator_session_token", None),
    }
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.mark.unit
def test_switching_broker_while_logged_in_calls_handle_auth_success(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        brlogin_module,
        "handle_auth_success",
        lambda auth_token, user, broker, **kw: calls.append((auth_token, user, broker)) or "ok",
    )

    with client.session_transaction() as sess:
        sess["user"] = "prat"
        sess["logged_in"] = True
        sess["broker"] = "indmoney"

    client.get("/stock_simulator/callback")

    assert calls == [("stock_simulator_session_token", "prat", "stock_simulator")]


@pytest.mark.unit
def test_reentrant_callback_for_same_broker_skips_reauth(client, monkeypatch):
    """The shortcut this fix preserves: re-hitting the callback for the
    broker the session already holds must NOT re-run auth."""
    calls = []
    monkeypatch.setattr(
        brlogin_module,
        "handle_auth_success",
        lambda auth_token, user, broker, **kw: calls.append((auth_token, user, broker)) or "ok",
    )

    with client.session_transaction() as sess:
        sess["user"] = "prat"
        sess["logged_in"] = True
        sess["broker"] = "stock_simulator"

    response = client.get("/stock_simulator/callback")

    assert calls == []
    assert response.status_code == 302
