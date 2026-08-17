"""No-op auth for NSE historical replay simulator."""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

_SESSION_TOKEN = "stock_simulator_session_token"


def authenticate_broker(code):  # noqa: ANN001
    """OpenAlgo auth entrypoint — no external broker credentials required.

    Deliberately does NOT default ``STOCK_SIMULATOR_MODE`` to "replay" here —
    logging into this broker must not by itself arm a replay. Replay mode is
    only armed by an explicit user/operator action: the Sandbox UI's replay
    form (``/sandbox/api/simulator/config``) or the control API
    (``/stock_simulator/control/replay/start``), both of which set the env
    var themselves. Without an explicit replay, ``mode.effective_mode()``
    resolves to live-during-market-hours instead.
    """
    if not code or code == "stock_simulator":
        os.environ.setdefault("HUB_NO_LEARN", "1")
        return _SESSION_TOKEN, None
    if isinstance(code, str) and len(code) > 20:
        return code, None
    return _SESSION_TOKEN, None


def get_direct_access_token(access_token):  # noqa: ANN001
    if access_token:
        return access_token, None
    return None, "No simulator session token"
