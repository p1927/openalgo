"""Stock-simulator replay controls surfaced through the Sandbox UI.

Split out of ``blueprints/sandbox.py`` (a file upstream owns) so this
fork-only surface lives in a file upstream never touches, keeping the
upstream diff on ``sandbox.py`` at zero. Mounted on the same ``/sandbox``
prefix so the routes (``/sandbox/api/simulator/*``) are unchanged.

Proxies to the standalone ``stock_simulator`` service via
``StockSimulatorClient`` rather than a local ``ReplayService`` — this
process no longer runs its own sim clock (see
.claude/backlog/items/2026-08-21-stock-simulator-single-clock-source-of-truth.md).
Browser-session auth (``check_session_validity``) stays here since the
service itself only understands the shared-secret control token, not user
sessions.
"""

import os

from flask import Blueprint, jsonify, request

from database.sandbox_db import set_config
from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "50 per second")

simulator_control_bp = Blueprint("simulator_control_bp", __name__, url_prefix="/sandbox")


def _client():
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.client import StockSimulatorClient

    return StockSimulatorClient()


@simulator_control_bp.route("/api/simulator/status")
@check_session_validity
@limiter.limit(API_RATE_LIMIT)
def api_simulator_status():
    try:
        client = _client()
        payload = client.status()
        payload.update(client.data_status()["mode"])
        return jsonify({"status": "success", "simulator": payload})
    except Exception as e:
        logger.exception("simulator status failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@simulator_control_bp.route("/api/simulator/config", methods=["POST"])
@check_session_validity
@limiter.limit(API_RATE_LIMIT)
def api_simulator_config():
    try:
        data = request.get_json(silent=True) or {}
        client = _client()
        prior_status = client.status()
        prior_replay_date = (prior_status.get("clock") or {}).get("replay_date", "2021-03-25")[:10]
        prior_week_mode = bool(prior_status.get("week_mode"))

        for key in (
            "replay_date",
            "replay_time",
            "replay_speed",
            "replay_loop",
            "eval_mode",
            "week_mode",
            "week_days_count",
        ):
            if key in data:
                set_config(f"sim_{key}", str(data[key]), description=f"Simulator {key}")

        status = client.configure(**data)

        mc_refresh = None
        new_replay_date = (status.get("clock") or {}).get("replay_date", prior_replay_date)[:10]
        week_mode_changed = False
        if "week_mode" in data:
            new_week = str(data["week_mode"]).lower() in {"1", "true", "yes", "on"}
            week_mode_changed = new_week != prior_week_mode
        if "replay_date" in data or week_mode_changed:
            from broker.stock_simulator.api._mc_rebuild import trigger_mc_rebuild_if_date_changed

            mc_refresh = trigger_mc_rebuild_if_date_changed(
                prior_replay_date, new_replay_date, force=week_mode_changed
            )

        payload = {"status": "success", "simulator": status}
        if mc_refresh:
            payload["master_contract_refresh"] = mc_refresh
        return jsonify(payload)
    except Exception as e:
        logger.exception("simulator config failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500


@simulator_control_bp.route("/api/simulator/step", methods=["POST"])
@check_session_validity
@limiter.limit(API_RATE_LIMIT)
def api_simulator_step():
    try:
        data = request.get_json(silent=True) or {}
        minutes = int(data.get("minutes") or 5)
        client = _client()
        status = client.step(minutes=minutes)
        sim_now = (status.get("clock") or {}).get("sim_now")
        return jsonify({"status": "success", "sim_now": sim_now, "simulator": status})
    except Exception as e:
        logger.exception("simulator step failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500
