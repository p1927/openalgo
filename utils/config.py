# utils/config.py

import os

from dotenv import load_dotenv

# Load environment variables from .env file with override=True to ensure values are updated.
#
# FLASK_DEBUG is deliberately exempted: `trade dev` starts OpenAlgo as
# `env FLASK_DEBUG=1 python app.py` specifically so Werkzeug's auto-reloader
# picks up code changes (see scripts/stack_lib.sh::stack_start_openalgo).
# This module is imported (via blueprints.auth / blueprints.brlogin) before
# app.py reads FLASK_DEBUG in its `__main__` block, so an unconditional
# override=True here silently stomps that pre-set value with .env's usual
# `FLASK_DEBUG=False` — the process still starts, but with no reloader and
# no error, so edits stop taking effect with no visible symptom.
_preset_flask_debug = os.environ.get("FLASK_DEBUG")
load_dotenv(override=True)
if _preset_flask_debug is not None:
    os.environ["FLASK_DEBUG"] = _preset_flask_debug


def _active_broker() -> str:
    from utils.broker_env_sync import get_configured_broker

    return get_configured_broker()


def get_broker_api_key() -> str | None:
    """
    Retrieve the broker API key for the active broker (REDIRECT_URL).

    Resolves from per-broker env vars (e.g. ALPACA_API_KEY) then BROKER_API_KEY.
    """
    from utils.broker_credentials import resolve_broker_credentials

    broker = _active_broker()
    if broker:
        key, _ = resolve_broker_credentials(broker)
        if key:
            return key
    return os.getenv("BROKER_API_KEY")


def get_broker_api_secret() -> str | None:
    """
    Retrieve the broker API secret for the active broker.

    Resolves from per-broker env vars (e.g. INDMONEY_ACCESS_TOKEN) then BROKER_API_SECRET.
    """
    from utils.broker_credentials import resolve_broker_credentials

    broker = _active_broker()
    if broker:
        _, secret = resolve_broker_credentials(broker)
        if secret:
            return secret
    return os.getenv("BROKER_API_SECRET")


def get_login_rate_limit_min() -> str:
    """
    Retrieve the rate limit for logins per minute.

    Returns:
        str: The rate limit string (e.g., '5 per minute').
    """
    return os.getenv("LOGIN_RATE_LIMIT_MIN", "5 per minute")


def get_login_rate_limit_hour() -> str:
    """
    Retrieve the rate limit for logins per hour.

    Returns:
        str: The rate limit string (e.g., '25 per hour').
    """
    return os.getenv("LOGIN_RATE_LIMIT_HOUR", "25 per hour")


def get_host_server() -> str:
    """
    Retrieve the host server URL.

    Returns:
        str: The host server URL string.
    """
    return os.getenv("HOST_SERVER", "http://127.0.0.1:5000")


def build_external_url(path: str) -> str:
    """
    Build an absolute URL for an outbound link, rooted at the configured HOST_SERVER.

    Use this instead of ``url_for(..., _external=True)`` for any URL that leaves
    the server - password reset emails, broker OAuth callbacks. Flask derives the
    scheme and host for ``_external=True`` from the incoming request's ``Host``
    header, which is attacker-controlled: a poisoned header rewrites the link to
    an origin of the attacker's choosing, and whoever follows it (the account
    owner reading their email, or the broker redirecting after OAuth) hands over
    the token in the URL. HOST_SERVER is set by every official install script and
    is not influenced by the request, so it cannot be poisoned this way.

    Args:
        path: Root-relative path, normally the return value of ``url_for()``
            called without ``_external``. A leading slash is optional.

    Returns:
        str: Absolute URL rooted at HOST_SERVER.
    """
    base = get_host_server().strip().rstrip("/")
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{base}{path}"

