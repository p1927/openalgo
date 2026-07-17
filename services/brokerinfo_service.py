from __future__ import annotations

from typing import Any

from database.auth_db import get_broker_name, verify_api_key
from database.settings_db import get_analyze_mode
from utils.broker_env_sync import get_configured_broker, get_token_sync_status, is_env_token_broker
from utils.logging import get_logger

logger = get_logger(__name__)


def get_brokerinfo(api_key: str | None = None) -> tuple[bool, dict[str, Any], int]:
    """Return connected broker metadata for a valid OpenAlgo API key."""
    if not api_key:
        return (
            False,
            {"status": "error", "message": "apikey is required"},
            400,
        )

    username = verify_api_key(api_key)
    if username is None:
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403

    session_broker = (get_broker_name(api_key) or "").strip().lower()
    configured_broker = get_configured_broker()
    broker = session_broker or configured_broker

    token_status: dict[str, Any] = {}
    token_sync_ok: bool | None = None
    if broker:
        token_status = get_token_sync_status(username=username, broker=broker)
        if token_status.get("is_env_token_broker"):
            token_sync_ok = bool(token_status.get("env_matches_db"))

    data = {
        "broker": broker or None,
        "configured_broker": configured_broker or None,
        "session_broker": session_broker or None,
        "analyze_mode": get_analyze_mode(),
        "is_env_token_broker": bool(token_status.get("is_env_token_broker")) if token_status else False,
        "token_sync_ok": token_sync_ok,
        "env_secret_set": bool(token_status.get("env_secret_set")) if token_status else None,
        "db_token_set": bool(token_status.get("db_token_set")) if token_status else None,
    }
    return True, {"status": "success", "data": data}, 200
