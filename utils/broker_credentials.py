"""Resolve active-broker API credentials from per-broker .env keys.

Store all broker keys once (ALPACA_API_KEY, INDMONEY_ACCESS_TOKEN, …).
``apply_broker_credentials(broker)`` copies the matching pair into
``BROKER_API_KEY`` / ``BROKER_API_SECRET`` for legacy broker plugins.
"""

from __future__ import annotations

import os
from typing import Iterable

from utils.logging import get_logger

logger = get_logger(__name__)

# (api_key env vars, api_secret env vars) — first non-empty wins per side.
BROKER_CREDENTIAL_ALIASES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "alpaca": (
        ("ALPACA_API_KEY",),
        ("ALPACA_API_SECRET", "ALPACA_SECRET_KEY"),
    ),
    "indmoney": (
        ("INDMONEY_API_KEY",),
        ("INDMONEY_ACCESS_TOKEN", "INDMONEY_API_SECRET"),
    ),
    "zerodha": (
        ("ZERODHA_API_KEY",),
        ("ZERODHA_API_SECRET",),
    ),
    "dhan": (
        ("DHAN_API_KEY",),
        ("DHAN_API_SECRET",),
    ),
    "dhan_sandbox": (
        ("DHAN_SANDBOX_API_KEY", "DHAN_API_KEY"),
        ("DHAN_SANDBOX_API_SECRET", "DHAN_API_SECRET"),
    ),
    "angel": (
        ("ANGEL_API_KEY",),
        ("ANGEL_API_SECRET",),
    ),
    "fyers": (
        ("FYERS_API_KEY", "FYERS_APP_ID"),
        ("FYERS_API_SECRET", "FYERS_SECRET_KEY"),
    ),
    "shoonya": (
        ("SHOONYA_API_KEY",),
        ("SHOONYA_API_SECRET",),
    ),
    "upstox": (
        ("UPSTOX_API_KEY",),
        ("UPSTOX_API_SECRET",),
    ),
    "groww": (
        ("GROWW_API_KEY",),
        ("GROWW_API_SECRET", "GROWW_ACCESS_TOKEN"),
    ),
    "deltaexchange": (
        ("DELTAEXCHANGE_API_KEY",),
        ("DELTAEXCHANGE_API_SECRET",),
    ),
    "stock_simulator": ((), ()),
}


def _first_env(*names: str) -> str:
    from utils.broker_env_sync import read_env_key_from_file

    for name in names:
        if not name:
            continue
        val = (os.getenv(name) or read_env_key_from_file(name) or "").strip()
        if val and not val.startswith("YOUR_"):
            return val
    return ""


def _alias_tuple(broker: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    key = broker.lower().strip()
    if key in BROKER_CREDENTIAL_ALIASES:
        return BROKER_CREDENTIAL_ALIASES[key]
    prefix = key.upper().replace("-", "_")
    return (f"{prefix}_API_KEY",), (f"{prefix}_API_SECRET", f"{prefix}_ACCESS_TOKEN")


def resolve_broker_credentials(broker: str) -> tuple[str, str]:
    """Return (api_key, api_secret) for broker from dedicated env vars."""
    broker = str(broker or "").strip().lower()
    if not broker:
        return _first_env("BROKER_API_KEY"), _first_env("BROKER_API_SECRET")

    from utils.broker_registry import get_default_broker

    default_broker = (get_default_broker() or "").lower()
    allow_global_fallback = broker == default_broker

    key_vars, secret_vars = _alias_tuple(broker)

    if key_vars:
        api_key = _first_env(*key_vars)
        if not api_key and allow_global_fallback:
            api_key = _first_env("BROKER_API_KEY")
    else:
        api_key = _first_env("BROKER_API_KEY")

    if secret_vars:
        api_secret = _first_env(*secret_vars)
        if not api_secret and allow_global_fallback:
            api_secret = _first_env("BROKER_API_SECRET")
    else:
        api_secret = _first_env("BROKER_API_SECRET")

    return api_key, api_secret


def apply_broker_credentials(broker: str | None = None) -> tuple[str, str]:
    """Copy resolved credentials into BROKER_API_KEY / BROKER_API_SECRET."""
    if broker is None:
        from utils.broker_env_sync import get_configured_broker

        broker = get_configured_broker()
    broker = str(broker or "").strip().lower()
    if not broker:
        return "", ""

    api_key, api_secret = resolve_broker_credentials(broker)
    if api_key:
        os.environ["BROKER_API_KEY"] = api_key
    if api_secret:
        os.environ["BROKER_API_SECRET"] = api_secret
    if api_key or api_secret:
        logger.debug(
            "Applied broker credentials for %s (key=%s secret=%s)",
            broker,
            "set" if api_key else "empty",
            "set" if api_secret else "empty",
        )
    return api_key, api_secret


def list_credential_env_vars(broker: str) -> Iterable[str]:
    """Env var names consulted for a broker (for UI/docs)."""
    key_vars, secret_vars = _alias_tuple(broker.lower().strip())
    yield from key_vars
    yield from secret_vars
