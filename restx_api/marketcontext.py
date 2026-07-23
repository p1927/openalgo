import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from services.marketcontext_service import get_marketcontext
from utils.logging import get_logger

from .account_schema import MarketContextSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("marketcontext", description="Unified market mode and broker context API")

logger = get_logger(__name__)
marketcontext_schema = MarketContextSchema()


@api.route("/", strict_slashes=False)
class MarketContext(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Return authoritative market context (broker, analyze mode, simulator)."""
        try:
            payload = marketcontext_schema.load(request.json)
            api_key = payload["apikey"]
            success, response_data, status_code = get_marketcontext(api_key=api_key)
            return make_response(jsonify(response_data), status_code)
        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception:
            logger.exception("Unexpected error in marketcontext endpoint.")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}),
                500,
            )
