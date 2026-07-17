import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource
from marshmallow import ValidationError

from limiter import limiter
from services.brokerinfo_service import get_brokerinfo
from utils.logging import get_logger

from .account_schema import BrokerInfoSchema

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("brokerinfo", description="Connected broker metadata API")

logger = get_logger(__name__)
brokerinfo_schema = BrokerInfoSchema()


@api.route("/", strict_slashes=False)
class BrokerInfo(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Return session broker, configured broker, and token sync status."""
        try:
            payload = brokerinfo_schema.load(request.json)
            api_key = payload["apikey"]
            success, response_data, status_code = get_brokerinfo(api_key=api_key)
            return make_response(jsonify(response_data), status_code)
        except ValidationError as err:
            return make_response(jsonify({"status": "error", "message": err.messages}), 400)
        except Exception:
            logger.exception("Unexpected error in brokerinfo endpoint.")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}),
                500,
            )
