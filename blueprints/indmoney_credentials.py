"""IndMoney credential management API (fork-only broker).

Split out of ``blueprints/broker_credentials.py`` (a file upstream owns) so
this fork-only surface lives in a file upstream never touches. Mounted on
the same ``/api/broker`` prefix as ``broker_credentials_bp`` so the routes
(``/api/broker/indmoney-token``, ``/api/broker/indmoney-token/sync``) are
unchanged; reuses that file's ``.env``-editing helpers rather than
duplicating them.
"""

from flask import Blueprint, jsonify, request, session

from blueprints.broker_credentials import get_env_path, read_env_file, update_env_value
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

indmoney_credentials_bp = Blueprint("indmoney_credentials_bp", __name__, url_prefix="/api/broker")


def _recorder_token_health():
    """Import the Trade-owned recorder helper without coupling to auth_db."""
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.stock_simulator.recorder import indmoney_token_health

    return indmoney_token_health


@indmoney_credentials_bp.route("/indmoney-recorder-token", methods=["GET"])
@check_session_validity
def get_indmoney_recorder_token():
    """Masked direct-INDmoney health for the independent recorder credential."""
    return jsonify({"status": "success", "data": _recorder_token_health().probe()})


_PHASE_LABELS = {
    "live_recorder": "Recorder / stock_simulator service",
    "vibe_agent": "Vibe-Trading agent (predictions tab)",
}


def _service_down_note(phase: str) -> dict[str, str]:
    return {
        "phase": phase,
        "label": _PHASE_LABELS[phase],
        "code": "service_not_running",
        "message": f"{_PHASE_LABELS[phase]} isn't running — start it, then paste the token again to sync it.",
    }


def _reload_failed_note(phase: str, detail: str) -> dict[str, str]:
    return {
        "phase": phase,
        "label": _PHASE_LABELS[phase],
        "code": "reload_failed",
        "message": f"{_PHASE_LABELS[phase]} is running but rejected the reload: {detail}",
    }


@indmoney_credentials_bp.route("/indmoney-recorder-token", methods=["POST"])
@check_session_validity
def replace_indmoney_recorder_token():
    """Validate, persist root token, mirror env, hot-reload recorder + vibe-agent, sync OpenAlgo DB."""
    data = request.get_json(silent=True) or {}
    token = str(data.get("token") or "").strip()
    if not token:
        return jsonify({"status": "error", "message": "Access token is required"}), 400
    result = _recorder_token_health().save_root_token(token)
    if result.get("status") != "valid":
        return jsonify({"status": "error", "message": "INDmoney connection test failed", "data": result}), 422

    phases = {"root_env": "applied", "openalgo_env": "synced"}
    notes = []
    live_data: dict = {}

    from trade_integrations.stock_simulator.client import StockSimulatorClient, StockSimulatorClientError
    try:
        live = StockSimulatorClient().reload_indmoney_token()
        live_data = live.get("data") or {}
        phases["live_recorder"] = str(live_data.get("status") or "unknown")
    except StockSimulatorClientError as exc:
        if exc.status_code is None:
            phases["live_recorder"] = "service_not_running"
            notes.append(_service_down_note("live_recorder"))
            logger.warning("INDmoney recorder reload skipped: service unreachable: %s", exc)
        else:
            phases["live_recorder"] = "reload_failed"
            notes.append(_reload_failed_note("live_recorder", str(exc)))
            logger.exception("INDmoney recorder token applied but live reload failed")

    from utils.broker_env_sync import reload_env_from_file, sync_env_secret_to_auth_db

    reload_env_from_file()
    db_sync = sync_env_secret_to_auth_db(username=session.get("user"), broker="indmoney")
    phases["openalgo_db"] = "synced" if db_sync.get("synced") or db_sync.get("env_matches_db") else str(db_sync.get("reason"))

    from trade_integrations.stock_simulator.recorder.indmoney_token_health import (
        VibeAgentReloadError,
        VibeAgentUnreachableError,
    )
    try:
        vibe = _recorder_token_health().reload_vibe_agent()
        phases["vibe_agent"] = str((vibe or {}).get("status") or "unknown")
    except VibeAgentUnreachableError as exc:
        phases["vibe_agent"] = "service_not_running"
        notes.append(_service_down_note("vibe_agent"))
        logger.warning("INDmoney vibe-agent reload skipped: service unreachable: %s", exc)
    except VibeAgentReloadError as exc:
        phases["vibe_agent"] = "reload_failed"
        notes.append(_reload_failed_note("vibe_agent", str(exc)))
        logger.exception("INDmoney recorder token applied but vibe-agent reload failed")

    if notes:
        summary = "Token saved and applied to OpenAlgo. " + " ".join(n["message"] for n in notes)
        return jsonify({"status": "degraded", "message": summary, "data": live_data, "phases": phases, "notes": notes}), 502
    return jsonify({"status": "success", "data": live_data, "phases": phases})


