"""Alpaca API key authentication — env vars or login payload."""

from __future__ import annotations

import os

from broker.alpaca.api._http import alpaca_request, resolve_api_secret
from broker.alpaca.api.baseurl import trade_base
from utils.logging import get_logger

logger = get_logger(__name__)


def _env_api_key() -> str:
    from utils.broker_credentials import resolve_broker_credentials

    key, _ = resolve_broker_credentials("alpaca")
    return key or (
        (os.getenv("BROKER_API_KEY") or "")
        or (os.getenv("ALPACA_API_KEY") or "")
    ).strip()


def _parse_login_code(code: str | None) -> tuple[str, str]:
    """Accept api_key, key:::secret, or key:secret from login form."""
    if not code or code in {"alpaca", "paper", "live"}:
        return _env_api_key(), resolve_api_secret()

    raw = code.strip()
    if ":::" in raw:
        key, secret = raw.split(":::", 1)
        return key.strip(), secret.strip()
    if ":" in raw and not raw.startswith("http"):
        key, secret = raw.split(":", 1)
        if secret.strip():
            return key.strip(), secret.strip()
    return raw, resolve_api_secret()


def _verify_credentials(api_key: str, api_secret: str) -> tuple[bool, str | None]:
    if not api_key:
        return False, "API key is required"
    if not api_secret:
        return False, "API secret is required (set BROKER_API_SECRET or pass key:secret on login)"

    url = f"{trade_base()}/v2/account"
    status, payload = alpaca_request("GET", url, api_key, api_secret=api_secret)
    if status == 200 and isinstance(payload, dict) and payload.get("status") != "ERROR":
        logger.info("Alpaca authentication successful for account %s", payload.get("account_number"))
        return True, None
    message = ""
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("error") or str(payload)
    return False, f"Alpaca authentication failed ({status}): {message}"


def authenticate_broker(code):  # noqa: ANN001
    """Validate Alpaca API key + secret from env or login code."""
    try:
        api_key, api_secret = _parse_login_code(code)
        ok, error = _verify_credentials(api_key, api_secret)
        if ok:
            return api_key, None
        return None, error
    except Exception as exc:
        logger.exception("Alpaca authenticate_broker failed")
        return None, str(exc)


def get_direct_access_token(access_token):  # noqa: ANN001
    """Validate a stored Alpaca API key."""
    if not access_token or len(str(access_token).strip()) < 8:
        return None, "Invalid Alpaca API key format"
    api_key = str(access_token).strip()
    ok, error = _verify_credentials(api_key, resolve_api_secret())
    if ok:
        return api_key, None
    return None, error or "Invalid Alpaca credentials"


def test_auth_token(auth_token: str) -> tuple[bool, str | None]:
    """Funds-service hook — confirm token still valid."""
    ok, error = _verify_credentials(auth_token, resolve_api_secret())
    return ok, error
