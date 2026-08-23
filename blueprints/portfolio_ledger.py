"""Portfolio ledger routes (fork-only): capital account, capital-at-risk
rollup, and win-rate/expectancy performance — module 9 of the
options-profitability prediction platform (see
.claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md).

Session-authed JSON endpoints, not exposed under /api/v1/ — same pattern as
strategy_chart.py/strategy_synthesis.py. Until now services/portfolio_ledger_
service.py had no live caller anywhere in the app (only its own test suite);
this is the first one, and the eventual UI dashboard's read path.
"""

import os

from flask import Blueprint, jsonify, request, session
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview
from limiter import limiter
from services.portfolio_ledger_service import (
    get_capital_account,
    get_portfolio_rollup,
    get_strategy_performance,
)
from services.sandbox_service import get_user_id_from_apikey
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

portfolio_ledger_bp = Blueprint("portfolio_ledger_bp", __name__, url_prefix="/")

PORTFOLIO_LEDGER_LIMIT = os.getenv("PORTFOLIO_LEDGER_LIMIT", "30 per minute")


def _resolve_user_id():
    """Session -> API key -> sandbox user_id, mirroring sandbox_service's own
    api_key-to-user_id resolution rather than using the login username
    directly (the ledger tables are keyed off the same user_id the sandbox
    order/fill path already uses)."""
    login_username = session["user"]
    api_key = get_api_key_for_tradingview(login_username)
    if not api_key:
        return None
    return get_user_id_from_apikey(api_key)


@portfolio_ledger_bp.route("/portfolio-ledger/api/capital-account", methods=["GET"])
@cross_origin()
@check_session_validity
@limiter.limit(PORTFOLIO_LEDGER_LIMIT)
def capital_account():
    """Capital account snapshot: total/available capital, used margin,
    realized/unrealized/total P&L."""
    try:
        user_id = _resolve_user_id()
        if not user_id:
            return jsonify({"status": "error", "message": "API key not configured"}), 401

        account = get_capital_account(user_id)
        if account is None:
            return jsonify({"status": "error", "message": "No sandbox funds found"}), 404

        return jsonify({"status": "success", **account})
    except Exception as e:
        logger.exception(f"Error in portfolio ledger capital-account endpoint: {e}")
        return jsonify({"status": "error", "message": "An unexpected error occurred"}), 500


@portfolio_ledger_bp.route("/portfolio-ledger/api/rollup", methods=["GET"])
@cross_origin()
@check_session_validity
@limiter.limit(PORTFOLIO_LEDGER_LIMIT)
def rollup():
    """Portfolio-level rollup: capital at risk, banked P&L, safe-to-withdraw."""
    try:
        user_id = _resolve_user_id()
        if not user_id:
            return jsonify({"status": "error", "message": "API key not configured"}), 401

        data = get_portfolio_rollup(user_id)
        status_code = 200 if data.get("status") == "success" else 404
        return jsonify(data), status_code
    except Exception as e:
        logger.exception(f"Error in portfolio ledger rollup endpoint: {e}")
        return jsonify({"status": "error", "message": "An unexpected error occurred"}), 500


@portfolio_ledger_bp.route("/portfolio-ledger/api/performance", methods=["GET"])
@cross_origin()
@check_session_validity
@limiter.limit(PORTFOLIO_LEDGER_LIMIT)
def performance():
    """Win-rate/expectancy stats over realized-trade-closure events, all-time
    or scoped to one strategy via ?strategy=<tag>."""
    try:
        user_id = _resolve_user_id()
        if not user_id:
            return jsonify({"status": "error", "message": "API key not configured"}), 401

        strategy = (request.args.get("strategy") or "").strip() or None
        data = get_strategy_performance(user_id, strategy=strategy)
        return jsonify(data)
    except Exception as e:
        logger.exception(f"Error in portfolio ledger performance endpoint: {e}")
        return jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
