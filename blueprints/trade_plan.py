"""Serve trade-stack hub artifacts to Strategy Builder (session-authed)."""

from __future__ import annotations

import json
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from limiter import limiter
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)

trade_plan_bp = Blueprint("trade_plan_bp", __name__, url_prefix="/")

READ_LIMIT = os.getenv("TRADE_PLAN_READ_LIMIT", "60 per minute")


def _hub_root() -> Path:
    raw = os.getenv("TRADE_STACK_HUB_DIR", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # openalgo/blueprints -> parents[2] = trade repo root
    return Path(__file__).resolve().parents[2] / "reports" / "hub"


def _safe_plan_path(symbol: str) -> Path | None:
    key = symbol.strip().upper().replace("/", "_")
    if not key or ".." in key:
        return None
    return _hub_root() / key / "options_research" / "latest.json"


@trade_plan_bp.route("/api/trade-plan", methods=["GET"])
@check_session_validity
@limiter.limit(READ_LIMIT)
def get_trade_plan():
    """Load options_research/latest.json for Strategy Builder ?plan=SYMBOL."""
    symbol = request.args.get("symbol") or request.args.get("underlying") or ""
    path = _safe_plan_path(symbol)
    if path is None:
        return jsonify({"status": "error", "message": "invalid symbol"}), 400
    if not path.is_file():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("trade plan read failed: %s", exc)
        return jsonify({"status": "error", "message": "failed to read plan"}), 500
    return jsonify({"status": "success", "plan": payload})
