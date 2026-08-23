"""apikey-authed REST surface (fork-only) over services/portfolio_ledger_service.py,
for server-to-server callers (e.g. trade_integrations' knowledge_engine querying
strategy-scoped track record) that can't use the session-cookie blueprint in
blueprints/portfolio_ledger.py -- see
.claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md.

Sandbox-only data (StrategyClosedTrade), same as the blueprint counterpart --
not routed through live/analyze mode branching.
"""

import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from database.auth_db import verify_api_key
from limiter import limiter
from services.portfolio_ledger_service import get_strategy_performance
from utils.logging import get_logger

from .account_schema import StrategyPerformanceSchema, StrategyRiskProfileSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("strategyperformance", description="Portfolio Ledger Strategy Performance API")

logger = get_logger(__name__)

strategy_performance_schema = StrategyPerformanceSchema()
# Same two fields (apikey, optional strategy) as strategy-performance -- reused rather than
# duplicated.
strategy_legs_schema = StrategyPerformanceSchema()
strategy_risk_profile_schema = StrategyRiskProfileSchema()


@api.route("/", strict_slashes=False)
class StrategyPerformance(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Win-rate/expectancy stats over realized-trade-closure events, all-time
        or scoped to one strategy via the optional `strategy` field."""
        try:
            data = strategy_performance_schema.load(request.json)

            user_id = verify_api_key(data["apikey"])
            if not user_id:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid openalgo apikey"}), 403
                )

            result = get_strategy_performance(user_id, strategy=data["strategy"])
            return make_response(jsonify(result), 200)

        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception as e:
            logger.exception(f"Unexpected error in strategy performance endpoint: {e}")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )


@api.route("/legs", strict_slashes=False)
class StrategyLegs(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Every currently-tracked open strategy leg (symbol/exchange/product/quantity,
        tagged by strategy name), all-time or scoped to one strategy via the optional
        `strategy` field.

        This is the multi-leg position-grouping convention for module 7's execution
        advisor (see .claude/backlog/items/2026-08-22-realtime-execution-position-advisor.md
        step 1) -- reuses module 9's existing per-strategy position book
        (database.strategy_book_db.StrategyPosition) rather than adding a new
        SandboxPositions column, since it already tags every filled order by strategy
        via the same (symbol, exchange, product) key OpenAlgo's positionbook uses.
        """
        from database.strategy_book_db import StrategyBookUnavailable, get_strategy_legs

        try:
            data = strategy_legs_schema.load(request.json)

            user_id = verify_api_key(data["apikey"])
            if not user_id:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid openalgo apikey"}), 403
                )

            try:
                legs = get_strategy_legs(user_id=user_id, strategy=data["strategy"])
            except StrategyBookUnavailable:
                # Book not initialized -- degrade to "no grouping known" rather than a
                # 500, same discipline as the other portfolio-ledger endpoints when
                # their underlying store isn't ready.
                return make_response(jsonify({"status": "unavailable", "legs": []}), 200)
            return make_response(jsonify({"status": "ok", "legs": legs}), 200)

        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception as e:
            logger.exception(f"Unexpected error in strategy legs endpoint: {e}")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )


@api.route("/riskprofile", strict_slashes=False)
class StrategyRiskProfile(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Record (or replace) the max-risk/max-profit-at-entry figures for one
        strategy group.

        The write counterpart `get_portfolio_rollup` already reads from --
        `set_strategy_risk_profile` had zero production callers anywhere in
        the codebase before this (only this module's own tests called it; see
        .claude/backlog/items/2026-08-22-profit-accumulation-portfolio-ledger.md's
        2026-08-24 attempt), so the rollup's `capital_at_risk` figure has never
        reflected a real trade outside tests. Module 5's selector
        (`GET /options/india/selector`) computes exactly these numbers per
        candidate already -- this is the endpoint a caller (a UI "commit this
        trade" action, or a future execution module) would call to persist
        them once a candidate is actually acted on. Idempotent -- re-recording
        the same strategy overwrites its prior snapshot, matching
        `set_strategy_risk_profile`'s own contract.
        """
        from database.strategy_book_db import set_strategy_risk_profile

        try:
            data = strategy_risk_profile_schema.load(request.json)

            user_id = verify_api_key(data["apikey"])
            if not user_id:
                return make_response(
                    jsonify({"status": "error", "message": "Invalid openalgo apikey"}), 403
                )

            set_strategy_risk_profile(
                user_id,
                data["strategy"],
                data["max_risk"],
                max_profit=data["max_profit"],
            )
            return make_response(
                jsonify(
                    {
                        "status": "success",
                        "strategy": data["strategy"],
                        "max_risk": data["max_risk"],
                        "max_profit": data["max_profit"],
                    }
                ),
                200,
            )

        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception as e:
            logger.exception(f"Unexpected error in strategy risk profile endpoint: {e}")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )
