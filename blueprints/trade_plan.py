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
    """The hub this process declared, through the one resolver (Trade context.hub.get_hub_dir).

    A second copy of that rule here fell back to ``<root>/reports/hub`` when TRADE_STACK_HUB_DIR was
    unset, i.e. guessed the production hub, which get_hub_dir refuses to do (D306: one resolver).
    """
    from broker.stock_simulator.api._trade_path import ensure_trade_integrations_path

    ensure_trade_integrations_path()
    from trade_integrations.context.hub import get_hub_dir

    return get_hub_dir()


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
    try:
        path = _safe_plan_path(symbol, asset=asset)
    except RuntimeError as exc:  # HubDirNotDeclaredError: this process has no hub; say so, do not look elsewhere
        logger.error("trade plan hub unavailable: %s", exc)
        return jsonify({"status": "error", "message": "hub not configured for this process"}), 503
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
