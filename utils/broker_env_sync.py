"""
Sync broker access tokens between .env and the auth DB.

IndMoney (and similar brokers) store the live access token in BROKER_API_SECRET.
OpenAlgo copies that token into auth.auth at login. If .env is updated externally
(daily token rotation), the DB can go stale until re-login — this module keeps them
in sync on startup and whenever credentials are saved from the UI.
"""

from __future__ import annotations

import os
import re
from typing import Any

from dotenv import load_dotenv

from utils.logging import get_logger

logger = get_logger(__name__)

# Brokers where BROKER_API_SECRET is the access token (see broker/*/api/auth_api.py).
ENV_TOKEN_BROKERS = frozenset({"indmoney", "groww", "deltaexchange", "dhan_sandbox", "stock_simulator"})


def _env_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(base_dir, "..", ".env"))


def reload_env_from_file() -> None:
    """Hot-reload process env after .env was written to disk."""
    load_dotenv(dotenv_path=_env_path(), override=True)


def get_configured_broker() -> str:
    redirect_url = os.getenv("REDIRECT_URL", "")
    match = re.search(r"/([^/]+)/callback$", redirect_url)
    return match.group(1).lower() if match else ""


def read_env_key_from_file(key: str) -> str:
    """Read a single key from .env, stripping quotes."""
    env_path = _env_path()
    if not os.path.exists(env_path):
        return ""
    try:
        with open(env_path, encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return ""

    pattern = rf"^{re.escape(key)}\s*=\s*(.+)$"
    match = re.search(pattern, content, flags=re.MULTILINE)
    if not match:
        return ""

    val = match.group(1).strip()
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
        val = val[1:-1]
        if match.group(1).strip()[0] == '"':
            val = val.replace('\\"', '"').replace("\\\\", "\\")
    return val


def is_env_token_broker(broker: str | None = None) -> bool:
    broker = (broker or get_configured_broker()).lower()
    return broker in ENV_TOKEN_BROKERS


def get_token_sync_status(username: str | None = None, broker: str | None = None) -> dict[str, Any]:
    """Compare .env BROKER_API_* values with the auth DB token."""
    broker = (broker or get_configured_broker()).lower()
    from utils.broker_credentials import resolve_broker_credentials

    env_key, env_secret = resolve_broker_credentials(broker)
    if not env_key:
        env_key = read_env_key_from_file("BROKER_API_KEY") or os.getenv("BROKER_API_KEY", "")
    if not env_secret:
        env_secret = read_env_key_from_file("BROKER_API_SECRET") or os.getenv("BROKER_API_SECRET", "")

    db_token: str | None = None
    db_username: str | None = None

    if is_env_token_broker(broker):
        from database.auth_db import Auth, db_session, decrypt_token, get_auth_token

        if username:
            db_token = get_auth_token(username, bypass_cache=True)
            db_username = username
        else:
            auth_row = (
                Auth.query.filter_by(broker=broker, is_revoked=False).order_by(Auth.id.desc()).first()
            )
            if auth_row:
                db_username = auth_row.name
                try:
                    db_token = decrypt_token(auth_row.auth) if auth_row.auth else None
                except Exception:
                    db_token = None

    return {
        "broker": broker,
        "is_env_token_broker": is_env_token_broker(broker),
        "api_key": env_key,
        "api_secret": env_secret,
        "db_username": db_username,
        "db_token_set": bool(db_token),
        "env_matches_db": bool(env_secret and db_token and env_secret == db_token),
        "env_secret_set": bool(env_secret),
    }


def sync_env_secret_to_auth_db(
    username: str | None = None,
    broker: str | None = None,
    *,
    reload_env: bool = True,
) -> dict[str, Any]:
    """
    If BROKER_API_SECRET in .env differs from auth DB, upsert the DB token.

    Returns a result dict with synced/count/skipped fields for logging and API responses.
    """
    if reload_env:
        reload_env_from_file()

    broker = (broker or get_configured_broker()).lower()
    if not is_env_token_broker(broker):
        return {"synced": False, "reason": "not_env_token_broker", "broker": broker, "updated_users": []}

    env_secret = (read_env_key_from_file("BROKER_API_SECRET") or os.getenv("BROKER_API_SECRET", "")).strip()
    if not env_secret:
        from utils.broker_credentials import resolve_broker_credentials

        _, env_secret = resolve_broker_credentials(broker)
        env_secret = env_secret.strip()
    if not env_secret or env_secret.startswith("YOUR_"):
        return {"synced": False, "reason": "no_env_secret", "broker": broker, "updated_users": []}

    from database.auth_db import Auth, db_session, decrypt_token, get_auth_token, upsert_auth

    targets: list[tuple[str, str | None]] = []

    if username:
        targets.append((username, None))
    else:
        rows = Auth.query.filter_by(broker=broker, is_revoked=False).all()
        if rows:
            targets = [(row.name, None) for row in rows]
        else:
            from database.user_db import User

            user = User.query.order_by(User.id.asc()).first()
            if user:
                targets.append((user.username, None))

    if not targets:
        return {"synced": False, "reason": "no_target_user", "broker": broker, "updated_users": []}

    updated_users: list[str] = []
    for name, _ in targets:
        current = get_auth_token(name, bypass_cache=True)
        if current == env_secret:
            continue
        upsert_auth(name, env_secret, broker)
        updated_users.append(name)
        logger.info("Synced %s access token from .env to auth DB for user %s", broker, name)

    return {
        "synced": bool(updated_users),
        "reason": "updated" if updated_users else "already_in_sync",
        "broker": broker,
        "updated_users": updated_users,
        "env_matches_db": len(updated_users) == 0,
    }