@indmoney_credentials_bp.route("/indmoney-token", methods=["GET"])
@check_session_validity
def get_indmoney_token():
    """Return IndMoney API credentials from .env and DB sync status."""
    from utils.broker_env_sync import get_token_sync_status

    status = get_token_sync_status(broker="indmoney")
    return jsonify({"status": "success", "data": status})


@indmoney_credentials_bp.route("/indmoney-token", methods=["POST"])
@check_session_validity
def update_indmoney_token():
    """Update IndMoney credentials in .env and sync access token to auth DB."""
    from utils.broker_env_sync import (
        get_token_sync_status,
        reload_env_from_file,
        sync_env_secret_to_auth_db,
    )

    if request.is_json:
        data = request.get_json() or {}
        api_key = data.get("api_key", "").strip()
        api_secret = data.get("api_secret", "").strip()
    else:
        api_key = request.form.get("api_key", "").strip()
        api_secret = request.form.get("api_secret", "").strip()

    if not api_key and not api_secret:
        return jsonify({"status": "error", "message": "Provide api_key and/or api_secret"}), 400

    content, error = read_env_file()
    if error:
        return jsonify({"status": "error", "message": f"Failed to read .env file: {error}"}), 500

    updated_fields = []
    if api_key:
        content = update_env_value(content, "BROKER_API_KEY", api_key)
        updated_fields.append("BROKER_API_KEY")
    if api_secret:
        content = update_env_value(content, "BROKER_API_SECRET", api_secret)
        updated_fields.append("BROKER_API_SECRET")

    env_path = get_env_path()
    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        logger.exception(f"Error writing .env file: {e}")
        return jsonify({"status": "error", "message": f"Failed to write .env file: {e}"}), 500

    reload_env_from_file()
    sync_result = sync_env_secret_to_auth_db(username=session.get("user"), broker="indmoney")
    token_status = get_token_sync_status(username=session.get("user"), broker="indmoney")

    return jsonify(
        {
            "status": "success",
            "message": "IndMoney credentials saved to .env and synced to database",
            "updated_fields": updated_fields,
            "db_sync": sync_result,
            "data": token_status,
            "restart_required": False,
        }
    )


@indmoney_credentials_bp.route("/indmoney-token/sync", methods=["POST"])
@check_session_validity
def sync_indmoney_token():
    """Force-sync BROKER_API_SECRET from .env into auth DB (e.g. after manual .env edit)."""
    from utils.broker_env_sync import get_token_sync_status, sync_env_secret_to_auth_db

    sync_result = sync_env_secret_to_auth_db(username=session.get("user"), broker="indmoney")
    token_status = get_token_sync_status(username=session.get("user"), broker="indmoney")
    return jsonify(
        {
            "status": "success",
            "db_sync": sync_result,
            "data": token_status,
        }
    )
