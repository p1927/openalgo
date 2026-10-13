"""Rank the Strategy Builder's smile-fit edges for the "Find the Cheese" tab (session-authed).

The browser scores every strike with the smile-fit edge (`lib/optionMispricing.ts`, the one
mispricing scorer) and posts the chain plus those edges here; Trade's options_research ranks them by
trend fit and P(ITM) and builds combos (`options_research.cheese.build_cheese_payload`).
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

trade_cheese_bp = Blueprint("trade_cheese_bp", __name__, url_prefix="/")

READ_LIMIT = os.getenv("TRADE_CHEESE_LIMIT", "60 per minute")


def _build_cheese_payload(**kwargs):
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.dataflows.options_research.cheese import build_cheese_payload

    return build_cheese_payload(**kwargs)


@trade_cheese_bp.route("/api/trade-cheese", methods=["POST"])
@check_session_validity
@limiter.limit(READ_LIMIT)
def post_trade_cheese():
    """Top BUY/SELL strikes and combos for one underlying/expiry chain and its smile-fit edges."""
    body = request.get_json(silent=True) or {}
    underlying = str(body.get("underlying") or "").strip().upper()
    chain, edges = body.get("chain"), body.get("edges")
    if not underlying or not isinstance(chain, list) or not chain or not isinstance(edges, list):
        return jsonify({"status": "error", "message": "underlying, chain[] and edges[] required"}), 400
    try:
        spot = float(body["spot"])
        dte_days = int(body["dte_days"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"status": "error", "message": "spot (number) and dte_days (integer) required"}), 400
    if not spot > 0 or dte_days < 0:
        return jsonify({"status": "error", "message": "spot must be > 0 and dte_days >= 0"}), 400

    try:
        payload = _build_cheese_payload(
            underlying=underlying,
            chain=chain,
            spot=spot,
            dte_days=dte_days,
            expiry=str(body.get("expiry") or ""),
            edges=edges,
        )
    except Exception as exc:
        logger.warning("trade cheese failed for %s: %s", underlying, exc)
        return jsonify({"status": "error", "message": str(exc)}), 502
    return jsonify({"status": "success", "cheese": payload})
