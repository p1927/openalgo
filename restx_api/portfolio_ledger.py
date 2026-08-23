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

from .account_schema import StrategyPerformanceSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("strategyperformance", description="Portfolio Ledger Strategy Performance API")

logger = get_logger(__name__)

strategy_performance_schema = StrategyPerformanceSchema()


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
