"""Compute F&O trade charges for Strategy Builder (session-authed)."""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request, session

from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

trade_charges_bp = Blueprint("trade_charges_bp", __name__, url_prefix="/")

READ_LIMIT = os.getenv("TRADE_CHARGES_LIMIT", "120 per minute")


def _import_charges():
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.dataflows.options_research.payoff_charges import (
        calculate_charges,
        calculate_charges_with_exit,
    )

    return calculate_charges, calculate_charges_with_exit


@trade_charges_bp.route("/api/trade-charges", methods=["POST"])
@check_session_validity
@limiter.limit(READ_LIMIT)
def post_trade_charges():
    """Return per-leg and total brokerage/STT/GST/stamp/exchange for strategy legs."""
    payload = request.get_json(silent=True) or {}
    legs = payload.get("legs")
    if not isinstance(legs, list) or not legs:
        return jsonify({"status": "error", "message": "legs array required"}), 400

    include_exit = bool(payload.get("include_exit", True))
    spot = payload.get("spot")
    try:
        spot_f = float(spot) if spot is not None else 0.0
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "spot must be a number"}), 400

    try:
        calculate_charges, calculate_charges_with_exit = _import_charges()
        # The one preset resolution (same as the get_trade_charges MCP tool): explicit preset, else the
        # session broker, else env, else the presets default.
        from trade_integrations.research.broker_context import resolve_broker_preset

        broker_preset = resolve_broker_preset(
            openalgo_session_broker=payload.get("broker_preset") or session.get("broker")
        )
        if include_exit and spot_f > 0:
            charges = calculate_charges_with_exit(legs, spot=spot_f, broker_preset=broker_preset)
        else:
            charges = calculate_charges(legs, broker_preset=broker_preset)
    except Exception as exc:
        logger.warning("trade charges failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({"status": "success", "charges": charges})
