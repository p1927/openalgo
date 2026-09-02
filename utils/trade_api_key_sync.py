"""Sync this OpenAlgo instance's own API key into the Trade monorepo's .env.

Fork-only. Called once from ``handle_auth_success`` (utils/auth_utils.py) after
every successful broker login, so Trade-side modules (nautilus_openalgo_bridge,
trade_integrations.execution) always have the API key this OpenAlgo instance
actually issued instead of a stale manually-pasted value. Login itself never
depends on this — any failure here is logged and swallowed.
"""

from __future__ import annotations

from utils.logging import get_logger

logger = get_logger(__name__)


def sync_api_key_to_trade(user_id: str) -> None:
    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()

        from database.auth_db import get_api_key_for_tradingview

        api_key = get_api_key_for_tradingview(user_id)
        if not api_key:
            return

        from trade_integrations.env import sync_openalgo_api_key

        if sync_openalgo_api_key(api_key):
            logger.info("Synced OpenAlgo API key into Trade .env for user %s", user_id)
    except Exception:
        logger.exception("Failed to sync OpenAlgo API key into Trade .env for user %s", user_id)
