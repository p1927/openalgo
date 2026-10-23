"""Margin estimate for simulator paper trading: the sandbox's own margin rule, nothing else.

The estimate calls `FundManager.calculate_margin_required` (sandbox/margin_rule.py documents the rule),
so what the risk gate and the nautilus preflight read is exactly what the sandbox reserves (D306).
A leg that cannot be priced fails the whole estimate with a 400, never a floor or a zero (D410).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from sandbox.fund_manager import FundManager
from sandbox.margin_rule import MarginRuleError, positive_price


def _error(message: str) -> tuple[Any, dict[str, Any]]:
    return type("Resp", (), {"status_code": 400})(), {"status": "error", "message": message}


def _leg_price(leg: dict[str, Any]) -> Decimal:
    """The leg's own price or ltp; a market leg with none uses the sandbox's quote, as an order would."""
    for key in ("price", "ltp"):
        try:
            return positive_price(leg.get(key))
        except MarginRuleError:
            continue
    from sandbox.execution_engine import ExecutionEngine

    quote = ExecutionEngine()._fetch_quote(leg.get("symbol"), leg.get("exchange"))
    return positive_price((quote or {}).get("ltp"))


def calculate_margin_api(positions: list[dict[str, Any]], auth: str) -> tuple[Any, dict[str, Any]]:
    """Total the sandbox margin of every leg (Analyzer path)."""
    del auth
    fund_manager = FundManager("margin-estimate")
    total = Decimal(0)
    premium = Decimal(0)
    for index, leg in enumerate(positions or [], 1):
        try:
            price = _leg_price(leg)
            quantity = abs(int(leg.get("quantity") or leg.get("qty") or 0))
            margin, message = fund_manager.calculate_margin_required(
                leg.get("symbol"),
                leg.get("exchange"),
                str(leg.get("product") or "MIS").upper(),
                quantity,
                price,
                str(leg.get("action") or "BUY").upper(),
            )
        except (MarginRuleError, ValueError, TypeError) as exc:
            return _error(f"Position {index}: margin cannot be priced: {exc}")
        if margin is None:
            return _error(f"Position {index}: {message}")
        total += margin
        premium += price * quantity

    value = float(round(total, 2))
    payload = {
        "status": "success",
        "data": {
            "total_margin_required": value,
            "span_margin": value,
            "exposure_margin": 0.0,
            "premium": float(premium),
            "simulated": True,
            "source": "stock_simulator",
        },
    }
    return type("Resp", (), {"status_code": 200})(), payload
