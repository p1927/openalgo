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


def _trade_stack_root() -> Path:
    raw = os.getenv("TRADE_STACK_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    # openalgo/blueprints -> parents[2] = trade repo root
    return Path(__file__).resolve().parents[2]


def _hub_root() -> Path:
    raw = os.getenv("TRADE_STACK_HUB_DIR", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = _trade_stack_root() / path
        return path.resolve()
    return _trade_stack_root() / "reports" / "hub"


def _safe_plan_path(symbol: str, asset: str = "options") -> Path | None:
    key = symbol.strip().upper().replace("/", "_")
    if not key or ".." in key:
        return None
    sub = "stock_research" if asset == "stock" else "options_research"
    return _hub_root() / key / sub / "latest.json"


@trade_plan_bp.route("/api/trade-plan", methods=["GET"])
@check_session_validity
@limiter.limit(READ_LIMIT)
def get_trade_plan():
    """Load options_research/latest.json for Strategy Builder ?plan=SYMBOL."""
    symbol = request.args.get("symbol") or request.args.get("underlying") or ""
    asset = (request.args.get("asset") or "options").strip().lower()
    view = (request.args.get("view") or "full").strip().lower()
    path = _safe_plan_path(symbol, asset=asset)
    if path is None:
        return jsonify({"status": "error", "message": "invalid symbol"}), 400
    if not path.is_file():
        return jsonify({"status": "error", "message": "plan not found"}), 404
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("trade plan read failed: %s", exc)
        return jsonify({"status": "error", "message": "failed to read plan"}), 500
    if view == "browse":
        browse = payload.get("browse_summary") or {}
        return jsonify({"status": "success", "symbol": symbol.upper(), "browse_summary": browse})
    if view == "recommended":
        return jsonify(
            {
                "status": "success",
                "symbol": symbol.upper(),
                "recommended": payload.get("recommended") or {},
                "charges": payload.get("charges") or {},
                "payoff_over_time": payload.get("payoff_over_time") or {},
                "meta": payload.get("meta") or {},
            }
        )
    return jsonify({"status": "success", "plan": payload})
