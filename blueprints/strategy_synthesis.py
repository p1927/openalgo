"""Strategy synthesis route (fork-only): draw-a-target-payoff option leg search.

Split out of ``blueprints/strategy_chart.py`` (a file upstream owns) so this
fork-only surface lives in a file upstream never touches. Mounted on the
same ``/`` prefix so the route (``/strategybuilder/api/synthesize``) is
unchanged.
"""

import os

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview, get_auth_token
from limiter import limiter
from services.strategy_synthesis import synthesize_from_option_chain
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

strategy_synthesis_bp = Blueprint("strategy_synthesis_bp", __name__, url_prefix="/")

# Synthesis fetches the full option chain and runs a combinatorial search
# per request — meaningfully heavier than the strategy-chart routes, hence a
# tighter default limit.
STRATEGY_SYNTHESIS_LIMIT = os.getenv("STRATEGY_SYNTHESIS_LIMIT", "10 per minute")


@strategy_synthesis_bp.route("/strategybuilder/api/synthesize", methods=["POST"])
@cross_origin()
@check_session_validity
@limiter.limit(STRATEGY_SYNTHESIS_LIMIT)
def strategy_synthesize():
    """
    Given a user-drawn target payoff curve and a max leg count, searches
    the live option chain for the leg combination(s) that best match that
    shape (see services/strategy_synthesis/). Used by the Strategy
    Builder's "Draw Target" tab.
    """
    try:
        broker = session.get("broker")
        if not broker:
            return jsonify({"status": "error", "message": "Broker not set in session"}), 400

        login_username = session["user"]
        auth_token = get_auth_token(login_username)
        if auth_token is None:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify(
                {
                    "status": "error",
                    "message": "API key not configured. Please generate an API key in /apikey",
                }
            ), 401

        data = request.get_json(silent=True) or {}
        underlying = (data.get("underlying") or "").strip()
        exchange = (data.get("exchange") or "").strip()
        expiry_date = (data.get("expiry_date") or "").strip()
        raw_points = data.get("target_points") or []
        max_legs = data.get("max_legs")

        if not underlying or not exchange or not expiry_date:
            return jsonify(
                {"status": "error", "message": "underlying, exchange, and expiry_date are required"}
            ), 400
        if not isinstance(raw_points, list) or len(raw_points) < 2:
            return jsonify(
                {"status": "error", "message": "target_points must have at least 2 points"}
            ), 400
        try:
            target_points = [(float(p[0]), float(p[1])) for p in raw_points]
        except (TypeError, ValueError, IndexError):
            return jsonify(
                {"status": "error", "message": "target_points must be a list of [price, pnl] pairs"}
            ), 400
        try:
            max_legs = int(max_legs)
        except (TypeError, ValueError):
            return jsonify({"status": "error", "message": "max_legs must be an integer"}), 400
        if not 1 <= max_legs <= 6:
            return jsonify({"status": "error", "message": "max_legs must be between 1 and 6"}), 400

        try:
            lot_size = int(data.get("lot_size", 1))
        except (TypeError, ValueError):
            lot_size = 1

        synthesize_kwargs = {}
        target_max_profit = data.get("target_max_profit")
        target_max_loss = data.get("target_max_loss")
        if target_max_profit is not None or target_max_loss is not None:
            try:
                if target_max_profit is not None:
                    synthesize_kwargs["target_max_profit"] = float(target_max_profit)
                if target_max_loss is not None:
                    synthesize_kwargs["target_max_loss"] = float(target_max_loss)
            except (TypeError, ValueError):
                return jsonify(
                    {
                        "status": "error",
                        "message": "target_max_profit and target_max_loss must be numbers",
                    }
                ), 400
            # A rupee target only affects ranking if it's given real weight;
            # the shape axis stays primary but yields room for it. Both
            # halve so the split still sums close to 1 when a rupee target
            # is in play.
            synthesize_kwargs["rupee_weight"] = 0.25
            synthesize_kwargs["shape_weight"] = 0.15
            synthesize_kwargs["profit_weight"] = 0.20
            synthesize_kwargs["loss_weight"] = 0.15
            synthesize_kwargs["win_prob_weight"] = 0.25

        success, response, status_code = synthesize_from_option_chain(
            api_key=api_key,
            underlying=underlying,
            exchange=exchange,
            expiry_date=expiry_date,
            target_points=target_points,
            max_legs=max_legs,
            lot_size=lot_size,
            **synthesize_kwargs,
        )
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in strategy synthesis API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
