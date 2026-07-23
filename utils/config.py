# utils/config.py

import os

from dotenv import load_dotenv

# Load environment variables from .env file with override=True to ensure values are updated
load_dotenv(override=True)


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
