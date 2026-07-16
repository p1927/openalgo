"""Compute F&O trade charges for Strategy Builder (session-authed)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request, session

from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

trade_charges_bp = Blueprint("trade_charges_bp", __name__, url_prefix="/")

READ_LIMIT = os.getenv("TRADE_CHARGES_LIMIT", "120 per minute")


def _import_charges():
    trade_root = Path(__file__).resolve().parents[2]
    integrations = trade_root / "integrations"
    if integrations.is_dir() and str(integrations) not in sys.path:
        sys.path.insert(0, str(integrations))
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

    broker_preset = str(
        payload.get("broker_preset") or session.get("broker") or "indmoney"
    ).lower()
    include_exit = bool(payload.get("include_exit", True))
    spot = payload.get("spot")
    try:
        spot_f = float(spot) if spot is not None else 0.0
    except (TypeError, ValueError):
        spot_f = 0.0

    try:
        calculate_charges, calculate_charges_with_exit = _import_charges()
        if include_exit and spot_f > 0:
            charges = calculate_charges_with_exit(legs, spot=spot_f, broker_preset=broker_preset)
        else:
            charges = calculate_charges(legs, broker_preset=broker_preset)
    except Exception as exc:
        logger.warning("trade charges failed: %s", exc)
        return jsonify({"status": "error", "message": str(exc)}), 500

    return jsonify({"status": "success", "charges": charges})
