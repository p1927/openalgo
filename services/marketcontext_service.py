from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from database.auth_db import get_broker_name, verify_api_key
from database.settings_db import get_analyze_mode
from utils.broker_env_sync import get_configured_broker
from utils.logging import get_logger

logger = get_logger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_US_BROKERS = frozenset({"alpaca"})
_IN_CAPABILITIES = ("options", "equity", "basket", "websocket")
_US_CAPABILITIES = ("equity", "basket")


def _market_region_for_broker(broker: str) -> str:
    key = str(broker or "").strip().lower()
    if key in _US_BROKERS:
        return "US"
    return "IN"


def _simulator_block(broker: str) -> dict[str, Any]:
    if str(broker or "").strip().lower() != "stock_simulator":
        return {"active": False}
    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.replay import get_replay_service

        status = get_replay_service().status()
        clock = status.get("clock") if isinstance(status.get("clock"), dict) else {}
        return {
            "active": True,
            "mode": status.get("mode"),
            "replay_date": clock.get("replay_date"),
            "session_open": bool(clock.get("session_open", True)),
            "sim_now": clock.get("sim_now"),
            "speed": clock.get("speed"),
        }
    except Exception:
        logger.debug("simulator status unavailable for marketcontext", exc_info=True)
        return {"active": True}


def _execution_fields(*, data_broker: str, analyze_mode: bool) -> tuple[str, str, tuple[str, ...]]:
    if data_broker in _US_BROKERS:
        if analyze_mode:
            return "paper-api.alpaca.markets", "alpaca.paper", _US_CAPABILITIES
        return "api.alpaca.markets", "alpaca", _US_CAPABILITIES
    if analyze_mode:
        return "sandbox", "sandbox.db", _IN_CAPABILITIES
    return "broker", "broker", _IN_CAPABILITIES


def build_marketcontext_data(
    *,
    broker: str,
    analyze_mode: bool,
) -> dict[str, Any]:
    data_broker = str(broker or "").strip().lower() or "unknown"
    execution_venue, positions_authority, capabilities = _execution_fields(
        data_broker=data_broker,
        analyze_mode=bool(analyze_mode),
    )
    return {
        "context_generation": datetime.now(tz=IST).isoformat(),
        "data_broker": data_broker,
        "execution_venue": execution_venue,
        "analyze_mode": bool(analyze_mode),
        "market_region": _market_region_for_broker(data_broker),
        "positions_authority": positions_authority,
        "quotes_source": "broker_plugin",
        "simulator": _simulator_block(data_broker),
        "capabilities": list(capabilities),
    }


def get_marketcontext(api_key: str | None = None) -> tuple[bool, dict[str, Any], int]:
    """Return unified market context for the OpenAlgo instance."""
    if not api_key:
        return False, {"status": "error", "message": "apikey is required"}, 400

    username = verify_api_key(api_key)
    if username is None:
        return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403

    session_broker = (get_broker_name(api_key) or "").strip().lower()
    configured_broker = get_configured_broker()
    broker = session_broker or configured_broker
    analyze_mode = get_analyze_mode()
    data = build_marketcontext_data(broker=broker, analyze_mode=analyze_mode)
    return True, {"status": "success", "data": data}, 200
