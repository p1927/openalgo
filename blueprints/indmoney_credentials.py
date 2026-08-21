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
