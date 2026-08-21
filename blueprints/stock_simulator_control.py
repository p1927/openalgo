"""Server-to-server control endpoint for the NSE stock simulator's replay clock.

Thin forwarding proxy to the standalone ``stock_simulator`` service —
this process no longer runs its own ``ReplayService`` (see
.claude/backlog/items/2026-08-21-stock-simulator-single-clock-source-of-truth.md).
Kept as a proxy (not deleted) so any existing external caller/doc/SDK
reference to ``/stock_simulator/control/*`` on OpenAlgo's own port keeps
working — VibeTrading's agent no longer calls this at all, it talks to the
service directly.

Auth is unchanged: gated by the caller's ``X-Simulator-Control-Token``
against OpenAlgo's own ``SIMULATOR_CONTROL_TOKEN`` (fails closed if
unconfigured); the forwarded request to the service reuses the same token
value, since both sides are provisioned with the same shared secret.
"""

import hmac
import os

from flask import Blueprint, jsonify, request

from limiter import limiter
from utils.logging import get_logger

logger = get_logger(__name__)

stock_simulator_control_bp = Blueprint(
    "stock_simulator_control_bp", __name__, url_prefix="/stock_simulator/control"
)

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "50 per second")

_TOKEN_HEADER = "X-Simulator-Control-Token"


def _require_control_token():
    """Return an error response dict if the request is not authorized, else None."""
    configured = (os.getenv("SIMULATOR_CONTROL_TOKEN") or "").strip()
    if not configured:
        return jsonify(
            {"status": "error", "message": "simulator control endpoint not configured"}
        ), 503
    provided = (request.headers.get(_TOKEN_HEADER) or "").strip()
    if not provided or not hmac.compare_digest(provided, configured):
        return jsonify({"status": "error", "message": "invalid control token"}), 401
    return None


def _client():
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    return StockSimulatorClient()


def _forward(fn):
    """Call a `StockSimulatorClient` method, mapping its errors to a JSON error response."""
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClientError

    try:
        payload = fn(_client())
    except StockSimulatorClientError as exc:
        status = exc.status_code if exc.status_code and exc.status_code >= 400 else 502
        return jsonify({"status": "error", "message": str(exc)}), status
    return jsonify({"status": "ok", **payload})


@stock_simulator_control_bp.errorhandler(429)
def ratelimit_handler(e):
    return jsonify(
        {"status": "error", "message": "Rate limit exceeded. Please try again later."}
    ), 429


@stock_simulator_control_bp.route("/replay/start", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def start_replay():
    """Arm the simulator to replay a specific previously recorded day."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized

    body = request.get_json(silent=True) or {}
    replay_date = str(body.get("date") or "").strip()
    if not replay_date:
        return jsonify({"status": "error", "message": "date is required"}), 400

    client = _client()
    prior_status = client.status()
    prior_replay_date = (prior_status.get("clock") or {}).get("replay_date", "2021-03-25")[:10]

    result = _forward(
        lambda c: c.start_replay(
            replay_date,
            end_date=body.get("end_date") or None,
            speed=body.get("speed"),
            loop=body.get("loop"),
        )
    )
    if isinstance(result, tuple):
        return result  # error already mapped

    from broker.stock_simulator.api._mc_rebuild import trigger_mc_rebuild_if_date_changed

    new_replay_date = replay_date[:10]
    mc_refresh = trigger_mc_rebuild_if_date_changed(prior_replay_date, new_replay_date)
    if mc_refresh:
        payload = result.get_json()
        payload["master_contract_refresh"] = mc_refresh
        return jsonify(payload)
    return result


@stock_simulator_control_bp.route("/replay/pause", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def pause_replay():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    return _forward(lambda c: c.pause())


@stock_simulator_control_bp.route("/replay/resume", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def resume_replay():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    return _forward(lambda c: c.resume())


@stock_simulator_control_bp.route("/replay/seek", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def seek_replay():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    body = request.get_json(silent=True) or {}
    time_str = str(body.get("time") or "").strip()
    if not time_str:
        return jsonify({"status": "error", "message": "time is required"}), 400
    return _forward(lambda c: c.seek(time_str))


@stock_simulator_control_bp.route("/replay/speed", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def set_replay_speed():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    body = request.get_json(silent=True) or {}
    speed = body.get("speed")
    try:
        speed = float(speed)
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "speed must be a number"}), 400
    if speed < 0:
        return jsonify({"status": "error", "message": "speed must be >= 0"}), 400
    return _forward(lambda c: c.set_speed(speed))


@stock_simulator_control_bp.route("/replay/stop", methods=["POST"])
@limiter.limit(API_RATE_LIMIT)
def stop_replay():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    return _forward(lambda c: c.stop())


@stock_simulator_control_bp.route("/replay/calendar", methods=["GET"])
@limiter.limit(API_RATE_LIMIT)
def replay_calendar():
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    return _forward(lambda c: c.calendar())


@stock_simulator_control_bp.route("/replay/status", methods=["GET"])
@limiter.limit(API_RATE_LIMIT)
def replay_status():
    """Current simulator replay clock state, without forcing a reload."""
    unauthorized = _require_control_token()
    if unauthorized is not None:
        return unauthorized
    return _forward(lambda c: c.status())
