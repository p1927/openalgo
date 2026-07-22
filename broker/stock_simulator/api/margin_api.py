"""SPAN-lite margin estimates for simulator paper trading."""

from __future__ import annotations

from typing import Any


def calculate_margin_api(positions: list[dict[str, Any]], auth: str) -> tuple[Any, dict[str, Any]]:
    """Estimate margin from leg premiums and lot sizes (Analyzer path)."""
    del auth
    total = 0.0
    for leg in positions or []:
        qty = abs(int(leg.get("quantity") or leg.get("qty") or 0))
        price = float(leg.get("price") or leg.get("ltp") or 0)
        product = str(leg.get("product") or "MIS").upper()
        notional = qty * price
        factor = 1.0 if product in {"MIS", "NRML"} else 0.2
        total += notional * factor

    margin = round(max(5000.0, total * 0.12), 2)
    payload = {
        "status": "success",
        "data": {
            "total_margin_required": margin,
            "span_margin": margin,
            "exposure_margin": 0.0,
            "premium": total,
            "simulated": True,
            "source": "stock_simulator",
        },
    }
    return type("Resp", (), {"status_code": 200})(), payload
