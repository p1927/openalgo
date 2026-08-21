"""
Strategy Chart Blueprint.

UI-only endpoint used by the Strategy Builder's Strategy Chart tab to fetch
the historical combined premium time series for the user's current leg set.
Session-authed, not exposed under /api/v1/.
"""

import os

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview, get_auth_token
from limiter import limiter
from services.intervals_service import get_intervals
from services.multi_strike_oi_service import get_multi_strike_oi_data
from services.strategy_chart_service import get_strategy_chart_data
from services.strategy_synthesis import synthesize_from_option_chain
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

strategy_chart_bp = Blueprint("strategy_chart_bp", __name__, url_prefix="/")

STRATEGY_CHART_LIMIT = os.getenv("STRATEGY_CHART_LIMIT", "30 per minute")
# Synthesis fetches the full option chain and runs a combinatorial search
# per request — meaningfully heavier than the other routes here, hence a
# tighter default limit.
STRATEGY_SYNTHESIS_LIMIT = os.getenv("STRATEGY_SYNTHESIS_LIMIT", "10 per minute")


@strategy_chart_bp.route("/strategybuilder/api/strategy-chart", methods=["POST"])
@cross_origin()
@check_session_validity
@limiter.limit(STRATEGY_CHART_LIMIT)
def strategy_chart_data():
    """Get the combined premium time series for a user-built strategy."""
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
        underlying_symbol = (data.get("underlying_symbol") or "").strip() or None
        underlying_exchange = (data.get("underlying_exchange") or "").strip() or None
        interval = (data.get("interval") or "5m").strip()
        try:
            days = int(data.get("days", 3))
        except (TypeError, ValueError):
            days = 3
        legs = data.get("legs") or []

        if not underlying or not exchange:
            return jsonify(
                {"status": "error", "message": "underlying and exchange are required"}
            ), 400
        if not isinstance(legs, list) or len(legs) == 0:
            return jsonify({"status": "error", "message": "At least one leg is required"}), 400

        success, response, status_code = get_strategy_chart_data(
            underlying=underlying,
            exchange=exchange,
            legs=legs,
            interval=interval,
            api_key=api_key,
            days=days,
            underlying_symbol=underlying_symbol,
            underlying_exchange=underlying_exchange,
        )
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in strategy chart API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@strategy_chart_bp.route("/strategybuilder/api/multi-strike-oi", methods=["POST"])
@cross_origin()
@check_session_validity
@limiter.limit(STRATEGY_CHART_LIMIT)
def multi_strike_oi_data():
    """Get per-leg OI time series alongside the underlying price."""
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
        underlying_symbol = (data.get("underlying_symbol") or "").strip() or None
        underlying_exchange = (data.get("underlying_exchange") or "").strip() or None
        interval = (data.get("interval") or "5m").strip()
        try:
            days = int(data.get("days", 3))
        except (TypeError, ValueError):
            days = 3
        legs = data.get("legs") or []

        if not underlying or not exchange:
            return jsonify(
                {"status": "error", "message": "underlying and exchange are required"}
            ), 400
        if not isinstance(legs, list) or len(legs) == 0:
            return jsonify({"status": "error", "message": "At least one leg is required"}), 400

        success, response, status_code = get_multi_strike_oi_data(
            underlying=underlying,
            exchange=exchange,
            legs=legs,
            interval=interval,
            api_key=api_key,
            days=days,
            underlying_symbol=underlying_symbol,
            underlying_exchange=underlying_exchange,
        )
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error in multi-strike OI API: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@strategy_chart_bp.route("/strategybuilder/api/synthesize", methods=["POST"])
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


@strategy_chart_bp.route("/strategybuilder/api/intervals", methods=["GET"])
@cross_origin()
@check_session_validity
def strategy_chart_intervals():
    """Proxy broker-supported intervals for the Strategy Chart tab."""
    try:
        login_username = session.get("user")
        if not login_username:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify({"status": "error", "message": "API key not configured"}), 401

        _, response, status_code = get_intervals(api_key=api_key)
        return jsonify(response), status_code

    except Exception as e:
        logger.exception(f"Error fetching intervals: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
