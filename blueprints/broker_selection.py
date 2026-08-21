"""Broker selection/connect routes for the React SPA login flow (fork-only).

Split out of ``blueprints/auth.py`` (a file upstream owns), which upstream
uses for a single ``/auth/broker-config`` route we replaced entirely with
these two. Mounted on the same ``/auth`` prefix so the routes
(``/auth/brokers``, ``/auth/broker/prepare-connect``) are unchanged —
``app.py``'s CSRF-exempt path checks reference those literal paths, not
this blueprint object, so they don't need updating.
"""

import secrets

from flask import Blueprint, jsonify, request, session

from utils.logging import get_logger

logger = get_logger(__name__)

broker_selection_bp = Blueprint("broker_selection", __name__, url_prefix="/auth")


@broker_selection_bp.route("/brokers", methods=["GET"])
def list_brokers():
    """Return available brokers with display metadata for the login UI."""
    if "user" not in session:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    from utils.broker_registry import get_default_broker, list_available_brokers, resolve_default_broker_for_list

    brokers = list_available_brokers()
    broker_ids = [broker.id for broker in brokers]
    default_broker = resolve_default_broker_for_list(broker_ids)
    brokers = list_available_brokers(default_broker=default_broker or "")

    return jsonify(
        {
            "status": "success",
            "default_broker": default_broker,
            "brokers": [broker.to_dict() for broker in brokers],
        }
    )


@broker_selection_bp.route("/broker/prepare-connect", methods=["POST"])
def prepare_broker_connect():
    """Apply broker credentials and return the connect URL for the selected broker."""
    if "user" not in session:
        return jsonify({"status": "error", "message": "Authentication required"}), 401

    data = request.get_json(silent=True) or {}
    broker_id = str(data.get("broker") or "").strip().lower()
    if not broker_id:
        return jsonify({"status": "error", "message": "broker is required"}), 400

    from utils.broker_registry import get_broker_descriptor, list_available_brokers, parse_valid_brokers_env

    allowed = {b.id for b in list_available_brokers()}
    if broker_id not in allowed:
        valid = parse_valid_brokers_env()
        return jsonify(
            {
                "status": "error",
                "message": (
                    f"Broker '{broker_id}' is not available. "
                    f"Check VALID_BROKERS and broker/{broker_id}/plugin.json."
                ),
                "valid_brokers": valid,
            }
        ), 400

    from utils.broker_credentials import apply_broker_credentials
    from utils.broker_login import build_connect_url, build_redirect_url_for_broker

    descriptor = get_broker_descriptor(broker_id)
    if descriptor and not descriptor.credentials_configured:
        return jsonify(
            {
                "status": "error",
                "message": (
                    f"Broker '{broker_id}' credentials are not configured. "
                    "Add API keys in Profile or per-broker env vars in .env."
                ),
            }
        ), 400

    apply_broker_credentials(broker_id)
    session["connect_broker"] = broker_id

    pocketful_state = None
    if descriptor and descriptor.auth_flow == "oauth_external" and broker_id == "pocketful":
        pocketful_state = secrets.token_urlsafe(16)
        session["pocketful_oauth_state"] = pocketful_state

    redirect_url = build_redirect_url_for_broker(broker_id)
    connect_url = build_connect_url(
        broker_id,
        redirect_url=redirect_url,
        pocketful_state=pocketful_state,
    )

    notice = descriptor.login_notice if descriptor else None
    auth_flow = descriptor.auth_flow if descriptor else "callback"

    return jsonify(
        {
            "status": "success",
            "broker": broker_id,
            "connect_url": connect_url,
            "auth_flow": auth_flow,
            "login_notice": notice,
            "redirect_url": redirect_url,
        }
    )
