"""No-op auth for NSE historical replay simulator."""

from __future__ import annotations

import os

from utils.logging import get_logger

logger = get_logger(__name__)

_SESSION_TOKEN = "stock_simulator_session_token"


def authenticate_broker(code):  # noqa: ANN001
    """OpenAlgo auth entrypoint — no external broker credentials required."""
    if not code or code == "stock_simulator":
        os.environ.setdefault("STOCK_SIMULATOR_MODE", "replay")
        os.environ.setdefault("HUB_NO_LEARN", "1")
        return _SESSION_TOKEN, None
    if isinstance(code, str) and len(code) > 20:
        return code, None
    return _SESSION_TOKEN, None


def get_direct_access_token(access_token):  # noqa: ANN001
    if access_token:
        return access_token, None
    return None, "No simulator session token"
