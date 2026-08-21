"""Shared safe Socket.IO emit helper for broker master-contract builders."""

from extensions import socketio
from utils.logging import get_logger

logger = get_logger(__name__)


def safe_emit(event: str, payload: dict) -> None:
    """Emit progress when Flask-SocketIO is initialized (CLI-safe no-op otherwise)."""
    try:
        if getattr(socketio, "server", None) is not None:
            socketio.emit(event, payload)
    except Exception as exc:
        logger.debug("socketio emit skipped: %s", exc)
