import os

from flask import jsonify, make_response, request
from flask_restx import Namespace, Resource

from limiter import limiter
from services.scheduler_registry_service import (
    list_scheduler_registry,
    pause_scheduler_job,
    resume_scheduler_job,
)
from utils.logging import get_logger

API_RATE_LIMIT = os.getenv("API_RATE_LIMIT", "10 per second")
api = Namespace("scheduler/registry", description="Aggregated scheduler registry API")

logger = get_logger(__name__)


@api.route("/", strict_slashes=False)
class SchedulerRegistry(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """List every job across openalgo's scheduler instances."""
        try:
            data = request.json or {}
            _ok, response_data, status_code = list_scheduler_registry(data.get("apikey"))
            return make_response(jsonify(response_data), status_code)
        except Exception:
            logger.exception("Unexpected error in scheduler registry listing endpoint")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )


@api.route("/pause", strict_slashes=False)
class SchedulerRegistryPause(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Pause a single job on one of openalgo's scheduler instances."""
        try:
            data = request.json or {}
            _ok, response_data, status_code = pause_scheduler_job(
                data.get("apikey"), data.get("source"), data.get("job_id")
            )
            return make_response(jsonify(response_data), status_code)
        except Exception:
            logger.exception("Unexpected error in scheduler registry pause endpoint")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )


@api.route("/resume", strict_slashes=False)
class SchedulerRegistryResume(Resource):
    @limiter.limit(API_RATE_LIMIT)
    def post(self):
        """Resume a single job on one of openalgo's scheduler instances."""
        try:
            data = request.json or {}
            _ok, response_data, status_code = resume_scheduler_job(
                data.get("apikey"), data.get("source"), data.get("job_id")
            )
            return make_response(jsonify(response_data), status_code)
        except Exception:
            logger.exception("Unexpected error in scheduler registry resume endpoint")
            return make_response(
                jsonify({"status": "error", "message": "An unexpected error occurred"}), 500
            )
