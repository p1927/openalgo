"""Stock-simulator replay controls surfaced through the Sandbox UI.

Split out of ``blueprints/sandbox.py`` (a file upstream owns) so this
fork-only surface lives in a file upstream never touches, keeping the
upstream diff on ``sandbox.py`` at zero. Mounted on the same ``/sandbox``
prefix so the routes (``/sandbox/api/simulator/*``) are unchanged.
"""

import os
from threading import Thread

from flask import Blueprint, jsonify, request

from database.sandbox_db import set_config
from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "50 per second")

simulator_control_bp = Blueprint("simulator_control_bp", __name__, url_prefix="/sandbox")


def _simulator_env_update(payload: dict) -> None:
    """Apply simulator settings to process env (OpenAlgo co-located with Trade)."""
    mapping = {
        "replay_date": "NSE_REPLAY_DATE",
        "replay_time": "NSE_REPLAY_TIME",
        "replay_speed": "NSE_REPLAY_SPEED",
        "replay_loop": "NSE_REPLAY_LOOP",
        "eval_mode": "SIM_EVAL_MODE",
        "week_mode": "NSE_REPLAY_WEEK_MODE",
        "week_days_count": "NSE_REPLAY_WEEK_COUNT",
    }
    for key, env_key in mapping.items():
        if key not in payload:
            continue
        value = payload[key]
        if key in {"replay_loop", "week_mode"}:
            value = "1" if str(value).lower() in {"1", "true", "yes", "on"} else "0"
        os.environ[env_key] = str(value)
    os.environ["STOCK_SIMULATOR_MODE"] = "replay"
    os.environ["HUB_NO_LEARN"] = "1"


@simulator_control_bp.route("/api/simulator/status")
@check_session_validity
@limiter.limit(API_RATE_LIMIT)
def api_simulator_status():
    try:
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.mode import effective_mode
        from trade_integrations.stock_simulator.replay import get_replay_service

        svc = get_replay_service()
        payload = svc.status()
        payload.update(effective_mode())
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
        prior_replay_date = os.getenv("NSE_REPLAY_DATE", "2021-03-25").strip()[:10]
        prior_week_mode = os.getenv("NSE_REPLAY_WEEK_MODE", "1").strip()
        _simulator_env_update(data)
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
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.config import load_sim_config
        from trade_integrations.stock_simulator.replay import get_replay_service

        svc = get_replay_service(reload=True)
        svc.reload(load_sim_config())

        mc_refresh = None
        new_replay_date = os.getenv("NSE_REPLAY_DATE", prior_replay_date).strip()[:10]
        week_mode_changed = False
        if "week_mode" in data:
            new_week = str(data["week_mode"]).lower() in {"1", "true", "yes", "on"}
            old_week = prior_week_mode.lower() in {"1", "true", "yes", "on"}
            week_mode_changed = new_week != old_week
        if "replay_date" in data or week_mode_changed:
            if new_replay_date != prior_replay_date or week_mode_changed:
                from utils.auth_utils import async_master_contract_download

                rebuild_thread = Thread(
                    target=async_master_contract_download, args=("stock_simulator",), daemon=True
                )
                rebuild_thread.start()
                # Bounded wait so the response the frontend acts on (e.g.
                # immediately re-fetching /search/api/expiries to populate
                # the dropdown) doesn't race the rebuild — see the matching
                # comment in stock_simulator_control.py::start_replay.
                rebuild_thread.join(timeout=8.0)
                mc_refresh = "completed" if not rebuild_thread.is_alive() else "started"

        payload = {"status": "success", "simulator": svc.status()}
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
        from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

        ensure_trade_integrations_path()
        from trade_integrations.stock_simulator.replay import get_replay_service

        svc = get_replay_service()
        new_ts = svc.step(minutes=minutes)
        return jsonify({"status": "success", "sim_now": new_ts.isoformat(), "simulator": svc.status()})
    except Exception as e:
        logger.exception("simulator step failed: %s", e)
        return jsonify({"status": "error", "message": str(e)}), 500
