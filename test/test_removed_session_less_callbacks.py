"""rmoney / compositedge / iiflcapital callbacks were removed: they set the admin session without a login."""

import inspect

import pytest
from flask import Flask, session

from blueprints import brlogin

REMOVED = ("rmoney", "compositedge", "iiflcapital")


def _call(broker, *, method="GET", logged_in=False, **kwargs):
    app = Flask(__name__)
    app.secret_key = "test-only"
    # Even if an operator lists the broker in VALID_BROKERS and its auth function loads, the route stays dead.
    app.broker_auth_functions = {f"{broker}_auth": lambda *_a: ("tok", "feed", "uid", None)}
    callback = inspect.unwrap(brlogin.broker_callback)
    with app.test_request_context(f"/{broker}/callback", method=method, **kwargs):
        if logged_in:
            session["user"] = "alice"
        response = callback(broker)
        return response, dict(session)


@pytest.mark.parametrize("broker", REMOVED)
@pytest.mark.parametrize("method", ["GET", "POST"])
def test_removed_callback_never_grants_a_session(broker, method):
    kwargs = {"json": {"token": "forged", "userID": "x"}} if method == "POST" else {}
    (_body, status), sess = _call(broker, method=method, **kwargs)
    assert status == 404
    assert "user" not in sess and "broker" not in sess


@pytest.mark.parametrize("broker", REMOVED)
def test_removed_callback_refused_even_with_a_session(broker):
    (_body, status), sess = _call(broker, logged_in=True)
    assert status == 404
    assert sess.get("broker") is None


def test_source_has_no_session_less_admin_fallback():
    src = inspect.getsource(brlogin)
    assert "find_user_by_username" not in src
    for name in REMOVED:
        assert f'elif broker == "{name}"' not in src
