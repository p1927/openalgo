"""Self-connecting session for the ``stock_simulator`` broker (Trade maintainer design D123).

``stock_simulator`` is the NSE-replay/simulated market: its login is a no-op that needs no
credentials, and orders through it never reach a real exchange. So a dead/revoked ``auth`` row
(the daily session-expiry revocation, a logout, an empty row on first boot) carries no
information a human could supply — it is reconnected here with the broker's own no-op token.

Only when the configured broker (``REDIRECT_URL``) is ``stock_simulator``: for any other broker
this module does nothing, and it only ever writes the no-op token.
Hooks: ``broker_env_sync.sync_env_token_brokers_on_startup`` (boot) and
``auth_db.get_auth_token_broker`` (the revoked-session read path every API call goes through).
"""

from __future__ import annotations

from utils.broker_env_sync import get_configured_broker
from utils.logging import get_logger

logger = get_logger(__name__)

BROKER = "stock_simulator"


def reconnect_if_configured(username: str | None = None) -> bool:
    """Write the no-op session token into the auth row; True only if a row was (re)written."""
    if get_configured_broker() != BROKER:
        return False
    from broker.stock_simulator.api.auth_api import authenticate_broker
    from database.auth_db import upsert_auth

    if not username:
        from database.user_db import User

        user = User.query.order_by(User.id.asc()).first()
        if user is None:
            return False
        username = user.username
    token, error = authenticate_broker(BROKER)
    if error or not token:
        logger.warning("stock_simulator no-op login failed: %s", error)
        return False
    if upsert_auth(username, token, BROKER) is None:
        return False
    logger.info("stock_simulator session auto-connected for user %s", username)
    return True
